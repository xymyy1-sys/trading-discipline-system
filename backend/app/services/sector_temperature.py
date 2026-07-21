from __future__ import annotations

from datetime import datetime, timezone, timedelta
import math
from typing import Any, Iterable, Mapping


_SHANGHAI_TZ = timezone(timedelta(hours=8))


def _value(row: Any, *names: str, default: Any = None) -> Any:
    if row is None:
        return default
    for name in names:
        if isinstance(row, Mapping):
            if name in row and row[name] is not None:
                return row[name]
        else:
            value = getattr(row, name, None)
            if value is not None:
                return value
    return default


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    return int(number) if number is not None else None


def _optional_bool(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "否"}:
        return False
    return None


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _scaled(value: float | None, low: float, high: float) -> float | None:
    if value is None:
        return None
    if high <= low:
        return 50.0
    return _clamp((value - low) * 100.0 / (high - low))


def _smooth_flow_score(value: float | None, scale: float) -> float | None:
    if value is None:
        return None
    return _clamp(50.0 + 50.0 * math.tanh(value / max(scale, 1e-6)))


def _weighted(parts: Iterable[tuple[float | None, float]], default: float = 50.0) -> float:
    present = [(value, weight) for value, weight in parts if value is not None and weight > 0]
    if not present:
        return default
    denominator = sum(weight for _, weight in present)
    return sum(float(value) * weight for value, weight in present) / denominator


def _round_optional(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def _row_name(row: Any) -> str:
    return str(_value(row, "name", "display_name", "raw_name", default="") or "").strip()


def _aliases(row: Any) -> set[str]:
    values = {
        str(_value(row, key, default="") or "").strip()
        for key in ("name", "display_name", "raw_name")
    }
    return {value for value in values if value}


def _index_rows(rows: Iterable[Any] | None) -> tuple[dict[str, Any], list[Any]]:
    index: dict[str, Any] = {}
    ordered: list[Any] = []
    for row in rows or []:
        name = _row_name(row)
        if not name:
            continue
        ordered.append(row)
        for alias in _aliases(row):
            index.setdefault(alias, row)
    return index, ordered


def _lookup(index: Mapping[str, Any], row: Any) -> Any:
    for alias in _aliases(row):
        if alias in index:
            return index[alias]
    return None


def _mapping_lookup(values: Mapping[str, Any] | None, row: Any) -> Any:
    if not values:
        return None
    for alias in _aliases(row):
        if alias in values:
            return values[alias]
    return None


def _turning_direction(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return ""
    # These are marginal directions, so substring matching is unsafe:
    # ``INFLOW_FADING`` is deterioration while ``OUTFLOW_NARROWING`` is
    # improvement.  Resolve the provider vocabulary before generic aliases.
    explicit = {
        "TURN_TO_INFLOW": "up",
        "OUTFLOW_NARROWING": "up",
        "INFLOW_ACCELERATING": "up",
        "FLOW_IMPROVING": "up",
        "TURN_TO_OUTFLOW": "down",
        "INFLOW_FADING": "down",
        "OUTFLOW_ACCELERATING": "down",
        "FLOW_WEAKENING": "down",
    }
    if normalized in explicit:
        return explicit[normalized]
    if any(token in normalized for token in ("UP", "INFLOW", "REBOUND", "POSITIVE", "向上", "流入", "转强", "回流")):
        return "up"
    if any(token in normalized for token in ("DOWN", "OUTFLOW", "NEGATIVE", "向下", "流出", "转弱", "退潮")):
        return "down"
    return ""


def _change(row: Any) -> float | None:
    return _optional_float(_value(row, "change_pct", "pct_change", "change"))


def _net(row: Any) -> float | None:
    return _optional_float(_value(row, "net_inflow", "main_inflow", "net"))


def _flow_score(
    current_net: float | None,
    net_5d: float | None,
    net_10d: float | None,
    speed: float | None,
    acceleration: float | None,
    turning: str,
) -> float:
    score = _weighted(
        (
            (_smooth_flow_score(current_net, 35.0), 0.28),
            (_smooth_flow_score(net_5d, 120.0), 0.27),
            (_smooth_flow_score(net_10d, 220.0), 0.20),
            (_smooth_flow_score(speed, 1.2), 0.15),
            (_smooth_flow_score(acceleration, 0.12), 0.10),
        )
    )
    if turning == "up":
        score += 8
    elif turning == "down":
        score -= 8
    return _clamp(score)


def _trend_score(change: float | None, change_5d: float | None, change_10d: float | None) -> float:
    return _weighted(
        (
            (_scaled(change, -5.0, 6.0), 0.25),
            (_scaled(change_5d, -12.0, 18.0), 0.35),
            (_scaled(change_10d, -18.0, 30.0), 0.40),
        )
    )


def _crowding_score(
    change: float | None,
    change_5d: float | None,
    change_10d: float | None,
    limit_up_count: int | None,
) -> float | None:
    parts = [
        (_scaled(change, -2.0, 7.0), 0.15),
        (_scaled(change_5d, -3.0, 16.0), 0.35),
        (_scaled(change_10d, -5.0, 26.0), 0.35),
        (_scaled(float(limit_up_count), 0.0, 12.0) if limit_up_count is not None else None, 0.15),
    ]
    if not any(value is not None for value, _ in parts):
        return None
    return _weighted(parts)


def _attention_score(attention: Any) -> float | None:
    if attention is None:
        return None
    explicit = _optional_float(_value(attention, "score", "attention_score", "heat_score"))
    if explicit is not None:
        return _clamp(explicit)
    rank = _optional_int(_value(attention, "rank", "attention_rank"))
    if rank is None:
        return None
    return _clamp(100.0 - max(0, rank - 1) * 3.0)


def _margin_fields(margin: Any) -> dict[str, Any]:
    if margin is None:
        return {
            "financing_balance": None,
            "financing_buy": None,
            "financing_reference_turnover": None,
            "financing_turnover_as_of": "",
            "financing_net_buy": None,
            "financing_balance_ratio": None,
            "financing_net_buy_5d": None,
            "financing_net_buy_10d": None,
            "financing_net_buy_20d": None,
            "financing_net_buy_slope_5d": None,
            "financing_net_buy_slope_10d": None,
            "financing_net_buy_slope_20d": None,
            "financing_balance_ratio_percentile_60d": None,
            "financing_balance_ratio_percentile_120d": None,
            "margin_history_sample_count": 0,
            "margin_history_degraded": True,
            "margin_history_sequence_complete": False,
            "margin_history_method": "",
            "margin_as_of": "",
            "margin_realtime": False,
            "margin_score": None,
        }
    balance = _optional_float(_value(margin, "financing_balance"))
    financing_buy = _optional_float(_value(margin, "financing_buy"))
    financing_reference_turnover = _optional_float(_value(
        margin,
        "financing_reference_turnover",
        "reference_turnover_amount",
    ))
    financing_turnover_as_of = str(_value(
        margin,
        "financing_turnover_as_of",
        "reference_turnover_as_of",
        default="",
    ) or "")[:10]
    net_buy = _optional_float(_value(margin, "financing_net_buy"))
    balance_ratio = _optional_float(_value(margin, "financing_balance_ratio"))
    net_buy_5d = _optional_float(_value(margin, "net_buy_5d", "financing_net_buy_5d"))
    net_buy_10d = _optional_float(_value(margin, "net_buy_10d", "financing_net_buy_10d"))
    net_buy_20d = _optional_float(_value(margin, "net_buy_20d", "financing_net_buy_20d"))
    slope_5d = _optional_float(_value(margin, "financing_net_buy_slope_5d"))
    slope_10d = _optional_float(_value(margin, "financing_net_buy_slope_10d"))
    slope_20d = _optional_float(_value(margin, "financing_net_buy_slope_20d"))
    percentile_60d = _optional_float(_value(margin, "financing_balance_ratio_percentile_60d"))
    percentile_120d = _optional_float(_value(margin, "financing_balance_ratio_percentile_120d"))
    history_sample_count = _optional_int(_value(margin, "margin_history_sample_count", default=0)) or 0
    history_degraded = bool(_optional_bool(_value(
        margin,
        "margin_history_degraded",
        default=True,
    )))
    history_sequence_complete = bool(_optional_bool(_value(
        margin,
        "margin_history_sequence_complete",
        default=False,
    )))
    margin_score = _weighted(
        (
            (_scaled(percentile_120d, 0.0, 100.0), 0.30),
            (_scaled(percentile_60d, 0.0, 100.0), 0.20),
            (_scaled(balance_ratio, 0.0, 10.0), 0.15),
            (_smooth_flow_score(slope_5d, 1.0), 0.15),
            (_smooth_flow_score(slope_10d, 0.6), 0.10),
            (_smooth_flow_score(slope_20d, 0.3), 0.10),
        )
    ) if any(value is not None for value in (
        balance_ratio,
        percentile_60d,
        percentile_120d,
        slope_5d,
        slope_10d,
        slope_20d,
    )) else None
    return {
        "financing_balance": _round_optional(balance),
        "financing_buy": _round_optional(financing_buy),
        "financing_reference_turnover": _round_optional(financing_reference_turnover, 4),
        "financing_turnover_as_of": financing_turnover_as_of,
        "financing_net_buy": _round_optional(net_buy),
        "financing_balance_ratio": _round_optional(balance_ratio, 3),
        "financing_net_buy_5d": _round_optional(net_buy_5d),
        "financing_net_buy_10d": _round_optional(net_buy_10d),
        "financing_net_buy_20d": _round_optional(net_buy_20d),
        "financing_net_buy_slope_5d": _round_optional(slope_5d, 4),
        "financing_net_buy_slope_10d": _round_optional(slope_10d, 4),
        "financing_net_buy_slope_20d": _round_optional(slope_20d, 4),
        "financing_balance_ratio_percentile_60d": _round_optional(percentile_60d),
        "financing_balance_ratio_percentile_120d": _round_optional(percentile_120d),
        "margin_history_sample_count": history_sample_count,
        "margin_history_degraded": history_degraded,
        "margin_history_sequence_complete": history_sequence_complete,
        "margin_history_method": str(_value(margin, "margin_history_method", default="") or ""),
        "margin_as_of": str(_value(margin, "as_of", "trade_date", default="") or "")[:10],
        # Even if an upstream field incorrectly says true, this public disclosure
        # must never be presented by this model as an intraday signal.
        "margin_realtime": False,
        "margin_score": _round_optional(margin_score),
    }


def _structure_fields(current: Any, change: float | None) -> dict[str, Any]:
    turnover_amount = _optional_float(_value(
        current,
        "turnover_amount",
        "board_turnover_amount",
        "amount",
    ))
    leader_change = _optional_float(_value(
        current,
        "leader_change_pct",
        "leading_stock_change_pct",
    ))
    leader_divergence = (
        leader_change - change
        if leader_change is not None and change is not None
        else None
    )
    up_count = _optional_int(_value(current, "up_count", "advance_count"))
    down_count = _optional_int(_value(current, "down_count", "decline_count"))
    flat_count = _optional_int(_value(current, "flat_count", default=0))
    declared_stock_count = _optional_int(_value(current, "stock_count", "constituent_count"))
    observed_count = (
        up_count + down_count + (flat_count or 0)
        if up_count is not None and down_count is not None
        else None
    )
    stock_count = declared_stock_count or observed_count
    advance_ratio = (
        up_count / observed_count * 100
        if up_count is not None and observed_count is not None and observed_count > 0
        else None
    )
    new_high_count = _optional_int(_value(
        current,
        "new_high_count",
        "high_20d_count",
        "constituent_new_high_count",
    ))
    new_high_ratio = (
        new_high_count / stock_count * 100
        if new_high_count is not None and stock_count is not None and stock_count > 0
        else None
    )
    promotion_rate = _optional_float(_value(
        current,
        "promotion_rate",
        "limit_up_promotion_rate",
    ))
    break_rate = _optional_float(_value(
        current,
        "break_rate",
        "limit_up_break_rate",
        "broken_board_rate",
    ))
    sector_price = _optional_float(_value(current, "sector_price", "latest"))
    sector_vwap = _optional_float(_value(current, "sector_vwap", "vwap"))
    vwap_reliable = bool(_optional_bool(_value(current, "sector_vwap_reliable", "vwap_reliable")))
    below_vwap = (
        sector_price < sector_vwap
        if vwap_reliable and sector_price is not None and sector_vwap is not None and sector_vwap > 0
        else None
    )
    return {
        "sector_turnover_amount": _round_optional(turnover_amount, 4),
        "sector_turnover_complete": bool(_optional_bool(_value(
            current,
            "turnover_complete",
            "sector_turnover_complete",
            "session_complete",
            default=False,
        ))),
        "leader_change_pct": _round_optional(leader_change),
        "leader_divergence_pct": _round_optional(leader_divergence),
        "advance_count": up_count,
        "decline_count": down_count,
        "constituent_count": stock_count,
        "advance_ratio": _round_optional(advance_ratio),
        "new_high_count": new_high_count,
        "new_high_ratio": _round_optional(new_high_ratio),
        "promotion_rate": _round_optional(promotion_rate),
        "break_rate": _round_optional(break_rate),
        "sector_price": _round_optional(sector_price, 4),
        "sector_vwap": _round_optional(sector_vwap, 4),
        "sector_vwap_reliable": vwap_reliable,
        "sector_below_vwap": below_vwap,
    }


def _financing_turnover_metrics(
    margin_fields: Mapping[str, Any],
    structure_fields: Mapping[str, Any],
    provider_trade_date: str,
) -> tuple[float | None, bool, str]:
    """Return a descriptive, same-date financing-buy/turnover ratio.

    This metric is independent from intraday freshness.  A stale current board
    quote must not erase a valid archived T+1 ratio whose numerator and
    denominator are both from the same completed disclosure date.
    """

    financing_buy = _optional_float(margin_fields.get("financing_buy"))
    current_turnover_amount = _optional_float(
        structure_fields.get("sector_turnover_amount")
    )
    margin_as_of = str(margin_fields.get("margin_as_of") or "")[:10]
    reference_turnover_amount = _optional_float(
        margin_fields.get("financing_reference_turnover")
    )
    reference_turnover_as_of = str(
        margin_fields.get("financing_turnover_as_of") or ""
    )[:10]
    if reference_turnover_amount is not None:
        turnover_amount = reference_turnover_amount
        turnover_as_of = reference_turnover_as_of
    else:
        turnover_amount = (
            current_turnover_amount
            if bool(structure_fields.get("sector_turnover_complete"))
            else None
        )
        turnover_as_of = provider_trade_date
    aligned = bool(
        financing_buy is not None
        and turnover_amount is not None
        and turnover_amount > 0
        and margin_as_of
        and turnover_as_of
        and margin_as_of == turnover_as_of
    )
    ratio = financing_buy / turnover_amount * 100 if aligned else None
    return _round_optional(ratio), aligned, turnover_as_of


def _persistence_fields(persistence: Any) -> dict[str, Any]:
    if persistence is None:
        return {
            "strict_state": "",
            "confirmed_state": "",
            "persistence_state": "",
            "sample_confirmation_count": 0,
            "trading_day_confirmation_count": 0,
            "persistence_confirmed": False,
            "persistence_basis": [],
            "last_sample_at": None,
            "last_trade_date": None,
            "recent_state_samples": [],
            "sample_confirmation_min_interval_seconds": 300,
            "capital_price_carrying_efficiency": None,
            "capital_price_carrying_sample_count": 0,
            "capital_price_carrying_span_minutes": None,
            "capital_price_carrying_slope": None,
            "capital_price_carrying_method": "immutable_intraday_delta_rolling",
        }
    basis = _value(persistence, "confirmation_basis", "persistence_basis", default=[])
    if not isinstance(basis, list):
        basis = []
    sample_count = _optional_int(_value(
        persistence,
        "sample_confirmation_count",
        "sample_count",
        default=0,
    )) or 0
    day_count = _optional_int(_value(
        persistence,
        "trading_day_confirmation_count",
        "trading_day_count",
        default=0,
    )) or 0
    confirmed = bool(
        _optional_bool(_value(persistence, "persistence_confirmed"))
        or sample_count >= 2
        or day_count >= 2
    )
    strict_state = str(_value(
        persistence,
        "strict_state",
        "distribution_state",
        "state",
        default="",
    ) or "")
    recent_samples = _value(
        persistence,
        "samples",
        "recent_samples",
        "recent_state_samples",
        default=[],
    )
    if not isinstance(recent_samples, list):
        recent_samples = []
    return {
        "strict_state": strict_state,
        "confirmed_state": strict_state if confirmed else "",
        "persistence_state": strict_state,
        "sample_confirmation_count": sample_count,
        "trading_day_confirmation_count": day_count,
        "persistence_confirmed": confirmed,
        "persistence_basis": [str(item) for item in basis if str(item).strip()],
        "last_sample_at": _value(persistence, "data_as_of", "last_sample_at"),
        "last_trade_date": str(_value(persistence, "last_trade_date", default="") or "")[:10] or None,
        "recent_state_samples": recent_samples[-8:],
        "sample_confirmation_min_interval_seconds": _optional_int(_value(
            persistence,
            "sample_confirmation_min_interval_seconds",
            default=300,
        )) or 300,
        "capital_price_carrying_efficiency": _round_optional(_optional_float(_value(
            persistence,
            "capital_price_carrying_efficiency",
        ))),
        "capital_price_carrying_sample_count": _optional_int(_value(
            persistence,
            "capital_price_carrying_sample_count",
            default=0,
        )) or 0,
        "capital_price_carrying_span_minutes": _round_optional(_optional_float(_value(
            persistence,
            "capital_price_carrying_span_minutes",
        ))),
        "capital_price_carrying_slope": _round_optional(_optional_float(_value(
            persistence,
            "capital_price_carrying_slope",
        )), 4),
        "capital_price_carrying_method": str(_value(
            persistence,
            "capital_price_carrying_method",
            default="immutable_intraday_delta_rolling",
        ) or "immutable_intraday_delta_rolling"),
    }


def _distribution_assessment(
    *,
    change: float | None,
    change_5d: float | None,
    change_10d: float | None,
    current_net: float | None,
    net_5d: float | None,
    net_10d: float | None,
    speed: float | None,
    acceleration: float | None,
    turning: str,
    flow_ratio: float | None,
    flow_ratio_5d: float | None,
    flow_ratio_10d: float | None,
    structure_fields: Mapping[str, Any],
    margin_fields: Mapping[str, Any],
    persistence_fields: Mapping[str, Any],
    provider_trade_date: str,
    provider_updated_at: str,
    non_leveraged_net_inflow: float | None,
    non_leveraged_flow_audited: bool,
    etf_share_net_change: float | None,
    etf_share_change_pct: float | None,
    etf_flow_audited: bool,
    data_quality: str,
) -> dict[str, Any]:
    """Assess cash-flow carrying capacity versus the T+1 leverage slow variable.

    The confirmation count deliberately counts *independent evidence families*.
    The overlapping financing windows are one leverage family, so public T+1
    margin data can never, by itself, produce a high-risk state or a trade
    instruction.
    """

    price_window_count = sum(value is not None for value in (change, change_5d, change_10d))
    flow_window_count = sum(value is not None for value in (current_net, net_5d, net_10d))
    evidence: list[str] = []
    counter_evidence: list[str] = []
    (
        financing_buy_turnover_ratio,
        financing_turnover_date_aligned,
        turnover_as_of,
    ) = _financing_turnover_metrics(
        margin_fields,
        structure_fields,
        provider_trade_date,
    )
    carrying_efficiency = _optional_float(
        persistence_fields.get("capital_price_carrying_efficiency")
    )
    carrying_sample_count = int(
        _optional_int(persistence_fields.get("capital_price_carrying_sample_count"))
        or 0
    )
    carrying_span_minutes = _optional_float(
        persistence_fields.get("capital_price_carrying_span_minutes")
    )
    carrying_slope = _optional_float(
        persistence_fields.get("capital_price_carrying_slope")
    )

    if price_window_count < 2 or flow_window_count < 2 or data_quality in {"missing", "limited", "stale"}:
        if price_window_count < 2:
            counter_evidence.append("价格窗口少于2个，无法确认价格所处位置与响应强弱。")
        if flow_window_count < 2:
            counter_evidence.append("订单流方向窗口少于2个，无法确认资金承载是否衰减。")
        if data_quality == "stale":
            counter_evidence.append("当日板块快照已过期，不生成当前派发或踩踏结论。")
        return {
            "distribution_state": "数据不足",
            "instantaneous_distribution_state": "数据不足",
            "distribution_risk_level": "UNKNOWN",
            "distribution_risk_score": 0,
            "order_flow_exhausted": False,
            "leverage_crowding": False,
            "price_response_weak": False,
            "distribution_confirmation_count": 0,
            "capital_price_carrying_efficiency": carrying_efficiency,
            "capital_price_carrying_sample_count": carrying_sample_count,
            "capital_price_carrying_span_minutes": carrying_span_minutes,
            "capital_price_carrying_slope": carrying_slope,
            "financing_buy_turnover_ratio": financing_buy_turnover_ratio,
            "financing_turnover_date_aligned": financing_turnover_date_aligned,
            "non_leveraged_net_inflow": (
                _round_optional(non_leveraged_net_inflow)
                if non_leveraged_flow_audited else None
            ),
            "non_leveraged_flow_audited": non_leveraged_flow_audited,
            "etf_share_net_change": (
                _round_optional(etf_share_net_change)
                if etf_flow_audited else None
            ),
            "etf_share_change_pct": (
                _round_optional(etf_share_change_pct, 4)
                if etf_flow_audited else None
            ),
            "etf_flow_audited": etf_flow_audited,
            **dict(persistence_fields),
            "strict_state": "数据不足",
            "distribution_evidence": evidence,
            "distribution_counter_evidence": counter_evidence,
            "distribution_actions": ["补齐至少2个价格与订单流方向窗口后再判断，不据此交易。"],
        }

    # "High" means consistent short-window extension, not merely one rebound
    # window.  A 5-day rebound from a deeply oversold 10-day base must not be
    # called high-position distribution.
    high_price_location = bool(
        (
            change_5d is not None
            and change_5d >= 8.0
            and (change_10d is None or change_10d >= 8.0)
        )
        or (
            change_10d is not None
            and change_10d >= 15.0
            and (change_5d is None or change_5d >= 3.0)
        )
    )

    # Board flows are expressed in 亿元, but board size varies materially.  A
    # one-size absolute threshold would suppress smaller boards while a sign-
    # only rule turns rounding dust into a signal.  Use a 1亿元/day absolute
    # floor, plus a relative branch that still requires at least 0.25亿元/day
    # and 35% of the board's recent daily-equivalent flow.
    daily_5 = abs(net_5d) / 5.0 if net_5d is not None else None
    daily_10 = abs(net_10d) / 10.0 if net_10d is not None else None
    reference_daily_flow = max(
        (value for value in (daily_5, daily_10) if value is not None),
        default=0.0,
    )

    def material_flow(value: float | None, window_days: int) -> bool:
        if value is None:
            return False
        daily_equivalent = abs(value) / max(1, window_days)
        return bool(
            daily_equivalent >= 1.0
            or (
                reference_daily_flow >= 1.0
                and daily_equivalent >= 0.25
                and daily_equivalent >= reference_daily_flow * 0.35
            )
        )

    current_flow_material = material_flow(current_net, 1)
    net_5d_material = material_flow(net_5d, 5)
    net_10d_material = material_flow(net_10d, 10)
    recent_flow_positive = bool(
        (net_5d is not None and net_5d > 0 and net_5d_material)
        or (net_10d is not None and net_10d > 0 and net_10d_material)
    )
    turning_down = turning == "down"
    velocity_down = bool(
        speed is not None
        and speed <= -0.05
        and (acceleration is None or acceleration <= 0)
        and (current_flow_material or recent_flow_positive)
    )
    flow_drop_from_baseline = bool(
        recent_flow_positive
        and current_net is not None
        and reference_daily_flow - current_net
        >= max(1.0, reference_daily_flow * 0.35)
    )
    flow_rollover = bool(
        flow_drop_from_baseline
        and current_net is not None
        and current_net <= 0
    )
    flow_fading = bool(flow_drop_from_baseline and (turning_down or velocity_down))
    material_current_outflow = bool(
        current_net is not None
        and current_net < 0
        and current_flow_material
        and (turning_down or velocity_down)
    )
    order_flow_exhausted = bool(flow_rollover or flow_fading or material_current_outflow)

    weak_response_signals = [
        current_net is not None
        and current_net > 0
        and current_flow_material
        and change is not None
        and change <= 0.3,
        net_5d is not None
        and net_5d > 0
        and net_5d_material
        and change_5d is not None
        and change_5d <= 1.0,
        net_10d is not None
        and net_10d > 0
        and net_10d_material
        and change_10d is not None
        and change_10d <= 2.0,
        flow_rollover and change is not None and change <= -0.8,
    ]
    weak_response_count = sum(bool(value) for value in weak_response_signals)
    price_response_weak = bool(
        weak_response_count > 0
        or (
            carrying_efficiency is not None
            and carrying_sample_count > 0
            and carrying_efficiency <= 40
        )
    )

    financing_buy = _optional_float(margin_fields.get("financing_buy"))
    margin_as_of = str(margin_fields.get("margin_as_of") or "")[:10]

    financing_values = [
        _optional_float(margin_fields.get("financing_net_buy")),
        _optional_float(margin_fields.get("financing_net_buy_5d")),
        _optional_float(margin_fields.get("financing_net_buy_10d")),
        _optional_float(margin_fields.get("financing_net_buy_20d")),
    ]
    financing_present = [value for value in financing_values if value is not None]
    positive_financing_count = sum(value > 0 for value in financing_present)
    negative_financing_count = sum(value < 0 for value in financing_present)
    financing_ratio = _optional_float(margin_fields.get("financing_balance_ratio"))
    financing_slopes = [
        _optional_float(margin_fields.get("financing_net_buy_slope_5d")),
        _optional_float(margin_fields.get("financing_net_buy_slope_10d")),
        _optional_float(margin_fields.get("financing_net_buy_slope_20d")),
    ]
    slope_present = [value for value in financing_slopes if value is not None]
    positive_slope_count = sum(value > 0 for value in slope_present)
    negative_slope_count = sum(value < 0 for value in slope_present)
    percentile_60d = _optional_float(
        margin_fields.get("financing_balance_ratio_percentile_60d")
    )
    percentile_120d = _optional_float(
        margin_fields.get("financing_balance_ratio_percentile_120d")
    )
    leverage_data_count = (
        len(financing_present)
        + len(slope_present)
        + int(financing_ratio is not None)
        + int(percentile_60d is not None)
        + int(percentile_120d is not None)
        + int(financing_buy_turnover_ratio is not None)
    )
    leverage_crowding = bool(
        leverage_data_count >= 2
        and (
            (percentile_120d is not None and percentile_120d >= 85)
            or (percentile_60d is not None and percentile_60d >= 90)
            or (
                financing_ratio is not None
                and financing_ratio >= 8.0
                and (positive_slope_count >= 1 or positive_financing_count >= 2)
            )
            or (
                financing_buy_turnover_ratio is not None
                and financing_buy_turnover_ratio >= 12
                and positive_slope_count >= 1
            )
            or positive_financing_count >= 3
        )
    )
    deleveraging = bool(
        financing_values[0] is not None
        and financing_values[0] < 0
        and (
            negative_slope_count >= 2
            or (len(financing_present) >= 2 and negative_financing_count >= 2)
        )
    )
    negative_price = bool(
        (change is not None and change <= -1.0)
        or (change_5d is not None and change_5d <= -3.0)
        or (change_10d is not None and change_10d <= -5.0)
    )
    negative_order_flow = bool(
        current_net is not None
        and current_net < 0
        and (
            turning_down
            or (speed is not None and speed < 0)
            or (net_5d is not None and net_5d < 0)
            or (net_10d is not None and net_10d < 0)
        )
    )

    leader_divergence = _optional_float(structure_fields.get("leader_divergence_pct"))
    advance_ratio = _optional_float(structure_fields.get("advance_ratio"))
    new_high_ratio = _optional_float(structure_fields.get("new_high_ratio"))
    promotion_rate = _optional_float(structure_fields.get("promotion_rate"))
    break_rate = _optional_float(structure_fields.get("break_rate"))
    below_vwap = _optional_bool(structure_fields.get("sector_below_vwap"))
    structure_signals = {
        "龙头弱于板块": leader_divergence is not None and leader_divergence <= -2.0,
        "上涨广度收缩": advance_ratio is not None and advance_ratio <= 35,
        "创新高家数不足": new_high_ratio is not None and new_high_ratio <= 5,
        "涨停晋级率偏低": promotion_rate is not None and promotion_rate <= 12,
        "炸板率偏高": break_rate is not None and break_rate >= 45,
        "板块跌破真实VWAP": below_vwap is True,
    }
    weak_structure_labels = [label for label, matched in structure_signals.items() if matched]
    structure_weak = bool(weak_structure_labels)

    historically_oversold = bool(
        (change_5d is not None and change_5d <= -5.0)
        or (change_10d is not None and change_10d <= -8.0)
    )
    stabilization_families = {
        "订单流向上改善": turning == "up" or bool(
            speed is not None and speed > 0 and (acceleration is None or acceleration >= 0)
        ),
        "价格止跌": change is not None and change >= 0,
        "板块站上真实VWAP": below_vwap is False,
        "上涨广度恢复": advance_ratio is not None and advance_ratio >= 55,
    }
    stabilization_labels = [
        label for label, matched in stabilization_families.items() if matched
    ]
    oversold_stabilizing = historically_oversold and len(stabilization_labels) >= 2

    if high_price_location:
        location_parts = []
        if change_5d is not None:
            location_parts.append(f"近5日{change_5d:+.2f}%")
        if change_10d is not None:
            location_parts.append(f"近10日{change_10d:+.2f}%")
        evidence.append(f"价格处于阶段高位（{'、'.join(location_parts)}）。")
    if order_flow_exhausted:
        reasons = []
        if turning_down:
            reasons.append("方向向下拐头")
        if velocity_down:
            reasons.append("流速与加速度走弱")
        if flow_rollover:
            reasons.append("历史净流入后当日转为非流入")
        evidence.append(f"订单流方向出现衰竭迹象（{'、'.join(reasons)}）。")
    if price_response_weak:
        suffix = (
            f"，连续型承载效率{carrying_efficiency:.1f}/100"
            if carrying_efficiency is not None else ""
        )
        evidence.append(
            f"价格对正向订单流的响应偏弱（命中{weak_response_count}个跨窗口条件{suffix}）。"
        )
    elif carrying_efficiency is not None:
        evidence.append(
            f"资金—价格连续承载效率{carrying_efficiency:.1f}/100"
            f"（不可变盘中序列{carrying_sample_count}个有效转移"
            f"，跨度{carrying_span_minutes:.0f}分钟"
            f"，近端斜率{carrying_slope:+.2f}）"
            if carrying_span_minutes is not None and carrying_slope is not None
            else f"资金—价格连续承载效率{carrying_efficiency:.1f}/100"
            f"（不可变盘中序列{carrying_sample_count}个有效转移）。"
        )
    if weak_structure_labels:
        evidence.append(f"板块结构转弱（{'、'.join(weak_structure_labels)}）。")
    if oversold_stabilizing:
        evidence.append(
            f"超跌后出现企稳组合（{'、'.join(stabilization_labels)}）。"
        )
    if leverage_crowding:
        as_of = str(margin_fields.get("margin_as_of") or "最近披露日")
        evidence.append(
            f"融资拥挤慢变量升高（正向窗口{positive_financing_count}个，"
            f"余额占比{financing_ratio:.2f}%）截至{as_of}。"
            if financing_ratio is not None
            else f"融资拥挤慢变量升高（正向窗口{positive_financing_count}个）截至{as_of}。"
        )
        if percentile_120d is not None or percentile_60d is not None:
            percentile_parts = []
            if percentile_60d is not None:
                percentile_parts.append(f"60日{percentile_60d:.1f}%分位")
            if percentile_120d is not None:
                percentile_parts.append(f"120日{percentile_120d:.1f}%分位")
            evidence.append(f"融资余额占比自身历史位置：{'、'.join(percentile_parts)}。")
        if slope_present:
            evidence.append(
                "逐日融资净买入OLS斜率："
                + "、".join(
                    f"{window}日{value:+.4f}亿/交易日"
                    for window, value in zip((5, 10, 20), financing_slopes)
                    if value is not None
                )
                + "。"
            )
    if deleveraging:
        as_of = str(margin_fields.get("margin_as_of") or "最近披露日")
        evidence.append(f"融资净买入有{negative_financing_count}个窗口为负（截至{as_of}，T+1慢变量）。")
    if leverage_data_count == 0:
        counter_evidence.append("T+1融资数据缺失，不判断杠杆拥挤或去杠杆。")
    elif leverage_data_count < 3:
        counter_evidence.append("T+1融资窗口不足3个，杠杆结论已降级。")
    if financing_buy_turnover_ratio is not None:
        evidence.append(
            f"融资买入额/同交易日板块成交额{financing_buy_turnover_ratio:.2f}%"
            f"（成交额日{turnover_as_of}，融资披露日{margin_as_of}，T+1披露）。"
        )
    elif financing_buy is not None:
        counter_evidence.append(
            "融资买入额与板块成交额交易日未对齐或成交额缺失，比例保持空值。"
        )
    if non_leveraged_flow_audited and non_leveraged_net_inflow is not None:
        evidence.append(
            f"可审计非杠杆净增量资金{non_leveraged_net_inflow:+.2f}亿。"
        )
    else:
        counter_evidence.append(
            "公开订单流无法识别投资者是否使用融资，未把主力资金算法冒充非杠杆增量资金。"
        )
    if (
        etf_flow_audited
        and etf_share_net_change is not None
        and etf_share_change_pct is not None
    ):
        evidence.append(
            f"关联ETF真实份额净变化{etf_share_net_change:+.0f}份"
            f"（{etf_share_change_pct:+.2f}%）。"
        )
    else:
        counter_evidence.append(
            "关联ETF真实份额申赎缺少授权审计数据，未用ETF价格或成交额替代。"
        )
    counter_evidence.append("融资为T+1慢变量，只能作为一个确认维度，不能单独触发高危或交易动作。")

    distribution_family_flags = {
        "阶段位置": high_price_location,
        "订单流衰竭": order_flow_exhausted,
        "资金价格承载": price_response_weak,
        "板块结构": structure_weak,
        "融资拥挤": leverage_crowding,
        "可审计增量撤退": bool(
            (non_leveraged_flow_audited and non_leveraged_net_inflow is not None and non_leveraged_net_inflow < 0)
            or (etf_flow_audited and etf_share_change_pct is not None and etf_share_change_pct < 0)
        ),
    }
    distribution_family_count = sum(distribution_family_flags.values())
    high_distribution = bool(
        high_price_location
        and order_flow_exhausted
        and distribution_family_count >= 3
    )
    deleveraging_stampede = deleveraging and negative_price and negative_order_flow
    high_risk_data = data_quality in {"high", "good"}
    healthy_increment_families = {
        "价格正向": bool(change is not None and change > 0),
        "当日订单流正向": bool(
            current_net is not None
            and current_net > 0
            and current_flow_material
        ),
        "上涨广度扩散": bool(advance_ratio is not None and advance_ratio >= 50),
        "可审计非杠杆增量": bool(
            (
                non_leveraged_flow_audited
                and non_leveraged_net_inflow is not None
                and non_leveraged_net_inflow > 0
            )
            or (
                etf_flow_audited
                and etf_share_change_pct is not None
                and etf_share_change_pct > 0
            )
        ),
    }
    healthy_increment_count = sum(healthy_increment_families.values())
    if data_quality == "partial":
        counter_evidence.append("当前数据质量为partial，只允许观察级结论，不升级为HIGH。")

    if high_distribution:
        instantaneous_state = "高位派发风险"
        confirmations = distribution_family_count
    elif deleveraging_stampede:
        instantaneous_state = "去杠杆踩踏"
        confirmations = sum((deleveraging, negative_price, negative_order_flow))
    elif oversold_stabilizing:
        instantaneous_state = "超跌企稳观察"
        confirmations = len(stabilization_labels)
    elif price_response_weak or order_flow_exhausted or (
        negative_price and negative_order_flow
    ):
        instantaneous_state = "资金承载衰减"
        confirmations = sum((
            price_response_weak,
            order_flow_exhausted,
            structure_weak,
            negative_price and negative_order_flow,
        ))
    elif leverage_crowding:
        instantaneous_state = "杠杆追涨观察"
        confirmations = 1
    elif healthy_increment_count >= 2:
        instantaneous_state = "健康增量"
        confirmations = healthy_increment_count
        evidence.append(
            "健康增量由至少两个正向证据家族确认（"
            + "、".join(
                label for label, matched in healthy_increment_families.items() if matched
            )
            + "）。"
        )
    else:
        instantaneous_state = "数据不足"
        confirmations = healthy_increment_count
        counter_evidence.append(
            "未发现风险六态的联合条件，但正向增量证据不足两个独立家族，"
            "不把中性或零值行情误标为健康增量。"
        )

    persistence_state = str(persistence_fields.get("persistence_state") or "")
    sample_confirmation_count = int(
        _optional_int(persistence_fields.get("sample_confirmation_count")) or 0
    )
    trading_day_confirmation_count = int(
        _optional_int(persistence_fields.get("trading_day_confirmation_count")) or 0
    )
    # Only immutable, facts-deduplicated history may confirm persistence.  The
    # current calculation is persisted after this builder returns, so it must
    # not be speculatively counted from a newer provider timestamp.  The next
    # read will include it once the minimum sample interval has been enforced.
    persistence_confirmed = bool(
        persistence_state == instantaneous_state
        and (
            sample_confirmation_count >= 2
            or trading_day_confirmation_count >= 2
        )
    )
    persistence_output = dict(persistence_fields)
    persistence_output.update({
        "sample_confirmation_count": sample_confirmation_count,
        "trading_day_confirmation_count": trading_day_confirmation_count,
        "persistence_confirmed": persistence_confirmed,
        "confirmed_state": instantaneous_state if persistence_confirmed else "",
    })
    if persistence_confirmed:
        basis = list(persistence_output.get("persistence_basis") or [])
        if sample_confirmation_count >= 2:
            basis.append(f"连续{sample_confirmation_count}个有效采样点同态")
        if trading_day_confirmation_count >= 2:
            basis.append(f"连续{trading_day_confirmation_count}个交易日同态")
        persistence_output["persistence_basis"] = list(dict.fromkeys(basis))
    high_state_persisted = bool(
        persistence_confirmed and persistence_state == instantaneous_state
    )
    if instantaneous_state in {"高位派发风险", "去杠杆踩踏"} and not high_state_persisted:
        counter_evidence.append(
            f"瞬时状态为“{instantaneous_state}”，但尚未满足连续2个有效采样点或2个交易日确认，"
            "保留原始证据并降为观察态。"
        )

    if instantaneous_state == "高位派发风险" and high_risk_data and high_state_persisted:
        state = "高位派发风险"
        level = "HIGH"
        actions = [
            "禁止追高；等待订单流方向止跌且价格重新响应后再评估。",
            "已有仓位只按预设结构止损或利润保护计划分批降风险，不因融资慢变量机械处理。",
        ]
    elif instantaneous_state == "高位派发风险":
        state = "资金承载衰减"
        level = "MEDIUM"
        actions = [
            "高位派发瞬时证据已出现但仍待持续确认；暂停追涨，等待下一有效采样复核。",
        ]
    elif instantaneous_state == "去杠杆踩踏" and high_risk_data and high_state_persisted:
        state = "去杠杆踩踏"
        level = "HIGH"
        actions = [
            "禁止接飞刀；等待价格止跌、订单流方向拐头与承接恢复共同确认。",
            "仅在价格、现金订单流与T+1融资三类证据共振时按原风控计划降风险。",
        ]
    elif instantaneous_state == "去杠杆踩踏":
        state = "资金承载衰减"
        level = "MEDIUM"
        actions = [
            "去杠杆踩踏瞬时证据仍待持续确认；禁止接飞刀并等待下一有效采样复核。",
        ]
    elif instantaneous_state == "超跌企稳观察":
        state = "超跌企稳观察"
        level = "MEDIUM"
        actions = [
            "仅列入超跌企稳观察，不抢第一根反弹；等待回踩不破和资金承接继续改善。",
            "超跌标签本身不构成抄底或加仓指令。",
        ]
    elif instantaneous_state == "资金承载衰减":
        state = "资金承载衰减"
        level = "MEDIUM"
        actions = [
            "暂停追涨或加仓；观察后续放量能否带来有效价格推进。",
            "若订单流方向重新拐头且价格收复关键位置，再按原计划恢复评估。",
        ]
    elif instantaneous_state == "杠杆追涨观察":
        state = "杠杆追涨观察"
        level = "MEDIUM"
        actions = [
            "降低追涨冲动并等待价格与现金订单流确认；两融单项不构成交易指令。",
        ]
    elif instantaneous_state == "健康增量":
        state = "健康增量"
        level = "LOW"
        actions = ["未发现资金承载与杠杆的联合背离；仅在预设触发条件满足时执行。"]
    else:
        state = "数据不足"
        level = "UNKNOWN"
        actions = ["正向与风险证据均未形成联合确认；继续观察，不据此交易。"]

    score = (
        15 * int(high_price_location)
        + 25 * int(order_flow_exhausted)
        + 25 * int(price_response_weak)
        + 15 * int(leverage_crowding)
        + 20 * int(deleveraging)
        + 10 * int(negative_price and negative_order_flow)
        + 10 * int(structure_weak)
    )
    if state == "高位派发风险" and level == "HIGH":
        score = max(score, 80)
    elif state == "去杠杆踩踏" and level == "HIGH":
        score = max(score, 80)
    elif state == "去杠杆踩踏":
        score = max(55, min(score, 74))
    elif state == "资金承载衰减":
        score = max(45, min(score, 74))
    elif state == "杠杆追涨观察":
        # The leverage family alone is explicitly capped below high risk.
        score = min(max(score, 30), 45)
    else:
        score = min(score, 24)

    return {
        "distribution_state": state,
        "instantaneous_distribution_state": instantaneous_state,
        "distribution_risk_level": level,
        "distribution_risk_score": int(_clamp(float(score))),
        "order_flow_exhausted": order_flow_exhausted,
        "leverage_crowding": leverage_crowding,
        "price_response_weak": price_response_weak,
        "distribution_confirmation_count": int(confirmations),
        "capital_price_carrying_efficiency": carrying_efficiency,
        "capital_price_carrying_sample_count": carrying_sample_count,
        "capital_price_carrying_span_minutes": carrying_span_minutes,
        "capital_price_carrying_slope": carrying_slope,
        "financing_buy_turnover_ratio": _round_optional(financing_buy_turnover_ratio),
        "financing_turnover_date_aligned": financing_turnover_date_aligned,
        "non_leveraged_net_inflow": (
            _round_optional(non_leveraged_net_inflow)
            if non_leveraged_flow_audited else None
        ),
        "non_leveraged_flow_audited": non_leveraged_flow_audited,
        "etf_share_net_change": (
            _round_optional(etf_share_net_change)
            if etf_flow_audited else None
        ),
        "etf_share_change_pct": (
            _round_optional(etf_share_change_pct, 4)
            if etf_flow_audited else None
        ),
        "etf_flow_audited": etf_flow_audited,
        **persistence_output,
        # ``strict_state`` is the current six-state conclusion.  The prior
        # persisted state remains available separately as ``persistence_state``
        # and ``confirmed_state`` so consumers never display a stale state as
        # today's result.
        "strict_state": state,
        "distribution_evidence": evidence,
        "distribution_counter_evidence": counter_evidence,
        "distribution_actions": actions,
    }


def _status_and_risk(
    *,
    heat: float,
    trend: float,
    flow: float,
    change: float | None,
    change_5d: float | None,
    change_10d: float | None,
    current_net: float | None,
    speed: float | None,
    acceleration: float | None,
    turning: str,
    window_count: int,
) -> tuple[str, str]:
    if window_count < 2:
        return "数据不足", "UNKNOWN"

    falling = (
        (change is not None and change <= -1.0)
        or (current_net is not None and current_net < 0 and speed is not None and speed < 0)
        or turning == "down"
    )
    improving = (
        turning == "up"
        or (speed is not None and speed > 0 and acceleration is not None and acceleration >= 0)
        or (change is not None and change > 0 and current_net is not None and current_net >= 0)
    )
    deteriorating = (
        turning == "down"
        or (speed is not None and speed < 0 and acceleration is not None and acceleration < 0)
        or (change is not None and change < 0 and current_net is not None and current_net < 0)
    )
    historically_cold = (
        (change_5d is not None and change_5d <= -5.0)
        or (change_10d is not None and change_10d <= -8.0)
        or trend <= 30
    )
    strongly_hot = heat >= 76 or (
        change_5d is not None and change_10d is not None and change_5d >= 10 and change_10d >= 16
    )

    if strongly_hot and deteriorating:
        if flow < 42 or (change is not None and change <= -1.5):
            return "过热兑现风险", "HIGH"
        return "过热分歧", "MEDIUM"
    if strongly_hot:
        return "偏热趋势健康", "MEDIUM"
    if historically_cold and falling and not improving:
        return "过冷仍下跌", "HIGH"
    if historically_cold and improving:
        if change is not None and change > 0 and flow >= 52:
            return "修复初步确认", "LOW"
        return "过冷企稳观察", "MEDIUM"
    if historically_cold:
        return "过冷企稳观察", "MEDIUM"
    if heat >= 62 and trend >= 52 and flow >= 50:
        return "偏热趋势健康", "LOW"
    if trend >= 52 and flow >= 48:
        return "健康趋势", "LOW"
    if improving and flow >= 52:
        return "修复初步确认", "LOW"
    return "震荡中性", "LOW"


def _actions_for(status: str) -> list[str]:
    actions = {
        "过热兑现风险": [
            "禁止追高，等待回踩承接、订单流方向止跌并重新拐头后再评估。",
            "已有仓位按交易计划保护利润；过热标签本身不构成机械清仓指令。",
        ],
        "过热分歧": [
            "暂停追高，观察供应商订单流方向流速与加速度能否修复。",
            "若已有仓位，按结构止损和原计划执行，不因拥挤指标单独卖出。",
        ],
        "偏热趋势健康": [
            "趋势尚健康，但禁止在直线加速段追高，只等待回踩确认。",
            "过热度仅用于降低追涨仓位，不构成自动卖出信号。",
        ],
        "健康趋势": [
            "按计划等待缩量回踩不破或放量突破确认，避免随手追涨。",
        ],
        "过冷仍下跌": [
            "禁止接飞刀，等待止跌、订单流方向拐头和量价承接共同确认。",
            "过冷不等于买点。",
        ],
        "过冷企稳观察": [
            "仅列入观察，不抢第一根反弹；等待回踩不破与订单流方向持续改善。",
            "过冷不等于买入。",
        ],
        "修复初步确认": [
            "可继续观察修复持续性，等待回踩确认后再按计划小仓试错。",
            "不在直线拉升时追入。",
        ],
        "震荡中性": [
            "暂无明确冷热优势，只执行预先定义的交易模式和触发条件。",
        ],
        "数据不足": [
            "数据不足，不生成确定性冷热结论，不据此交易。",
        ],
    }
    return list(actions.get(status, actions["数据不足"]))


def _data_quality(current: Any, five_day: Any, ten_day: Any, kinetics_count: int) -> str:
    windows = sum(row is not None for row in (current, five_day, ten_day))
    if windows == 3 and kinetics_count >= 2:
        return "high"
    if windows == 3:
        return "good"
    if windows == 2:
        return "partial"
    if windows == 1:
        return "limited"
    return "missing"


def _shanghai_now() -> datetime:
    return datetime.now(_SHANGHAI_TZ)


def _provider_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=_SHANGHAI_TZ)
    return parsed.astimezone(_SHANGHAI_TZ)


def _build_item(
    current: Any,
    five_day: Any,
    ten_day: Any,
    *,
    margin: Any,
    attention: Any,
    persistence: Any,
    board_type: str,
) -> dict[str, Any]:
    name = _row_name(current or five_day or ten_day)
    change = _change(current)
    change_5d = _change(five_day)
    change_10d = _change(ten_day)
    current_net = _net(current)
    net_5d = _net(five_day)
    net_10d = _net(ten_day)
    flow_ratio = _optional_float(_value(current, "flow_ratio"))
    flow_ratio_5d = _optional_float(_value(five_day, "flow_ratio"))
    flow_ratio_10d = _optional_float(_value(ten_day, "flow_ratio"))
    speed = _optional_float(_value(current, "flow_speed", "speed"))
    acceleration = _optional_float(_value(current, "flow_acceleration", "acceleration"))
    turning_raw = _value(current, "flow_turning", "turning_point", "turning", default="")
    turning = _turning_direction(turning_raw)
    limit_up_count = _optional_int(
        _value(current, "limit_up_count", "limit_count", "zt_count", default=0)
    ) or 0

    trend_score = _trend_score(change, change_5d, change_10d)
    flow_score = _flow_score(current_net, net_5d, net_10d, speed, acceleration, turning)
    crowding_score = _crowding_score(change, change_5d, change_10d, limit_up_count)
    attention_score = _attention_score(attention)
    margin_fields = _margin_fields(margin)
    margin_score = margin_fields["margin_score"]
    structure_fields = _structure_fields(current, change)
    persistence_fields = _persistence_fields(persistence)
    non_leveraged_flow_audited = bool(_optional_bool(_value(
        current,
        "non_leveraged_flow_audited",
        default=False,
    )))
    non_leveraged_net_inflow = (
        _optional_float(_value(
            current,
            "non_leveraged_net_inflow",
            "audited_non_leveraged_net_inflow",
        ))
        if non_leveraged_flow_audited
        else None
    )
    non_leveraged_flow_source_url = (
        str(_value(current, "non_leveraged_flow_source_url", default="") or "")
        if non_leveraged_flow_audited
        else ""
    )
    non_leveraged_flow_published_at = (
        str(_value(current, "non_leveraged_flow_published_at", default="") or "")
        if non_leveraged_flow_audited
        else ""
    ) or None
    non_leveraged_net_inflow_unit = (
        str(_value(current, "non_leveraged_net_inflow_unit", default="") or "")
        if non_leveraged_flow_audited
        else ""
    )
    non_leveraged_methodology_id = (
        str(_value(current, "non_leveraged_methodology_id", default="") or "")
        if non_leveraged_flow_audited
        else ""
    )
    etf_flow_audited = bool(_optional_bool(_value(
        current,
        "etf_flow_audited",
        default=False,
    )))
    etf_share_net_change = (
        _optional_float(_value(current, "etf_share_net_change"))
        if etf_flow_audited
        else None
    )
    etf_share_change_pct = (
        _optional_float(_value(current, "etf_share_change_pct"))
        if etf_flow_audited
        else None
    )
    etf_id = str(_value(current, "etf_id", default="") or "") if etf_flow_audited else ""
    etf_share_unit = (
        str(_value(current, "etf_share_unit", default="") or "")
        if etf_flow_audited
        else ""
    )
    etf_share_base = (
        _optional_float(_value(current, "etf_share_base"))
        if etf_flow_audited
        else None
    )
    etf_methodology_id = (
        str(_value(current, "etf_methodology_id", default="") or "")
        if etf_flow_audited
        else ""
    )

    heat_score = int(round(_weighted(
        (
            (trend_score, 0.40),
            (flow_score, 0.30),
            (crowding_score, 0.15),
            (attention_score, 0.075),
            (margin_score, 0.075),
        )
    )))
    window_count = sum(row is not None for row in (current, five_day, ten_day))
    coverage = sum(value is not None for value in (change, change_5d, change_10d, current_net, net_5d, net_10d))
    status, risk_level = _status_and_risk(
        heat=heat_score,
        trend=trend_score,
        flow=flow_score,
        change=change,
        change_5d=change_5d,
        change_10d=change_10d,
        current_net=current_net,
        speed=speed,
        acceleration=acceleration,
        turning=turning,
        window_count=window_count,
    )

    evidence: list[str] = []
    counter_evidence: list[str] = []
    if change is not None:
        evidence.append(f"当日涨跌 {change:+.2f}%")
    if change_5d is not None:
        evidence.append(f"近5日涨跌 {change_5d:+.2f}%")
    if change_10d is not None:
        evidence.append(f"近10日涨跌 {change_10d:+.2f}%")
    if current_net is not None:
        evidence.append(f"当日订单流方向净额 {current_net:+.2f}亿（供应商算法）")
    if net_5d is not None:
        evidence.append(f"近5日订单流方向净额 {net_5d:+.2f}亿（供应商算法）")
    if net_10d is not None:
        evidence.append(f"近10日订单流方向净额 {net_10d:+.2f}亿（供应商算法）")
    if speed is not None:
        evidence.append(f"订单流方向流速 {speed:+.3f}亿/分钟（供应商算法）")
    if acceleration is not None:
        evidence.append(f"订单流方向加速度 {acceleration:+.4f}亿/分钟²（供应商算法）")
    if turning:
        evidence.append("订单流方向出现向上拐点" if turning == "up" else "订单流方向出现向下拐点")
    if limit_up_count:
        evidence.append(f"板块涨停 {limit_up_count} 只")

    if attention_score is None:
        counter_evidence.append("关注度代理缺失，未按0分处理。")
    else:
        rank = _optional_int(_value(attention, "rank", "attention_rank"))
        suffix = f"（排名{rank}）" if rank is not None else ""
        evidence.append(f"关注度代理 {attention_score:.0f}分{suffix}")
    if margin_score is None:
        counter_evidence.append("板块融资拥挤度缺失，未生成融资结论。")
    else:
        as_of = margin_fields["margin_as_of"] or "最近披露日"
        evidence.append(f"融资拥挤度 {margin_score:.0f}分（截至{as_of}，T+1慢变量）")
        counter_evidence.append("融资数据不是盘中实时数据，不能单独触发买入或卖出。")
    if coverage < 6:
        counter_evidence.append("多周期价格或订单流方向窗口不完整，结论已降级。")

    kinetics_count = sum(value is not None for value in (speed, acceleration)) + int(bool(turning))
    provider_trade_date = str(_value(current, "provider_trade_date", default="") or "")[:10]
    provider_updated_at = str(_value(current, "provider_updated_at", default="") or "")
    now = _shanghai_now()
    shanghai_today = now.date().isoformat()
    current_stale = bool(provider_trade_date and provider_trade_date != shanghai_today)
    data_quality = _data_quality(current, five_day, ten_day, kinetics_count)
    if current is not None and not provider_trade_date:
        data_quality = "partial" if data_quality in {"high", "good"} else data_quality
        counter_evidence.append("当日板块快照缺少上游交易日期，未标记为实时高质量数据。")
    if current_stale:
        data_quality = "stale"
        counter_evidence.append(f"当日板块快照实际截至 {provider_trade_date}，不能作为今日盘中实时拐点。")
    provider_dt = _provider_datetime(provider_updated_at)
    if current is not None and not provider_updated_at:
        if data_quality in {"high", "good"}:
            data_quality = "partial"
        counter_evidence.append("当日板块快照缺少精确更新时间，不能标记为高质量实时数据。")
    elif provider_dt is None and provider_updated_at:
        if data_quality in {"high", "good"}:
            data_quality = "partial"
        counter_evidence.append("当日板块快照更新时间无法解析，实时性已降级。")
    elif provider_dt is not None and provider_dt.date() == now.date() and not current_stale:
        age_minutes = (now - provider_dt).total_seconds() / 60
        if age_minutes > 15:
            data_quality = "stale"
            counter_evidence.append(f"当日板块快照已滞后 {age_minutes:.0f} 分钟，不能作为当前盘中拐点。")
        elif age_minutes > 5 and data_quality == "high":
            data_quality = "good"
            counter_evidence.append(f"当日板块快照已滞后 {age_minutes:.0f} 分钟，实时质量已降级。")
        elif age_minutes < -2:
            if data_quality in {"high", "good"}:
                data_quality = "partial"
            counter_evidence.append("当日板块快照时间晚于系统时钟，实时质量已降级。")

    cache_used = bool(_value(current, "_cache_used", "cache_used", default=False))
    if cache_used:
        cache_source = str(_value(current, "_cache_source", "cache_source", default="未知来源") or "未知来源")
        cache_date = str(_value(current, "_cache_trade_date", "cache_trade_date", default="") or "")[:10]
        if data_quality in {"high", "good"}:
            data_quality = "partial"
        suffix = f"，缓存日期 {cache_date}" if cache_date else ""
        counter_evidence.append(f"当日板块快照来自 {cache_source} 缓存{suffix}，不标记为高质量实时数据。")
    distribution = _distribution_assessment(
        change=change,
        change_5d=change_5d,
        change_10d=change_10d,
        current_net=current_net,
        net_5d=net_5d,
        net_10d=net_10d,
        speed=speed,
        acceleration=acceleration,
        turning=turning,
        flow_ratio=flow_ratio,
        flow_ratio_5d=flow_ratio_5d,
        flow_ratio_10d=flow_ratio_10d,
        structure_fields=structure_fields,
        margin_fields=margin_fields,
        persistence_fields=persistence_fields,
        provider_trade_date=provider_trade_date,
        provider_updated_at=provider_updated_at,
        non_leveraged_net_inflow=non_leveraged_net_inflow,
        non_leveraged_flow_audited=non_leveraged_flow_audited,
        etf_share_net_change=etf_share_net_change,
        etf_share_change_pct=etf_share_change_pct,
        etf_flow_audited=etf_flow_audited,
        data_quality=data_quality,
    )
    counter_evidence.extend(distribution["distribution_counter_evidence"])
    item = {
        "name": name,
        "board_code": str(_value(current or five_day or ten_day, "board_code", default="") or "") or None,
        "board_type": board_type,
        "heat_score": heat_score,
        "status": status,
        "risk_level": risk_level,
        "trend_score": round(trend_score, 2),
        "flow_score": round(flow_score, 2),
        "crowding_score": _round_optional(crowding_score),
        "margin_score": margin_score,
        "attention_score": _round_optional(attention_score),
        "change_pct": _round_optional(change),
        "change_pct_5d": _round_optional(change_5d),
        "change_pct_10d": _round_optional(change_10d),
        "net_inflow": _round_optional(current_net),
        "net_inflow_5d": _round_optional(net_5d),
        "net_inflow_10d": _round_optional(net_10d),
        "flow_ratio": _round_optional(flow_ratio, 4),
        "flow_ratio_5d": _round_optional(flow_ratio_5d, 4),
        "flow_ratio_10d": _round_optional(flow_ratio_10d, 4),
        "flow_speed": _round_optional(speed, 4),
        "flow_acceleration": _round_optional(acceleration, 6),
        "flow_turning": str(turning_raw or "") or None,
        "provider_trade_date": provider_trade_date or None,
        "provider_updated_at": provider_updated_at or None,
        "non_leveraged_flow_source_url": non_leveraged_flow_source_url,
        "non_leveraged_flow_published_at": non_leveraged_flow_published_at,
        "non_leveraged_net_inflow_unit": non_leveraged_net_inflow_unit,
        "non_leveraged_methodology_id": non_leveraged_methodology_id,
        "etf_id": etf_id,
        "etf_share_unit": etf_share_unit,
        "etf_share_base": _round_optional(etf_share_base, 4),
        "etf_methodology_id": etf_methodology_id,
        "limit_up_count": limit_up_count,
        **structure_fields,
        **{key: value for key, value in margin_fields.items() if key != "margin_score"},
        **distribution,
        "evidence": evidence,
        "counter_evidence": counter_evidence,
        "actions": [*_actions_for(status), *distribution["distribution_actions"]],
        "data_quality": data_quality,
    }
    return item


def build_sector_temperature(
    current_rows: Iterable[Any] | None,
    five_day_rows: Iterable[Any] | None,
    ten_day_rows: Iterable[Any] | None,
    margin_by_name: Mapping[str, Any] | None = None,
    attention_by_name: Mapping[str, Any] | None = None,
    persistence_by_name: Mapping[str, Any] | None = None,
    board_type: str = "行业",
    updated_at: datetime | str | None = None,
) -> dict[str, Any]:
    """Build an explainable multi-window sector temperature assessment.

    Temperature is a descriptive crowding/odds signal, not an order signal:
    overheat never mechanically means sell and oversold never means buy.  Public
    margin data is deliberately forced to ``margin_realtime=False`` because it
    is disclosed on a T+1 basis.
    """

    current_index, current_ordered = _index_rows(current_rows)
    five_index, five_ordered = _index_rows(five_day_rows)
    ten_index, ten_ordered = _index_rows(ten_day_rows)

    seeds: list[Any] = list(current_ordered)
    known_names = {_row_name(row) for row in seeds}
    for row in [*five_ordered, *ten_ordered]:
        if _row_name(row) not in known_names:
            seeds.append(row)
            known_names.add(_row_name(row))

    items: list[dict[str, Any]] = []
    for seed in seeds:
        current = _lookup(current_index, seed)
        five_day = _lookup(five_index, seed)
        ten_day = _lookup(ten_index, seed)
        reference = current or five_day or ten_day or seed
        items.append(_build_item(
            current,
            five_day,
            ten_day,
            margin=_mapping_lookup(margin_by_name, reference),
            attention=_mapping_lookup(attention_by_name, reference),
            persistence=_mapping_lookup(persistence_by_name, reference),
            board_type=board_type,
        ))

    items.sort(key=lambda item: (item["heat_score"], item["flow_score"]), reverse=True)
    overheated = [
        item for item in items
        if item["status"] in {"过热分歧", "过热兑现风险"}
    ]
    stabilizing = [
        item for item in items
        if item["status"] in {"过冷企稳观察", "修复初步确认"}
    ]
    oversold_watch = [
        item for item in items
        if item["status"] in {"过冷仍下跌", "过冷企稳观察"}
    ]

    if updated_at is None:
        updated: str = datetime.now(_SHANGHAI_TZ).replace(tzinfo=None).isoformat()
    elif isinstance(updated_at, datetime):
        updated = updated_at.isoformat()
    else:
        updated = str(updated_at)

    return {
        "source": "东方财富多周期板块订单流方向估算+T+1融资拥挤度+关注度代理",
        "updated_at": updated,
        "board_type": board_type,
        "lookback_windows": [1, 5, 10, 20],
        "items": items,
        "overheated": overheated,
        "stabilizing": stabilizing,
        "oversold_watch": oversold_watch,
        "notes": [
            "板块冷热使用当日、5日、10日涨跌与供应商订单流方向估算、盘中流速/加速度综合判断；20日仅用于融资拥挤慢变量，不伪装成20日实时订单流。",
            "过热只表示拥挤与追涨赔率下降，不构成机械卖出；过冷不等于买点，必须等待止跌和量价确认。",
            "融资数据为T+1慢变量，关注度仅为热度代理，二者都不能单独触发交易动作。",
        ],
    }
