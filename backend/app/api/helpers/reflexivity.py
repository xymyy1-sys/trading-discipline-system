from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.models.trading import MarketRegimeSnapshot
from app.schemas.trading import MarketRegimeOut, StockDecisionCardOut
from app.services.reflexivity import (
    analyze_consensus_high_open_fade,
    analyze_market_reflexivity,
    analyze_stock_reflexivity,
)


_NON_ACTIONABLE_STOP_SOURCES = {"", "cost_reference", "fallback_candidate"}
_PREVIOUS_SNAPSHOT_MAX_AGE = timedelta(minutes=15)
_CORE_INDEX_IDENTITIES = {
    "000001", "1.000001", "上证指数", "上证综指",
    "399001", "0.399001", "深证成指",
    "399006", "0.399006", "创业板指",
}


def _pct_from_prices(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or current <= 0 or reference <= 0:
        return None
    return round((current - reference) / reference * 100, 4)


def _core_indices(regime: MarketRegimeOut) -> list[Any]:
    """Prefer an equal-weight basket of 上证、深证、创业板 over one index."""
    valid = [item for item in regime.indices if item.current and item.current > 0]
    core = [
        item for item in valid
        if item.code in _CORE_INDEX_IDENTITIES or item.name in _CORE_INDEX_IDENTITIES
    ]
    return core or valid


def _record_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _valid_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _indices_from_regime(regime: MarketRegimeOut | MarketRegimeSnapshot | None) -> list[Any]:
    if regime is None:
        return []
    indices = getattr(regime, "indices", None)
    if isinstance(indices, list):
        return indices
    raw = getattr(regime, "indices_json", "[]") or "[]"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _core_index_records(records: list[Any]) -> list[Any]:
    valid = [
        item for item in records
        if (_valid_number(_record_value(item, "current")) or 0) > 0
    ]
    core = [
        item for item in valid
        if str(_record_value(item, "code") or "") in _CORE_INDEX_IDENTITIES
        or str(_record_value(item, "name") or "") in _CORE_INDEX_IDENTITIES
    ]
    return core or valid


def _real_index_record(item: Any, *, require_realtime: bool = False) -> bool:
    quality = str(_record_value(item, "data_quality") or "").strip().lower()
    source = str(_record_value(item, "source") or "").strip().lower()
    accepted = {"realtime"} if require_realtime else {"realtime", "partial"}
    return (
        quality in accepted
        and "simulat" not in source
        and "manual" not in source
        and "mock" not in source
    )


def _latest_previous_trade_snapshot(
    db: Session,
    trade_date: str,
) -> MarketRegimeSnapshot | None:
    return (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date < trade_date)
        .order_by(
            MarketRegimeSnapshot.trade_date.desc(),
            MarketRegimeSnapshot.captured_at.desc(),
            MarketRegimeSnapshot.id.desc(),
        )
        .first()
    )


def _previous_reversal_state(
    db: Session,
    trade_date: str,
) -> tuple[bool | None, dict[str, Any]]:
    previous = _latest_previous_trade_snapshot(db, trade_date)
    if previous is None:
        return None, {"previous_trade_date": None, "valid_index_count": 0}
    captured_at = previous.captured_at
    close_ready = bool(
        captured_at is not None
        and captured_at.hour * 60 + captured_at.minute >= 14 * 60 + 55
    )
    if str(previous.data_quality or "").lower() in {"", "missing"} or not close_ready:
        return None, {
            "previous_trade_date": previous.trade_date,
            "snapshot_at": captured_at.isoformat() if captured_at is not None else None,
            "session_close_ready": close_ready,
            "valid_index_count": 0,
        }
    records = _core_index_records(_indices_from_regime(previous))
    complete: list[dict[str, float]] = []
    for item in records:
        if not _real_index_record(item):
            continue
        current = _valid_number(_record_value(item, "current"))
        low = _valid_number(_record_value(item, "low_price"))
        previous_close = _valid_number(_record_value(item, "prev_close"))
        vwap = _valid_number(_record_value(item, "intraday_vwap"))
        if not current or not low or not previous_close or not vwap:
            continue
        complete.append({
            "low_change_pct": (low - previous_close) / previous_close * 100,
            "low_rebound_pct": (current - low) / low * 100,
            "vwap_deviation_pct": (current - vwap) / vwap * 100,
        })
    details = {
        "previous_trade_date": previous.trade_date,
        "snapshot_at": captured_at.isoformat() if captured_at is not None else None,
        "session_close_ready": close_ready,
        "valid_index_count": len(complete),
        "deep_v_index_count": 0,
    }
    if len(complete) < 2:
        return None, details
    confirmed = sum(
        item["low_change_pct"] <= -1.0
        and item["low_rebound_pct"] >= 1.0
        and item["vwap_deviation_pct"] >= 0
        for item in complete
    )
    details["deep_v_index_count"] = confirmed
    return confirmed >= 2, details


def build_consensus_high_open_fade(
    db: Session,
    regime: MarketRegimeOut | MarketRegimeSnapshot | None,
    sector_opening: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate only traceable market evidence for the crowded-open rule."""

    trade_date = str(getattr(regime, "trade_date", "") or "")
    records = _core_index_records(_indices_from_regime(regime))
    previous_reversal, previous_details = (
        _previous_reversal_state(db, trade_date)
        if trade_date
        else (None, {"previous_trade_date": None, "valid_index_count": 0})
    )

    opening_values: list[float] = []
    drawdowns: list[float] = []
    vwap_deviations: list[float] = []
    for item in records:
        current = _valid_number(_record_value(item, "current"))
        open_price = _valid_number(_record_value(item, "open_price"))
        previous_close = _valid_number(_record_value(item, "prev_close"))
        high = _valid_number(_record_value(item, "high_price"))
        if _real_index_record(item) and open_price and previous_close:
            opening_values.append((open_price - previous_close) / previous_close * 100)
        if _real_index_record(item) and current and high and high > 0:
            drawdowns.append((high - current) / high * 100)
        vwap = _valid_number(_record_value(item, "intraday_vwap"))
        if _real_index_record(item, require_realtime=True) and current and vwap and vwap > 0:
            vwap_deviations.append((current - vwap) / vwap * 100)

    metrics: dict[str, Any] = {
        "previous_reversal_confirmed": previous_reversal,
        "opening_data_real": True if len(opening_values) >= 2 else None,
        "actual_open_pct": _average(opening_values) if len(opening_values) >= 2 else None,
        "post_open_drawdown_pct": _average(drawdowns) if len(drawdowns) >= 2 else None,
        "vwap_reliable": True if len(vwap_deviations) >= 2 else None,
        "vwap_deviation_pct": _average(vwap_deviations) if len(vwap_deviations) >= 2 else None,
    }

    opening = dict(sector_opening or {})
    opening_trade_date = str(opening.get("trade_date") or "")
    opening_quality = str(opening.get("data_quality") or "").lower()
    opening_sample = int(_valid_number(opening.get("sample_count")) or 0)
    if (
        trade_date
        and opening_trade_date == trade_date
        and opening_quality in {"ok", "realtime"}
        and opening_sample >= 10
    ):
        metrics.update({
            # This provider sample is the whole industry-board universe, not a
            # small constituent basket.  Feed the breadth ratio only so the
            # generic rule's "three components" shortcut cannot turn 3/80
            # high opens into a false consensus signal.
            "sector_open_breadth_ratio": opening.get("sector_open_breadth_ratio"),
        })

    result = analyze_consensus_high_open_fade(metrics)
    captured_at = getattr(regime, "captured_at", None)
    result.update({
        "as_of": captured_at,
        "trade_date": trade_date,
        "source": [
            source for source in (
                str(getattr(regime, "source", "") or ""),
                str(opening.get("source") or ""),
            ) if source
        ],
        "input_evidence": {
            "previous_reversal": previous_details,
            "current_index_count": len(records),
            "real_open_index_count": len(opening_values),
            "real_drawdown_index_count": len(drawdowns),
            "reliable_vwap_index_count": len(vwap_deviations),
            "sector_opening": {
                "trade_date": opening_trade_date or None,
                "data_quality": opening_quality or "missing",
                "sample_count": opening_sample,
                "high_open_count": opening.get("sector_high_open_count"),
                "breadth_ratio": opening.get("sector_open_breadth_ratio"),
            },
        },
    })
    return result


def _average(values: list[float | None]) -> float | None:
    valid = [float(value) for value in values if value is not None]
    return round(sum(valid) / len(valid), 4) if valid else None


def _direction_consistency(values: list[float | None]) -> float | None:
    """Share of valid indices agreeing with the basket's signed direction."""
    valid = [float(value) for value in values if value is not None]
    if len(valid) < 2:
        return None
    average = sum(valid) / len(valid)
    if abs(average) < 0.05:
        agreeing = sum(abs(value) < 0.2 for value in valid)
    elif average > 0:
        agreeing = sum(value >= 0 for value in valid)
    else:
        agreeing = sum(value <= 0 for value in valid)
    return round(agreeing / len(valid), 4)


def _previous_market_snapshot(
    db: Session,
    regime: MarketRegimeOut,
) -> MarketRegimeSnapshot | None:
    query = db.query(MarketRegimeSnapshot).filter(
        MarketRegimeSnapshot.trade_date == regime.trade_date
    )
    if regime.id is not None:
        query = query.filter(MarketRegimeSnapshot.id != regime.id)
    if regime.captured_at is not None:
        query = query.filter(MarketRegimeSnapshot.captured_at < regime.captured_at)
        query = query.filter(
            MarketRegimeSnapshot.captured_at
            >= regime.captured_at - _PREVIOUS_SNAPSHOT_MAX_AGE
        )
    return query.order_by(
        MarketRegimeSnapshot.captured_at.desc(),
        MarketRegimeSnapshot.id.desc(),
    ).first()


def market_reflexivity_metrics(
    db: Session,
    regime: MarketRegimeOut,
) -> dict[str, Any]:
    """Translate a persisted market snapshot without inventing missing evidence."""
    indices = _core_indices(regime)
    previous = _previous_market_snapshot(db, regime)
    current_flow = regime.market_main_net_inflow_yi
    previous_flow = previous.market_main_net_inflow_yi if previous is not None else None
    flow_change = (
        round(current_flow - previous_flow, 4)
        if current_flow is not None and previous_flow is not None
        else None
    )
    index_changes = [item.change_pct for item in indices]
    vwap_deviations = [
        _pct_from_prices(item.current, item.intraday_vwap)
        if item.intraday_vwap is not None
        else None
        for item in indices
    ]
    opening_changes = [
        _pct_from_prices(item.open_price, item.prev_close)
        for item in indices
    ]
    return {
        "advance_ratio": regime.advance_ratio,
        "index_change_pct": _average(index_changes),
        "index_vwap_deviation_pct": _average(vwap_deviations),
        "index_signal_count": len([value for value in index_changes if value is not None]),
        "index_signal_consistency_ratio": _direction_consistency(index_changes),
        "market_main_net_inflow_yi": current_flow,
        "main_net_inflow_change_yi": flow_change,
        "positive_sector_ratio": regime.positive_sector_ratio,
        "low_rebound_pct": _average([item.low_rebound_pct for item in indices]),
        "high_drawdown_pct": _average([item.high_drawdown_pct for item in indices]),
        "volume_ratio_5d": regime.volume_ratio_5d,
        "limit_up_count": regime.limit_up_count,
        "limit_down_count": regime.limit_down_count,
        "actual_open_pct": _average(opening_changes),
    }


def build_market_reflexivity(
    db: Session,
    regime: MarketRegimeOut,
    sector_opening: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = analyze_market_reflexivity(market_reflexivity_metrics(db, regime))
    result["as_of"] = regime.captured_at
    result["data_quality"] = regime.data_quality
    result["market_regime_code"] = regime.regime_code
    result["market_regime_name"] = regime.regime_name
    result["consensus_high_open_fade"] = build_consensus_high_open_fade(
        db,
        regime,
        sector_opening,
    )
    return result


def _has_explicit_stop_source(card: StockDecisionCardOut) -> bool:
    execution = card.execution_state
    if execution is None:
        return False
    sources = {
        part.strip()
        for part in str(execution.stop_source or "").split("+")
        if part.strip()
    }
    return any(
        source not in _NON_ACTIONABLE_STOP_SOURCES for source in sources
    )


def _explicit_hard_stop_triggered(card: StockDecisionCardOut) -> bool:
    execution = card.execution_state
    return bool(
        execution is not None
        and execution.hard_stop_price > 0
        and card.current_price > 0
        and _has_explicit_stop_source(card)
        and card.current_price <= execution.hard_stop_price
    )


def stock_reflexivity_metrics(card: StockDecisionCardOut) -> dict[str, Any]:
    expectation = card.expectation
    volume = card.volume_price
    volume_usable = volume is not None and volume.data_quality not in {"", "missing"}
    price = (
        volume.price
        if volume_usable and volume is not None and volume.price > 0
        else card.current_price if card.current_price > 0 else None
    )
    vwap_deviation = None
    if (
        volume_usable
        and volume is not None
        and volume.vwap_reliable
        and volume.vwap > 0
        and price is not None
    ):
        vwap_deviation = _pct_from_prices(price, volume.vwap)

    low_rebound = None
    high_drawdown = None
    volume_ratio = None
    if volume_usable and volume is not None:
        if price is not None and volume.low_price > 0:
            low_rebound = _pct_from_prices(price, volume.low_price)
        if price is not None and volume.high_price > 0:
            high_drawdown = round((volume.high_price - price) / volume.high_price * 100, 4)
        if volume.volume_ratio > 0:
            volume_ratio = volume.volume_ratio

    support_distance = None
    execution = card.execution_state
    if (
        execution is not None
        and _has_explicit_stop_source(card)
        and execution.structure_stop_price > 0
        and card.current_price > 0
    ):
        support_distance = _pct_from_prices(
            card.current_price, execution.structure_stop_price
        )

    return {
        "code": card.code,
        "name": card.name,
        "expectation_gap_score": expectation.expectation_gap_score,
        "actual_open_pct": expectation.actual_open_pct,
        "expected_open_low": expectation.expected_open_low,
        "expected_open_high": expectation.expected_open_high,
        "vwap_deviation_pct": vwap_deviation,
        "change_pct": card.change_pct if price is not None else None,
        "low_rebound_pct": low_rebound,
        "high_drawdown_pct": high_drawdown,
        "volume_ratio": volume_ratio,
        # The decision card currently has no numeric sector-relative series.
        # Leave it absent so the scenario engine reports the evidence gap.
        "sector_relative_strength_pct": None,
        "support_distance_pct": support_distance,
        "hard_stop_triggered": _explicit_hard_stop_triggered(card),
    }


def build_stock_reflexivity(
    card: StockDecisionCardOut,
    market_assessment: dict[str, Any],
    regime: MarketRegimeOut,
) -> dict[str, Any]:
    context = dict(market_assessment)
    risk_regimes = {
        "EXTREME_SHRINK_DECLINE", "VOLUME_SELL_OFF", "SHRINK_ROTATION", "UNKNOWN",
    }
    if regime.regime_code in risk_regimes and context.get("current_scenario") not in {
        "NO_REBOUND_LIQUIDATION",
        "REBOUND_FAILURE_SUPPLY",
    }:
        # The behavioral path and the objective market gate are both real.  For
        # stock execution, the more conservative objective risk gate prevails.
        context["current_scenario"] = regime.regime_code
    result = analyze_stock_reflexivity(stock_reflexivity_metrics(card), context)
    result["as_of"] = (
        card.volume_price.captured_at
        if card.volume_price is not None
        else card.execution_state.updated_at
        if card.execution_state is not None
        else card.expectation.created_at
    )
    result["data_quality"] = card.data_quality
    result["market_regime_code"] = regime.regime_code
    result["market_regime_name"] = regime.regime_name
    return result
