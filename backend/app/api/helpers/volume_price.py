from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.quotes import _estimated_vwap, _is_realtime_note, _quote_lookup_code, _safe_float
from app.models.trading import VolumePriceSnapshot
from app.schemas.trading import VolumePriceSnapshotOut


def _today() -> str:
    return datetime.now().date().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _trading_elapsed_ratio(now: datetime | None = None) -> float:
    now = now or datetime.now()
    current = now.time()
    minutes = 0
    if current <= time(9, 30):
        return 0.05
    if current >= time(15, 0):
        return 1.0
    if current <= time(11, 30):
        minutes = (now.hour - 9) * 60 + now.minute - 30
    elif current < time(13, 0):
        minutes = 120
    else:
        minutes = 120 + (now.hour - 13) * 60 + now.minute
    return min(1.0, max(minutes / 240, 0.05))


def _fallback_vwap(price: float, high_price: float, low_price: float) -> float:
    values = [value for value in (high_price, low_price, price) if value > 0]
    return round(sum(values) / len(values), 4) if values else 0


def _minute_vwap(quote: dict[str, Any]) -> tuple[float, int]:
    rows = quote.get("minute_bars") or quote.get("minutes") or quote.get("minute_data") or []
    total_amount = 0.0
    total_volume = 0.0
    count = 0
    if not isinstance(rows, list):
        return 0.0, 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        volume = _safe_float(row.get("volume"))
        amount = _safe_float(row.get("amount"))
        price = _safe_float(row.get("price") or row.get("close"))
        if amount <= 0 and price > 0 and volume > 0:
            amount = price * volume
        if amount > 0 and volume > 0:
            total_amount += amount
            total_volume += volume
            count += 1
    return (round(total_amount / total_volume, 4), count) if total_amount > 0 and total_volume > 0 else (0.0, count)


def _classify_pattern(
    *,
    price: float,
    change_pct: float,
    amount: float,
    vwap: float,
    price_vs_vwap: float,
    high_drawdown: float,
) -> tuple[str, list[str], list[str]]:
    evidence: list[str] = []
    counter: list[str] = []
    if price <= 0:
        return "行情缺口", ["实时价格缺失，不能生成量价结论。"], []
    if vwap <= 0:
        return "量价待确认", ["VWAP 缺失，只能使用价格区间做弱判断。"], []
    if high_drawdown >= 4 and price_vs_vwap < 0:
        evidence.append(f"相对日内高点回撤 {high_drawdown:.2f}%，且低于VWAP {abs(price_vs_vwap):.2f}%。")
        return "冲高回落跌破VWAP", evidence, counter
    if price_vs_vwap < -1:
        evidence.append(f"当前价低于VWAP {abs(price_vs_vwap):.2f}%，盘中承接偏弱。")
        return "跌破VWAP", evidence, counter
    if high_drawdown >= 4:
        evidence.append(f"相对日内高点回撤 {high_drawdown:.2f}%，但尚未明显跌破VWAP。")
        return "冲高回落", evidence, counter
    if price_vs_vwap >= 2 and change_pct > 0:
        evidence.append(f"当前价高于VWAP {price_vs_vwap:.2f}%，涨幅 {change_pct:+.2f}%。")
        return "VWAP上方强势", evidence, counter
    if change_pct > 0 and amount > 0:
        evidence.append(f"上涨 {change_pct:+.2f}%，成交额 {amount:.2f} 亿，需继续确认持续性。")
        return "放量上涨待确认", evidence, counter
    if change_pct < 0 and price_vs_vwap < 0:
        evidence.append(f"下跌 {change_pct:+.2f}%，且低于VWAP。")
        return "量价转弱", evidence, counter
    counter.append("价格、VWAP 和回撤未出现明确强弱偏离。")
    return "量价中性", evidence, counter


def _snapshot_out(row: VolumePriceSnapshot) -> VolumePriceSnapshotOut:
    return VolumePriceSnapshotOut(
        id=row.id,
        trade_date=row.trade_date,
        code=row.code,
        name=row.name,
        stage=row.stage,
        captured_at=row.captured_at,
        price=row.price,
        change_pct=row.change_pct,
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        prev_close=row.prev_close,
        volume=row.volume,
        amount=row.amount,
        estimated_full_day_amount=row.estimated_full_day_amount,
        turnover=row.turnover,
        volume_ratio=row.volume_ratio,
        vwap=row.vwap,
        vwap_source=getattr(row, "vwap_source", "estimated"),
        minute_bar_count=getattr(row, "minute_bar_count", 0),
        vwap_reliable=bool(getattr(row, "vwap_reliable", False)),
        price_vs_vwap=row.price_vs_vwap,
        high_drawdown=row.high_drawdown,
        active_buy_amount=row.active_buy_amount,
        active_sell_amount=row.active_sell_amount,
        attack_efficiency=row.attack_efficiency,
        volume_acceleration=row.volume_acceleration,
        pattern=row.pattern,
        data_quality=row.data_quality,
        data_source=row.data_source,
        evidence=_json_list(row.evidence_json),
        counter_evidence=_json_list(row.counter_evidence_json),
    )


def build_volume_price_snapshot(
    db: Session,
    code: str,
    name: str = "",
    stage: str = "盘中状态",
    quote: dict[str, Any] | None = None,
    persist: bool = True,
) -> VolumePriceSnapshotOut:
    from app.api.helpers.decision import quote_for_code

    quote = quote or quote_for_code(code)
    lookup_code = _quote_lookup_code(code, {code: quote}) if quote else code
    price = _safe_float(quote.get("price"))
    change_pct = _safe_float(quote.get("change_pct"))
    open_price = _safe_float(quote.get("open"))
    high_price = _safe_float(quote.get("high"))
    low_price = _safe_float(quote.get("low"))
    prev_close = _safe_float(quote.get("prev_close"))
    volume = _safe_float(quote.get("volume"))
    amount = _safe_float(quote.get("amount"))
    turnover = _safe_float(quote.get("turnover"))
    minute_vwap, minute_bar_count = _minute_vwap(quote)
    if minute_vwap > 0:
        vwap = minute_vwap
        vwap_source = "minute"
    elif _estimated_vwap(quote) > 0:
        vwap = _estimated_vwap(quote)
        vwap_source = "quote_estimated"
    else:
        vwap = _fallback_vwap(price, high_price, low_price)
        vwap_source = "range_estimated"
    vwap_reliable = vwap_source == "minute" and minute_bar_count >= 3
    price_vs_vwap = ((price - vwap) / vwap * 100) if price > 0 and vwap > 0 else 0
    high_drawdown = ((high_price - price) / high_price * 100) if high_price > 0 and price > 0 else 0
    estimated_full_day_amount = round(amount / _trading_elapsed_ratio(), 2) if amount > 0 else 0
    note = str(quote.get("note") or "")
    data_quality = "realtime" if quote and _is_realtime_note(note) else ("degraded" if quote else "manual")
    if quote and not vwap_reliable:
        data_quality = "degraded_vwap"
    data_source = note or ("实时行情" if quote else "无行情")
    pattern, evidence, counter = _classify_pattern(
        price=price,
        change_pct=change_pct,
        amount=amount,
        vwap=vwap,
        price_vs_vwap=price_vs_vwap,
        high_drawdown=high_drawdown,
    )
    if amount > 0:
        evidence.append(f"当前成交额 {amount:.2f} 亿，按交易进度估算全天 {estimated_full_day_amount:.2f} 亿。")
    if turnover > 0:
        evidence.append(f"换手率 {turnover:.2f}%。")
    if not vwap_reliable:
        counter.append("缺少真实1分钟成交数据，VWAP为估算值，不能作为确定性减仓、清仓或做T触发。")
    elif data_quality != "realtime":
        counter.append("行情源不是实时可信状态，量价结论需要人工复核。")

    row = VolumePriceSnapshot(
        trade_date=_today(),
        code=lookup_code,
        name=name or str(quote.get("name") or code),
        stage=stage,
        price=round(price, 4),
        change_pct=round(change_pct, 4),
        open_price=round(open_price, 4),
        high_price=round(high_price, 4),
        low_price=round(low_price, 4),
        prev_close=round(prev_close, 4),
        volume=round(volume, 2),
        amount=round(amount, 2),
        estimated_full_day_amount=estimated_full_day_amount,
        turnover=round(turnover, 2),
        volume_ratio=round(_safe_float(quote.get("volume_ratio")), 2),
        vwap=round(vwap, 4),
        vwap_source=vwap_source,
        minute_bar_count=minute_bar_count,
        vwap_reliable=vwap_reliable,
        price_vs_vwap=round(price_vs_vwap, 2),
        high_drawdown=round(high_drawdown, 2),
        active_buy_amount=round(_safe_float(quote.get("active_buy_amount")), 2),
        active_sell_amount=round(_safe_float(quote.get("active_sell_amount")), 2),
        attack_efficiency=round(_safe_float(quote.get("attack_efficiency")), 2),
        volume_acceleration=round(_safe_float(quote.get("volume_acceleration")), 2),
        pattern=pattern,
        data_quality=data_quality,
        data_source=data_source,
        evidence_json=_json_dumps(evidence),
        counter_evidence_json=_json_dumps(counter),
    )
    if persist:
        db.add(row)
        db.commit()
        db.refresh(row)
    return _snapshot_out(row)
