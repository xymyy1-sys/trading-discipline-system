from datetime import datetime, time, timezone
from typing import Any
from sqlalchemy.orm import Session
from app.models.trading import AccountState, Holding, HoldingSyncBaseline, TradeLog
from app.schemas.trading import HoldingOut
from app.services.rules import profit_guard_price
from app.api.helpers.quotes import (
    _normalize_code,
    _quote_lookup_code,
    _latest_a_share_quotes,
    _is_realtime_note,
    _QUOTE_META_CACHE,
    _safe_float,
    _code_hint,
    _quote_code_candidates
)

def _trade_position_direction(side: str) -> int:
    text = str(side or "")
    if text in {"买入", "加仓"} or "买" in text or "加仓" in text:
        return 1
    if text in {"卖出", "减仓"} or "卖" in text or "减仓" in text:
        return -1
    return 0

def _trade_effect_data(trade: TradeLog) -> dict[str, Any]:
    return {
        "code": trade.code,
        "name": trade.name,
        "side": trade.side,
        "price": trade.price,
        "quantity": trade.quantity,
        "amount": trade.amount,
        "total_asset": trade.total_asset,
        "cost_price": trade.cost_price,
    }

def _find_holding_by_code(db: Session, code: str) -> Holding | None:
    normalized = _normalize_code(code)
    candidates = [normalized]
    stripped = normalized.lstrip("0")
    if stripped:
        candidates.append(stripped)
    return db.query(Holding).filter(Holding.code.in_(candidates)).first()

def _find_holding_sync_baseline(db: Session, code: str) -> HoldingSyncBaseline | None:
    normalized = _normalize_code(code)
    candidates = [normalized]
    stripped = normalized.lstrip("0")
    if stripped:
        candidates.append(stripped)
    return db.query(HoldingSyncBaseline).filter(HoldingSyncBaseline.code.in_(candidates)).first()

def _ensure_holding_sync_baselines(db: Session, codes: set[str]) -> None:
    for code in {_normalize_code(item) for item in codes if item}:
        if _find_holding_sync_baseline(db, code):
            continue
        holding = _find_holding_by_code(db, code)
        baseline = HoldingSyncBaseline(
            code=holding.code if holding else code,
            name=holding.name if holding else code,
            quantity=max(0, int(holding.quantity or 0)) if holding else 0,
            cost_price=float(holding.cost_price or 0) if holding else 0,
            current_price=float(holding.current_price or 0) if holding else 0,
            total_asset=float(holding.total_asset or _account_total_asset(db)) if holding else _account_total_asset(db),
            position_type=holding.position_type if holding else "交易同步基线仓",
            next_discipline=holding.next_discipline if holding else "",
        )
        db.add(baseline)
    db.flush()

def _restore_holding_from_baseline(baseline: HoldingSyncBaseline, db: Session) -> None:
    if int(baseline.quantity or 0) <= 0:
        return
    db.add(
        Holding(
            code=baseline.code,
            name=baseline.name,
            quantity=int(baseline.quantity or 0),
            cost_price=float(baseline.cost_price or 0),
            current_price=float(baseline.current_price or baseline.cost_price or 0),
            total_asset=float(baseline.total_asset or _account_total_asset(db)),
            position_type=baseline.position_type or "交易同步基线仓",
            next_discipline=baseline.next_discipline or "以首次同步前持仓为基线，叠加交易记录重算。",
        )
    )

def _apply_trade_to_holding(trade: TradeLog, db: Session, reverse: bool = False) -> None:
    _apply_trade_effect_to_holding(_trade_effect_data(trade), db, reverse=reverse)

def _apply_trade_effect_to_holding(data: dict[str, Any], db: Session, reverse: bool = False) -> None:
    direction = _trade_position_direction(str(data.get("side") or ""))
    if direction == 0:
        return
    if reverse:
        direction *= -1
    quantity = int(data.get("quantity") or 0)
    price = float(data.get("price") or 0)
    if quantity <= 0 or price <= 0:
        return
    amount = float(data.get("amount") or 0)
    cost_basis_price = amount / quantity if amount > 0 else price

    code = str(data.get("code") or "")
    name = str(data.get("name") or code)
    total_asset = float(data.get("total_asset") or _account_total_asset(db))
    holding = _find_holding_by_code(db, code)
    qty_delta = direction * quantity

    if qty_delta > 0:
        if holding is None:
            holding = Holding(
                code=code,
                name=name,
                quantity=qty_delta,
                cost_price=cost_basis_price,
                current_price=price,
                total_asset=total_asset,
                position_type="交易记录同步仓",
                next_discipline="由交易记录自动同步，刷新行情后重算盈亏。",
            )
            db.add(holding)
            db.flush()
            return
        old_qty = max(0, int(holding.quantity or 0))
        old_cost = float(holding.cost_price or cost_basis_price)
        new_qty = old_qty + qty_delta
        holding.quantity = new_qty
        holding.cost_price = round(((old_qty * old_cost) + (qty_delta * cost_basis_price)) / new_qty, 4)
        holding.current_price = price if not holding.current_price else holding.current_price
        holding.name = holding.name or name
        holding.total_asset = total_asset or holding.total_asset
        return

    if holding is None:
        return
    sell_qty = abs(qty_delta)
    old_qty = int(holding.quantity or 0)
    new_qty = old_qty - sell_qty
    if new_qty <= 0:
        db.delete(holding)
        return

    if reverse and _trade_position_direction(str(data.get("side") or "")) > 0:
        remaining_cost_amount = (old_qty * float(holding.cost_price or 0)) - (sell_qty * cost_basis_price)
        holding.cost_price = round(max(0, remaining_cost_amount) / new_qty, 4) if new_qty else 0
    holding.quantity = new_qty
    holding.current_price = price if not holding.current_price else holding.current_price
    holding.total_asset = total_asset or holding.total_asset

def _rebuild_holdings_from_trades(trades: list[TradeLog], db: Session, reset_codes: set[str] | None = None) -> list[str]:
    trade_codes = {_normalize_code(trade.code) for trade in trades if _trade_position_direction(trade.side)}
    if reset_codes:
        trade_codes.update(_normalize_code(code) for code in reset_codes if code)
    if not trade_codes:
        return ["没有可同步的买入/卖出/加仓/减仓交易记录。"]
    _ensure_holding_sync_baselines(db, trade_codes)
    lookup_codes = list(trade_codes | {code.lstrip("0") for code in trade_codes if code.lstrip("0")})
    for holding in db.query(Holding).filter(Holding.code.in_(lookup_codes)).all():
        db.delete(holding)
    db.flush()
    baselines = [
        item
        for item in db.query(HoldingSyncBaseline).filter(HoldingSyncBaseline.code.in_(lookup_codes)).all()
        if _normalize_code(item.code) in trade_codes
    ]
    for baseline in baselines:
        _restore_holding_from_baseline(baseline, db)
    db.flush()
    for trade in trades:
        if _normalize_code(trade.code) in trade_codes:
            _apply_trade_to_holding(trade, db)
    db.commit()
    baseline_count = sum(1 for item in baselines if int(item.quantity or 0) > 0)
    return [
        f"已按 {len(trades)} 条交易记录重算持仓；{baseline_count} 只股票保留首次同步前基线，买入/加仓叠加原仓，卖出/减仓扣减数量。"
    ]

def _account_state(db: Session) -> AccountState:
    state = db.get(AccountState, 1)
    if state is None:
        inferred_total_asset = (
            db.query(Holding.total_asset)
            .filter(Holding.total_asset > 0)
            .order_by(Holding.updated_at.desc())
            .limit(1)
            .scalar()
        )
        state = AccountState(id=1, total_asset=float(inferred_total_asset or 0.0))
        db.add(state)
        db.commit()
        db.refresh(state)
    return state

def _account_total_asset(db: Session) -> float:
    return _account_state(db).total_asset

def _refresh_holding_prices(holdings: list[Holding], db: Session) -> dict[str, str]:
    codes = [holding.code for holding in holdings if holding.code]
    if not codes:
        return {}
    try:
        quotes = _latest_a_share_quotes(codes)
    except Exception:
        return {code: "实时行情获取失败，暂用手动录入价。" for code in codes}

    notes: dict[str, str] = {}
    updated = False
    for holding in holdings:
        lookup_code = _quote_lookup_code(holding.code, quotes)
        quote = quotes.get(lookup_code)
        price = float(quote.get("price") or 0) if quote else 0
        if price > 0:
            if abs(price - holding.current_price) >= 0.001:
                holding.current_price = round(price, 2)
                updated = True
            normalized_note = str(quote.get("note") or "实时行情")
            if _normalize_code(holding.code) != lookup_code:
                normalized_note = f"{normalized_note}；原代码{holding.code}按{lookup_code}匹配"
            notes[holding.code] = normalized_note
            _QUOTE_META_CACHE[str(holding.code)] = quote
            _QUOTE_META_CACHE[lookup_code] = quote
        else:
            notes[holding.code] = f"未匹配到实时行情，暂用手动录入价。{_code_hint(holding.code)}"
    if updated:
        db.commit()
        for holding in holdings:
            db.refresh(holding)
    return notes

def _holding_out(holding: Holding, account_total_asset: float | None = None, price_note: str = "") -> HoldingOut:
    total_asset = account_total_asset if account_total_asset is not None else holding.total_asset
    market_value = holding.quantity * holding.current_price
    profit_amount = (holding.current_price - holding.cost_price) * holding.quantity
    profit_ratio = (
        (holding.current_price - holding.cost_price) / holding.cost_price
        if holding.cost_price
        else 0.0
    )
    position_ratio = market_value / total_asset if total_asset else 0.0
    data = holding.__dict__.copy()
    data.pop("_sa_instance_state", None)
    data["total_asset"] = total_asset
    is_realtime = _is_realtime_note(price_note)
    quote_meta = _QUOTE_META_CACHE.get(str(holding.code)) or _QUOTE_META_CACHE.get(_quote_lookup_code(holding.code, _QUOTE_META_CACHE))
    prev_close = _safe_float((quote_meta or {}).get("prev_close"))
    today_profit_amount = (holding.current_price - prev_close) * holding.quantity if prev_close else 0.0
    today_profit_ratio = (holding.current_price - prev_close) / prev_close if prev_close else 0.0
    return HoldingOut(
        **data,
        market_value=round(market_value, 2),
        profit_amount=round(profit_amount, 2),
        profit_ratio=round(profit_ratio, 4),
        today_profit_amount=round(today_profit_amount, 2),
        today_profit_ratio=round(today_profit_ratio, 4),
        position_ratio=round(position_ratio, 4),
        stop_loss_price=round(holding.cost_price * 0.96, 2),
        profit_guard_price=profit_guard_price(holding.cost_price, holding.current_price),
        price_source="realtime" if is_realtime else "manual",
        price_note=price_note,
        prev_close=round(prev_close, 2),
        change_pct=round(_safe_float((quote_meta or {}).get("change_pct")), 2),
        amount=round(_safe_float((quote_meta or {}).get("amount")), 2),
        turnover=round(_safe_float((quote_meta or {}).get("turnover")), 2),
        open_price=round(_safe_float((quote_meta or {}).get("open")), 2),
        high_price=round(_safe_float((quote_meta or {}).get("high")), 2),
        low_price=round(_safe_float((quote_meta or {}).get("low")), 2),
    )

def _today_realized_profit(db: Session, now: datetime | None = None) -> float:
    """Include sell/reduce P/L even after a position has been fully closed."""
    now = now or datetime.now()
    day_start = datetime.combine(now.date(), time.min)
    day_end = datetime.combine(now.date(), time.max)
    rows = (
        db.query(TradeLog)
        .filter(TradeLog.traded_at >= day_start, TradeLog.traded_at <= day_end)
        .all()
    )
    return round(sum(
        (float(row.price or 0) - float(row.cost_price or 0)) * int(row.quantity or 0)
        for row in rows
        if _trade_position_direction(row.side) < 0
    ), 2)


def _holding_account_summary(holdings: list[HoldingOut], total_asset: float, db: Session | None = None) -> dict[str, float]:
    total_market_value = round(sum(item.market_value for item in holdings), 2)
    today_open_profit_amount = round(sum(item.today_profit_amount for item in holdings), 2)
    today_realized_profit_amount = _today_realized_profit(db) if db is not None else 0.0
    today_profit_amount = round(today_open_profit_amount + today_realized_profit_amount, 2)
    total_profit_amount = round(sum(item.profit_amount for item in holdings), 2)
    total_position_ratio = total_market_value / total_asset if total_asset else 0.0
    today_profit_ratio = today_profit_amount / total_asset if total_asset else 0.0
    total_profit_ratio = total_profit_amount / total_asset if total_asset else 0.0
    return {
        "total_asset": round(total_asset, 2),
        "cash_available": round(total_asset - total_market_value, 2) if total_asset else 0.0,
        "total_market_value": total_market_value,
        "total_position_ratio": round(total_position_ratio, 4),
        "today_profit_amount": today_profit_amount,
        "today_profit_ratio": round(today_profit_ratio, 4),
        "today_open_profit_amount": today_open_profit_amount,
        "today_realized_profit_amount": today_realized_profit_amount,
        "total_profit_amount": total_profit_amount,
        "total_profit_ratio": round(total_profit_ratio, 4),
    }
