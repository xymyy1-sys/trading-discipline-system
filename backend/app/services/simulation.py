from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from typing import Any, Callable

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.helpers.decision import quote_for_code
from app.api.helpers.quotes import _is_realtime_note, _normalize_code
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    ExpectationSnapshot,
    MarketRegimeSnapshot,
    PositionExecutionState,
    SimulationAccount,
    SimulationClosedTrade,
    SimulationDailyEquity,
    SimulationEvidenceSnapshot,
    SimulationFill,
    SimulationOrder,
    SimulationPosition,
    SimulationTradeLot,
    VolumePriceSnapshot,
)
from app.schemas.simulation import SimulationAccountCreate, SimulationOrderCreate


QuoteLoader = Callable[[str], dict[str, Any]]
MAX_MATCH_QUOTE_AGE_SECONDS = 120
MAX_FUTURE_CLOCK_SKEW_SECONDS = 5


def _local_now() -> datetime:
    return shanghai_now_naive()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _safe_float(value: Any) -> float:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number else 0.0


def _to_local_naive(value: datetime) -> datetime:
    return shanghai_now_naive(value)


def _quote_time(quote: dict[str, Any], evaluated_at: datetime | None = None) -> datetime | None:
    """Return the provider event time, never the server evaluation time.

    ``quote_time``/``datetime`` remain accepted for explicit test and legacy
    adapters. ``updated_at`` and ``received_at`` are observation metadata and
    must not be promoted to exchange-event time.
    """
    del evaluated_at
    raw = quote.get("provider_event_at") or quote.get("quote_time") or quote.get("datetime")
    if isinstance(raw, datetime):
        return _to_local_naive(raw)
    if raw:
        try:
            return _to_local_naive(datetime.fromisoformat(str(raw).replace("Z", "+00:00")))
        except ValueError:
            pass
    raw_date = str(quote.get("date") or quote.get("trade_date") or "").strip()
    raw_time = str(quote.get("time") or "").strip()
    if raw_date and raw_time:
        try:
            return datetime.fromisoformat(f"{raw_date}T{raw_time}")
        except ValueError:
            pass
    return None


def _quote_age_seconds(quote_at: datetime | None, evaluated_at: datetime) -> float | None:
    return (evaluated_at - quote_at).total_seconds() if quote_at else None


def _quote_data_quality(quote: dict[str, Any], evaluated_at: datetime) -> str:
    quote_at = _quote_time(quote)
    age = _quote_age_seconds(quote_at, evaluated_at)
    if quote_at is None:
        return "timestamp_missing"
    if age is not None and age < -MAX_FUTURE_CLOCK_SKEW_SECONDS:
        return "future"
    if age is not None and age > MAX_MATCH_QUOTE_AGE_SECONDS:
        return "stale"
    if not _is_realtime_note(str(quote.get("note") or "")):
        return "missing"
    return "realtime"


def _is_trading_session(value: datetime) -> bool:
    if value.weekday() >= 5:
        return False
    current = value.time()
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def _minute_bucket(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


def _price_limit_ratio(code: str, name: str) -> float:
    normalized = _normalize_code(code)
    upper_name = (name or "").upper()
    if "ST" in upper_name:
        return 0.05
    if normalized.startswith(("300", "301", "688", "689")):
        return 0.20
    if normalized.startswith(("4", "8")):
        return 0.30
    return 0.10


def _tick(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _price_limits(code: str, name: str, quote: dict[str, Any]) -> tuple[float, float]:
    previous_close = _safe_float(quote.get("prev_close"))
    upper = _safe_float(quote.get("limit_up_price"))
    lower = _safe_float(quote.get("limit_down_price"))
    if previous_close > 0:
        ratio = _price_limit_ratio(code, name)
        upper = upper or _tick(previous_close * (1 + ratio))
        lower = lower or _tick(previous_close * (1 - ratio))
    return upper, lower


def _is_t0_instrument(code: str, name: str, quote: dict[str, Any]) -> bool:
    """Return True only for instruments whose T+0 identity is explicit/safe.

    Domestic stock ETFs remain T+1.  The conservative prefix set covers the
    exchange-traded money/bond, cross-border and gold families; an upstream
    instrument master can override it with settlement_cycle/t_plus.
    """
    explicit = str(quote.get("settlement_cycle") or quote.get("t_plus") or "").upper().replace(" ", "")
    if explicit in {"0", "T+0", "T0", "SAME_DAY"}:
        return True
    if explicit in {"1", "T+1", "T1"}:
        return False
    normalized = _normalize_code(code)
    if normalized.startswith(("511", "513", "518")):
        return True
    return normalized in {
        "159920", "159934", "159941", "159980", "159981", "159985",
    }


def _aliases(code: str) -> list[str]:
    normalized = _normalize_code(code)
    return list(dict.fromkeys([code, normalized, normalized.zfill(6), normalized.lstrip("0")]))


def _orm_payload(row: Any | None, fields: tuple[str, ...]) -> dict[str, Any]:
    if row is None:
        return {}
    return {field: getattr(row, field, None) for field in fields}


def _gap_band(score: int) -> str:
    if score <= -18:
        return "severe_negative"
    if score <= -8:
        return "negative"
    if score < 8:
        return "matched"
    if score < 18:
        return "positive"
    return "strong_positive"


def create_account(db: Session, payload: SimulationAccountCreate) -> SimulationAccount:
    row = SimulationAccount(
        **payload.model_dump(),
        cash=payload.initial_cash,
        status="active",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _capture_evidence(
    db: Session,
    account: SimulationAccount,
    payload: SimulationOrderCreate,
    quote: dict[str, Any],
    captured_at: datetime,
) -> SimulationEvidenceSnapshot:
    code = _normalize_code(payload.code)
    aliases = _aliases(code)
    trade_date = captured_at.date().isoformat()
    quote_at = _quote_time(quote, captured_at)
    market = (
        db.query(MarketRegimeSnapshot)
        .filter(
            MarketRegimeSnapshot.trade_date == trade_date,
            MarketRegimeSnapshot.captured_at <= captured_at,
        )
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )
    expectation = (
        db.query(ExpectationSnapshot)
        .filter(
            ExpectationSnapshot.code.in_(aliases),
            ExpectationSnapshot.trade_date <= trade_date,
            ExpectationSnapshot.created_at <= captured_at,
        )
        .order_by(ExpectationSnapshot.trade_date.desc(), ExpectationSnapshot.created_at.desc(), ExpectationSnapshot.id.desc())
        .first()
    )
    volume = (
        db.query(VolumePriceSnapshot)
        .filter(
            VolumePriceSnapshot.code.in_(aliases),
            VolumePriceSnapshot.trade_date == trade_date,
            VolumePriceSnapshot.captured_at <= captured_at,
        )
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .first()
    )
    execution = (
        db.query(PositionExecutionState)
        .filter(
            PositionExecutionState.code.in_(aliases),
            PositionExecutionState.trade_date == trade_date,
            PositionExecutionState.updated_at <= captured_at,
        )
        .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
        .first()
    )
    latest_version = (
        db.query(func.max(SimulationEvidenceSnapshot.version))
        .filter(
            SimulationEvidenceSnapshot.account_id == account.id,
            SimulationEvidenceSnapshot.code == code,
            SimulationEvidenceSnapshot.strategy_source == payload.strategy_source,
            SimulationEvidenceSnapshot.trade_date == trade_date,
        )
        .scalar()
        or 0
    )
    market_payload = _orm_payload(
        market,
        (
            "id", "trade_date", "captured_at", "source", "data_quality", "coverage_ratio",
            "regime_code", "regime_name", "risk_level", "opportunity_score", "loss_score",
            "liquidity_score", "advance_ratio", "limit_up_count", "limit_down_count",
            "market_main_net_inflow_yi", "strongest_sectors_json", "weakest_sectors_json",
            "evidence_json", "missing_fields_json",
        ),
    )
    expectation_payload = _orm_payload(
        expectation,
        (
            "id", "trade_date", "created_at", "stage", "base_expectation", "expected_open_low",
            "expected_open_high", "actual_open_pct", "actual_change_pct", "expectation_gap_score",
            "expectation_result", "state_transition", "confidence", "evidence_json",
            "counter_evidence_json", "suggestion",
        ),
    )
    volume_payload = _orm_payload(
        volume,
        (
            "id", "trade_date", "captured_at", "stage", "price", "change_pct", "vwap",
            "vwap_source", "vwap_reliable", "minute_bar_count", "price_vs_vwap", "high_drawdown",
            "active_buy_amount", "active_sell_amount", "attack_efficiency", "volume_acceleration",
            "pullback_sell_ratio", "pattern", "data_quality", "data_source", "evidence_json",
            "counter_evidence_json",
        ),
    )
    sector_payload = _orm_payload(
        execution,
        (
            "id", "trade_date", "updated_at", "state", "sector_state", "recommended_action",
            "recommended_reduce_ratio", "data_quality", "data_time", "evidence_json",
            "counter_evidence_json",
        ),
    )
    quote_payload = {
        key: value for key, value in quote.items()
        if key not in {"minute_bars"} and isinstance(value, (str, int, float, bool, type(None), datetime, date))
    }
    quote_payload["server_observed_at"] = captured_at
    quote_payload["received_at"] = quote.get("received_at") or captured_at
    quote_payload["provider_event_at"] = quote_at
    quote_payload["age_seconds"] = _quote_age_seconds(quote_at, captured_at)
    quote_payload["minute_bar_count"] = len(quote.get("minute_bars") or [])
    quote_payload["settlement_cycle"] = "T+0" if _is_t0_instrument(code, payload.name, quote) else "T+1"
    source_versions = {
        "market_regime_snapshot_id": getattr(market, "id", None),
        "market_captured_at": getattr(market, "captured_at", None),
        "expectation_snapshot_id": getattr(expectation, "id", None),
        "expectation_captured_at": getattr(expectation, "created_at", None),
        "volume_price_snapshot_id": getattr(volume, "id", None),
        "volume_price_captured_at": getattr(volume, "captured_at", None),
        "position_execution_state_id": getattr(execution, "id", None),
        "position_execution_updated_at": getattr(execution, "updated_at", None),
    }
    score = int(getattr(expectation, "expectation_gap_score", 0) or 0)
    # Hash the evidence facts rather than volatile observation metadata.  Two
    # snapshots of the same source versions and values must reproduce the same
    # hash, while captured_at/quote_time remain separately auditable columns.
    hash_quote_payload = {
        key: value for key, value in quote_payload.items()
        if key not in {"server_observed_at", "received_at", "quote_time", "provider_event_at", "age_seconds"}
    }
    serialized = {
        "quote": hash_quote_payload,
        "market": market_payload,
        "sector": sector_payload,
        "expectation": expectation_payload,
        "volume_price": volume_payload,
        "source_versions": source_versions,
    }
    temporal_quality = _quote_data_quality(quote, captured_at)
    row = SimulationEvidenceSnapshot(
        account_id=account.id,
        code=code,
        name=payload.name or str(quote.get("name") or code),
        strategy_source=payload.strategy_source,
        trade_date=trade_date,
        version=int(latest_version) + 1,
        captured_at=captured_at,
        quote_time=quote_at,
        data_quality=temporal_quality,
        quote_json=_json(quote_payload),
        market_json=_json(market_payload),
        sector_json=_json(sector_payload),
        expectation_json=_json(expectation_payload),
        volume_price_json=_json(volume_payload),
        source_versions_json=_json(source_versions),
        market_regime=str(getattr(market, "regime_code", "UNKNOWN") or "UNKNOWN"),
        expectation_gap_score=score,
        expectation_gap_band=_gap_band(score) if expectation else "unknown",
        volume_price_state=str(getattr(volume, "pattern", "") or ""),
        sector_state=str(getattr(execution, "sector_state", "") or ""),
        content_hash=hashlib.sha256(_json(serialized).encode("utf-8")).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row


def _roll_position(position: SimulationPosition | None, trade_date: str) -> None:
    if position is None or position.last_rollover_date == trade_date:
        return
    position.available_quantity = position.quantity
    position.today_buy_quantity = 0
    position.last_rollover_date = trade_date


def _reject(order: SimulationOrder, reason: str, evaluated_at: datetime) -> SimulationOrder:
    order.status = "REJECTED"
    order.reject_reason = reason
    order.last_evaluated_at = evaluated_at
    return order


def _commission(account: SimulationAccount, gross: float) -> float:
    return round(max(account.minimum_commission, gross * account.commission_rate), 2)


def _open_trade_lots(db: Session, account_id: int, code: str) -> list[SimulationTradeLot]:
    return (
        db.query(SimulationTradeLot)
        .filter(
            SimulationTradeLot.account_id == account_id,
            SimulationTradeLot.code == code,
            SimulationTradeLot.status == "OPEN",
            SimulationTradeLot.remaining_quantity > 0,
        )
        .order_by(SimulationTradeLot.opened_at.asc(), SimulationTradeLot.id.asc())
        .with_for_update()
        .all()
    )


def _ensure_lot_coverage(
    db: Session,
    account: SimulationAccount,
    order: SimulationOrder,
    position: SimulationPosition,
    evaluated_at: datetime,
) -> list[SimulationTradeLot]:
    """Backfill only quantities that predate the simulation ledger.

    Such a batch is intentionally labelled unattributed rather than borrowing
    the exit strategy/evidence and manufacturing a misleading win-rate slice.
    """
    lots = _open_trade_lots(db, account.id, order.code)
    covered = sum(int(lot.remaining_quantity or 0) for lot in lots)
    missing = max(int(position.quantity or 0) - covered, 0)
    if missing:
        synthetic = SimulationTradeLot(
            account_id=account.id,
            code=order.code,
            name=position.name or order.name,
            entry_order_id=None,
            entry_fill_id=None,
            entry_decision_evidence_snapshot_id=None,
            strategy_source="unattributed_legacy",
            initial_quantity=missing,
            remaining_quantity=missing,
            entry_price=float(position.average_cost or 0),
            entry_gross_amount=round(float(position.average_cost or 0) * missing, 2),
            entry_costs=0,
            status="OPEN",
            opened_at=evaluated_at,
        )
        db.add(synthetic)
        db.flush()
        lots.append(synthetic)
    return lots


def _create_entry_lot(
    db: Session,
    order: SimulationOrder,
    fill: SimulationFill,
) -> SimulationTradeLot:
    row = SimulationTradeLot(
        account_id=order.account_id,
        code=order.code,
        name=order.name or fill.name,
        entry_order_id=order.id,
        entry_fill_id=fill.id,
        entry_decision_evidence_snapshot_id=order.decision_evidence_snapshot_id,
        strategy_source=order.strategy_source,
        initial_quantity=fill.quantity,
        remaining_quantity=fill.quantity,
        entry_price=fill.price,
        entry_gross_amount=fill.gross_amount,
        entry_costs=round(fill.commission + fill.transfer_fee + fill.stamp_tax, 2),
        status="OPEN",
        opened_at=fill.filled_at,
    )
    db.add(row)
    return row


def _allocate_exit_to_lots(
    db: Session,
    order: SimulationOrder,
    fill: SimulationFill,
    lots: list[SimulationTradeLot],
) -> None:
    """FIFO allocate one exit fill; emit one result only when a lot closes."""
    remaining = int(fill.quantity)
    allocated_gross = 0.0
    allocated_costs = 0.0
    total_exit_costs = round(fill.commission + fill.stamp_tax + fill.transfer_fee, 2)
    for lot in lots:
        if remaining <= 0:
            break
        quantity = min(remaining, int(lot.remaining_quantity or 0))
        if quantity <= 0:
            continue
        is_last = quantity == remaining
        if is_last:
            exit_gross = round(fill.gross_amount - allocated_gross, 2)
            exit_costs = round(total_exit_costs - allocated_costs, 2)
        else:
            ratio = quantity / fill.quantity
            exit_gross = round(fill.gross_amount * ratio, 2)
            exit_costs = round(total_exit_costs * ratio, 2)
        allocated_gross += exit_gross
        allocated_costs += exit_costs
        entry_total = float(lot.entry_gross_amount or 0) + float(lot.entry_costs or 0)
        entry_cost_basis = round(entry_total * quantity / max(int(lot.initial_quantity or 0), 1), 2)
        allocated_pnl = round(exit_gross - exit_costs - entry_cost_basis, 2)
        lot.remaining_quantity -= quantity
        lot.exit_quantity += quantity
        lot.exit_gross_amount = round(lot.exit_gross_amount + exit_gross, 2)
        lot.exit_costs = round(lot.exit_costs + exit_costs, 2)
        lot.realized_pnl = round(lot.realized_pnl + allocated_pnl, 2)
        remaining -= quantity
        if lot.remaining_quantity == 0:
            lot.status = "CLOSED"
            lot.closed_at = fill.filled_at
            total_entry = round(lot.entry_gross_amount + lot.entry_costs, 2)
            closed = SimulationClosedTrade(
                account_id=lot.account_id,
                lot_id=lot.id,
                code=lot.code,
                name=lot.name,
                strategy_source=lot.strategy_source,
                entry_decision_evidence_snapshot_id=lot.entry_decision_evidence_snapshot_id,
                entry_order_id=lot.entry_order_id,
                entry_fill_id=lot.entry_fill_id,
                closing_order_id=order.id,
                closing_fill_id=fill.id,
                quantity=lot.initial_quantity,
                entry_average_price=lot.entry_price,
                exit_average_price=round(lot.exit_gross_amount / max(lot.exit_quantity, 1), 4),
                entry_gross_amount=lot.entry_gross_amount,
                exit_gross_amount=lot.exit_gross_amount,
                total_costs=round(lot.entry_costs + lot.exit_costs, 2),
                realized_pnl=lot.realized_pnl,
                return_pct=round(lot.realized_pnl / total_entry * 100, 4) if total_entry else 0,
                opened_at=lot.opened_at,
                closed_at=fill.filled_at,
                holding_days=max((fill.filled_at.date() - lot.opened_at.date()).days, 0),
            )
            db.add(closed)
        db.add(lot)
    if remaining:
        raise RuntimeError("模拟持仓批次覆盖不足，拒绝生成不完整闭环归因")


def _evaluate_order(
    db: Session,
    account: SimulationAccount,
    order: SimulationOrder,
    evidence: SimulationEvidenceSnapshot,
    quote: dict[str, Any],
    evaluated_at: datetime,
) -> SimulationOrder:
    order.last_evaluated_at = evaluated_at
    # ``evidence`` is the matching-time observation.  The immutable
    # decision-time reference lives on the order and must never be overwritten.
    if account.status != "active":
        return _reject(order, "模拟账户未启用", evaluated_at)
    if not _is_trading_session(evaluated_at):
        return _reject(order, "当前不在A股连续竞价时段", evaluated_at)
    quote_at = _quote_time(quote, evaluated_at)
    if quote_at is None:
        order.status = "OPEN"
        order.reject_reason = "行情缺少 provider_event_at，无法证明价格事件时点；委托保持待撮合"
        return order
    if quote_at > evaluated_at:
        return _reject(order, "行情时间晚于本次撮合评估时点，拒绝未来数据", evaluated_at)
    if quote_at.date() != evaluated_at.date():
        return _reject(order, "行情不是当前交易日数据", evaluated_at)
    age_seconds = _quote_age_seconds(quote_at, evaluated_at)
    if age_seconds is not None and age_seconds > MAX_MATCH_QUOTE_AGE_SECONDS:
        order.status = "OPEN"
        order.reject_reason = (
            f"行情事件时间已陈旧（{age_seconds:.0f}秒，阈值{MAX_MATCH_QUOTE_AGE_SECONDS}秒）；"
            "委托保持待撮合，等待新行情"
        )
        return order
    if _minute_bucket(quote_at) < _minute_bucket(order.submitted_at):
        return _reject(order, "行情早于委托决策时点，拒绝用决策前价格回填成交", evaluated_at)
    if _minute_bucket(quote_at) == _minute_bucket(order.submitted_at):
        order.status = "OPEN"
        order.reject_reason = "等待下一分钟桶的真实行情撮合，禁止同K线成交"
        return order
    price = _safe_float(quote.get("price"))
    note = str(quote.get("note") or "")
    suspended = bool(quote.get("suspended")) or str(quote.get("status") or "").lower() in {"suspended", "halted"}
    if suspended:
        return _reject(order, "标的停牌，不可成交", evaluated_at)
    if price <= 0 or not _is_realtime_note(note):
        return _reject(order, "无真实实时行情，拒绝模拟成交", evaluated_at)
    upper, lower = _price_limits(order.code, order.name, quote)
    if order.side == "BUY" and upper > 0 and price >= upper - 0.005:
        liquidity = _safe_float(quote.get("ask1_volume") or quote.get("ask_volume") or quote.get("sell_volume"))
        reason = "涨停且无可见卖盘" if liquidity <= 0 else "触及涨停价"
        return _reject(order, f"{reason} {upper:.2f}，模拟盘保守按不可成交处理", evaluated_at)
    if order.side == "SELL" and lower > 0 and price <= lower + 0.005:
        liquidity = _safe_float(quote.get("bid1_volume") or quote.get("bid_volume") or quote.get("buy_volume"))
        reason = "跌停且无可见买盘" if liquidity <= 0 else "触及跌停价"
        return _reject(order, f"{reason} {lower:.2f}，模拟盘保守按不可成交处理", evaluated_at)
    if order.side == "BUY" and order.quantity % 100 != 0:
        return _reject(order, "A股买入数量必须为100股的整数倍", evaluated_at)

    position = (
        db.query(SimulationPosition)
        .filter(SimulationPosition.account_id == account.id, SimulationPosition.code == order.code)
        .first()
    )
    _roll_position(position, order.trade_date)
    t0_instrument = _is_t0_instrument(order.code, order.name, quote)
    exit_lots: list[SimulationTradeLot] = []
    if order.side == "SELL":
        if position is None or position.quantity <= 0:
            return _reject(order, "模拟账户无该标的持仓", evaluated_at)
        if order.quantity > position.available_quantity:
            return _reject(order, "A股T+1限制：可卖数量不足", evaluated_at)
        if order.quantity % 100 != 0 and order.quantity != position.available_quantity:
            return _reject(order, "零股仅允许一次性卖出全部可用数量", evaluated_at)
        exit_lots = _ensure_lot_coverage(db, account, order, position, evaluated_at)

    if order.order_type == "LIMIT":
        marketable = order.limit_price >= price if order.side == "BUY" else order.limit_price <= price
        if not marketable:
            order.status = "OPEN"
            order.reject_reason = ""
            return order

    gross = round(price * order.quantity, 2)
    commission = _commission(account, gross)
    transfer_fee = round(gross * account.transfer_fee_rate, 2)
    stamp_tax = round(gross * account.stamp_tax_rate, 2) if order.side == "SELL" else 0.0
    if order.side == "BUY" and account.cash + 1e-6 < gross + commission + transfer_fee:
        return _reject(order, "模拟账户可用资金不足", evaluated_at)

    if position is None:
        position = SimulationPosition(
            account_id=account.id,
            code=order.code,
            name=order.name or str(quote.get("name") or order.code),
            last_rollover_date=order.trade_date,
        )
        db.add(position)
        db.flush()

    realized_pnl = 0.0
    if order.side == "BUY":
        old_cost = position.average_cost * position.quantity
        total_cost = gross + commission + transfer_fee
        position.quantity += order.quantity
        position.today_buy_quantity += order.quantity
        if t0_instrument:
            position.available_quantity += order.quantity
        position.average_cost = round((old_cost + total_cost) / position.quantity, 4)
        account.cash = round(account.cash - total_cost, 2)
        net_cash_flow = -total_cost
    else:
        realized_pnl = round(gross - commission - stamp_tax - transfer_fee - position.average_cost * order.quantity, 2)
        proceeds = gross - commission - stamp_tax - transfer_fee
        account.cash = round(account.cash + proceeds, 2)
        position.quantity -= order.quantity
        position.available_quantity -= order.quantity
        position.realized_pnl = round(position.realized_pnl + realized_pnl, 2)
        if position.quantity <= 0:
            position.quantity = 0
            position.available_quantity = 0
            position.today_buy_quantity = 0
            position.average_cost = 0
        net_cash_flow = proceeds

    position.market_price = price
    position.market_value = round(position.quantity * price, 2)
    position.unrealized_pnl = round(position.quantity * (price - position.average_cost), 2) if position.quantity else 0.0
    order.status = "FILLED"
    order.filled_quantity = order.quantity
    order.average_fill_price = price
    order.reject_reason = ""
    fill = SimulationFill(
        order_id=order.id,
        account_id=account.id,
        fill_evidence_snapshot_id=evidence.id,
        strategy_source=order.strategy_source,
        code=order.code,
        name=position.name,
        side=order.side,
        price=price,
        quantity=order.quantity,
        gross_amount=gross,
        commission=commission,
        stamp_tax=stamp_tax,
        transfer_fee=transfer_fee,
        net_cash_flow=round(net_cash_flow, 2),
        realized_pnl=realized_pnl,
        trade_date=order.trade_date,
        filled_at=evaluated_at,
    )
    db.add(fill)
    db.flush()
    if order.side == "BUY":
        _create_entry_lot(db, order, fill)
    else:
        _allocate_exit_to_lots(db, order, fill, exit_lots)
    db.add(position)
    db.add(account)
    return order


def submit_order(
    db: Session,
    account: SimulationAccount,
    payload: SimulationOrderCreate,
    *,
    now: datetime | None = None,
    quote_loader: QuoteLoader = quote_for_code,
) -> SimulationOrder:
    evaluated_at = shanghai_now_naive(now) if now is not None else _local_now()
    code = _normalize_code(payload.code)
    normalized_payload = payload.model_copy(update={"code": code})
    quote = quote_loader(code) or {}
    quote_at = _quote_time(quote, evaluated_at)
    if quote_at is not None and quote_at > evaluated_at:
        # A provider/cache must never inject a later observation into the
        # decision-time snapshot.  Preserve an explicit degraded marker and
        # wait for a later process call instead of accepting the payload.
        quote = {"note": "决策时点行情晚于提交时刻，已排除未来数据"}
    evidence = _capture_evidence(db, account, normalized_payload, quote, evaluated_at)
    order = SimulationOrder(
        account_id=account.id,
        decision_evidence_snapshot_id=evidence.id,
        strategy_source=normalized_payload.strategy_source,
        code=code,
        name=normalized_payload.name or str(quote.get("name") or code),
        side=normalized_payload.side,
        order_type=normalized_payload.order_type,
        limit_price=normalized_payload.limit_price,
        quantity=normalized_payload.quantity,
        status="PENDING",
        client_note=normalized_payload.client_note,
        trade_date=evaluated_at.date().isoformat(),
        submitted_at=evaluated_at,
        last_evaluated_at=evaluated_at,
    )
    db.add(order)
    db.flush()
    # Freeze the decision-time evidence only.  Filling against this same quote
    # would be a same-bar look-ahead.  The earliest eligible fill is evaluated
    # by process_open_orders from a strictly later minute bucket.
    if not _is_trading_session(evaluated_at):
        order.status = "REJECTED"
        order.reject_reason = "提交时点不在A股连续竞价时段；DAY委托未进入撮合队列"
    else:
        order.status = "OPEN"
        order.reject_reason = "等待下一分钟桶的真实行情撮合，禁止同K线成交"
    db.commit()
    db.refresh(order)
    return order


def process_open_orders(
    db: Session,
    account: SimulationAccount,
    *,
    now: datetime | None = None,
    quote_loader: QuoteLoader = quote_for_code,
) -> list[SimulationOrder]:
    evaluated_at = shanghai_now_naive(now) if now is not None else _local_now()
    candidate_ids = [
        row[0] for row in (
        db.query(SimulationOrder.id)
        .filter(
            SimulationOrder.account_id == account.id,
            SimulationOrder.status.in_(("OPEN", "PENDING")),
        )
        .order_by(SimulationOrder.submitted_at.asc(), SimulationOrder.id.asc())
        .all()
        )
    ]
    processed_ids: list[int] = []
    for order_id in candidate_ids:
        try:
            # Isolate any duplicate-fill/version race to this order.  A failed
            # flush rolls back only this savepoint, never earlier batch work.
            with db.begin_nested():
                claimed = (
                    db.query(SimulationOrder)
                    .filter(
                        SimulationOrder.id == order_id,
                        SimulationOrder.account_id == account.id,
                        SimulationOrder.status.in_(("OPEN", "PENDING")),
                    )
                    .update(
                        {
                            SimulationOrder.status: "PROCESSING",
                            SimulationOrder.last_evaluated_at: evaluated_at,
                        },
                        synchronize_session=False,
                    )
                )
                if claimed != 1:
                    continue
                db.flush()
                # PostgreSQL locks these rows. SQLite serializes the preceding
                # write; populate_existing avoids an identity-map OPEN copy.
                order = (
                    db.query(SimulationOrder)
                    .filter(SimulationOrder.id == order_id)
                    .populate_existing()
                    .with_for_update()
                    .one()
                )
                locked_account = (
                    db.query(SimulationAccount)
                    .filter(SimulationAccount.id == account.id)
                    .populate_existing()
                    .with_for_update()
                    .one()
                )
                existing_fill = db.query(SimulationFill).filter(
                    SimulationFill.order_id == order.id,
                ).first()
                if existing_fill is not None:
                    order.status = "FILLED"
                    order.filled_quantity = existing_fill.quantity
                    order.average_fill_price = existing_fill.price
                    order.reject_reason = ""
                    processed_ids.append(order.id)
                    continue
                if order.trade_date != evaluated_at.date().isoformat():
                    order.status = "EXPIRED"
                    order.reject_reason = "DAY委托仅当日有效，跨交易日自动过期"
                    processed_ids.append(order.id)
                    continue
                payload = SimulationOrderCreate(
                    strategy_source=order.strategy_source,
                    code=order.code,
                    name=order.name,
                    side=order.side,
                    order_type=order.order_type,
                    limit_price=order.limit_price,
                    quantity=order.quantity - order.filled_quantity,
                    client_note=order.client_note,
                )
                quote = quote_loader(order.code) or {}
                fill_evidence = _capture_evidence(db, locked_account, payload, quote, evaluated_at)
                _evaluate_order(db, locked_account, order, fill_evidence, quote, evaluated_at)
                db.flush()
                processed_ids.append(order.id)
        except IntegrityError:
            # begin_nested already restored this order to its savepoint.  Never
            # call db.rollback() here: that would destroy the whole batch.
            db.expire_all()
            current = db.query(SimulationOrder).filter(SimulationOrder.id == order_id).first()
            existing_fill = db.query(SimulationFill).filter(SimulationFill.order_id == order_id).first()
            if current is not None and existing_fill is not None:
                current.status = "FILLED"
                current.filled_quantity = existing_fill.quantity
                current.average_fill_price = existing_fill.price
                current.reject_reason = ""
            processed_ids.append(order_id)
    db.commit()
    if not processed_ids:
        return []
    return (
        db.query(SimulationOrder)
        .filter(SimulationOrder.id.in_(processed_ids))
        .order_by(SimulationOrder.submitted_at.asc(), SimulationOrder.id.asc())
        .all()
    )


def cancel_order(db: Session, account_id: int, order_id: int) -> SimulationOrder | None:
    order = db.query(SimulationOrder).filter(
        SimulationOrder.id == order_id,
        SimulationOrder.account_id == account_id,
    ).first()
    if order is None:
        return None
    if order.status != "OPEN":
        return order
    order.status = "CANCELED"
    order.reject_reason = "用户取消"
    order.last_evaluated_at = _local_now()
    db.commit()
    db.refresh(order)
    return order


def mark_to_market(
    db: Session,
    account: SimulationAccount,
    *,
    now: datetime | None = None,
    quote_loader: QuoteLoader = quote_for_code,
) -> SimulationDailyEquity:
    captured_at = shanghai_now_naive(now) if now is not None else _local_now()
    trade_date = captured_at.date().isoformat()
    positions = db.query(SimulationPosition).filter(
        SimulationPosition.account_id == account.id,
        SimulationPosition.quantity > 0,
    ).all()
    market_value = 0.0
    for position in positions:
        _roll_position(position, trade_date)
        quote = quote_loader(position.code) or {}
        price = _safe_float(quote.get("price")) or position.market_price or position.average_cost
        position.market_price = price
        position.market_value = round(position.quantity * price, 2)
        position.unrealized_pnl = round(position.quantity * (price - position.average_cost), 2)
        market_value += position.market_value
        db.add(position)
    total_equity = round(account.cash + market_value, 2)
    prior = (
        db.query(SimulationDailyEquity)
        .filter(SimulationDailyEquity.account_id == account.id, SimulationDailyEquity.trade_date < trade_date)
        .order_by(SimulationDailyEquity.trade_date.desc())
        .first()
    )
    prior_equity = prior.total_equity if prior else account.initial_cash
    peak = db.query(func.max(SimulationDailyEquity.total_equity)).filter(
        SimulationDailyEquity.account_id == account.id,
        SimulationDailyEquity.trade_date <= trade_date,
    ).scalar() or account.initial_cash
    peak = max(float(peak), account.initial_cash, total_equity)
    row = db.query(SimulationDailyEquity).filter(
        SimulationDailyEquity.account_id == account.id,
        SimulationDailyEquity.trade_date == trade_date,
    ).first()
    if row is None:
        row = SimulationDailyEquity(account_id=account.id, trade_date=trade_date)
    row.cash = round(account.cash, 2)
    row.market_value = round(market_value, 2)
    row.total_equity = total_equity
    row.daily_pnl = round(total_equity - prior_equity, 2)
    row.total_pnl = round(total_equity - account.initial_cash, 2)
    row.return_pct = round((total_equity / account.initial_cash - 1) * 100, 4) if account.initial_cash else 0
    row.drawdown_pct = round((total_equity / peak - 1) * 100, 4) if peak else 0
    row.captured_at = captured_at
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _slice(key: str, rows: list[SimulationClosedTrade]) -> dict[str, Any]:
    pnls = [float(row.realized_pnl or 0) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "key": key,
        "closed_trade_count": len(pnls),
        # Backward-compatible response key; its semantics are now completed
        # round trips, not individual sell fills.
        "sell_count": len(pnls),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(pnls) * 100, 2) if pnls else 0.0,
        "total_realized_pnl": round(sum(pnls), 2),
        "average_win": round(avg_win, 2),
        "average_loss": round(avg_loss, 2),
        "profit_loss_ratio": round(avg_win / abs(avg_loss), 4) if avg_win > 0 and avg_loss < 0 else 0.0,
    }


def performance_report(db: Session, account: SimulationAccount) -> dict[str, Any]:
    closed_trades = db.query(SimulationClosedTrade).filter(
        SimulationClosedTrade.account_id == account.id,
    ).order_by(SimulationClosedTrade.closed_at.asc(), SimulationClosedTrade.id.asc()).all()
    # Attribute each completed round trip to its entry facts. Partial exits
    # remain accumulated on the lot and never inflate the sample count.
    evidence_ids = [
        row.entry_decision_evidence_snapshot_id
        for row in closed_trades if row.entry_decision_evidence_snapshot_id is not None
    ]
    evidence = {
        row.id: row for row in db.query(SimulationEvidenceSnapshot)
        .filter(SimulationEvidenceSnapshot.id.in_(evidence_ids or [0])).all()
    }
    by_strategy: dict[str, list[SimulationClosedTrade]] = defaultdict(list)
    by_regime: dict[str, list[SimulationClosedTrade]] = defaultdict(list)
    by_gap: dict[str, list[SimulationClosedTrade]] = defaultdict(list)
    for closed_trade in closed_trades:
        snapshot = evidence.get(closed_trade.entry_decision_evidence_snapshot_id)
        by_strategy[closed_trade.strategy_source or "unknown"].append(closed_trade)
        by_regime[(snapshot.market_regime if snapshot else "UNKNOWN") or "UNKNOWN"].append(closed_trade)
        by_gap[(snapshot.expectation_gap_band if snapshot else "unknown") or "unknown"].append(closed_trade)
    overall = _slice("overall", closed_trades)
    equities = db.query(SimulationDailyEquity).filter(
        SimulationDailyEquity.account_id == account.id,
    ).order_by(SimulationDailyEquity.trade_date.asc()).all()
    maximum_drawdown = abs(min((float(row.drawdown_pct or 0) for row in equities), default=0.0))
    return {
        "account_id": account.id,
        "closed_trade_count": overall["closed_trade_count"],
        "sell_count": overall["sell_count"],
        "win_count": overall["win_count"],
        "loss_count": overall["loss_count"],
        "win_rate": overall["win_rate"],
        "total_realized_pnl": overall["total_realized_pnl"],
        "profit_loss_ratio": overall["profit_loss_ratio"],
        "maximum_drawdown_pct": round(maximum_drawdown, 4),
        "by_strategy": [_slice(key, value) for key, value in sorted(by_strategy.items())],
        "by_market_regime": [_slice(key, value) for key, value in sorted(by_regime.items())],
        "by_expectation_gap": [_slice(key, value) for key, value in sorted(by_gap.items())],
    }
