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


def _minute_rows(quote: dict[str, Any]) -> list[dict[str, Any]]:
    rows = quote.get("minute_bars") or quote.get("minutes") or quote.get("minute_data") or []
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _minute_amount(row: dict[str, Any]) -> float:
    volume = _safe_float(row.get("volume"))
    amount = _safe_float(row.get("amount"))
    price = _safe_float(row.get("price") or row.get("close"))
    if amount <= 0 and price > 0 and volume > 0:
        amount = price * volume
    return amount


def _minute_vwap(quote: dict[str, Any]) -> tuple[float, int]:
    rows = _minute_rows(quote)
    total_amount = 0.0
    total_volume = 0.0
    count = 0
    for row in rows:
        volume = _safe_float(row.get("volume"))
        amount = _minute_amount(row)
        if amount > 0 and volume > 0:
            total_amount += amount
            total_volume += volume
            count += 1
    return (round(total_amount / total_volume, 4), count) if total_amount > 0 and total_volume > 0 else (0.0, count)


def _minute_flow_metrics(quote: dict[str, Any]) -> tuple[float, float, float, float, float, float, float, float, list[str]]:
    rows = _minute_rows(quote)
    evidence: list[str] = []
    if len(rows) < 3:
        return (
            _safe_float(quote.get("active_buy_amount")),
            _safe_float(quote.get("active_sell_amount")),
            _safe_float(quote.get("attack_efficiency")),
            _safe_float(quote.get("volume_acceleration")),
            _safe_float(quote.get("attack_amount")),
            _safe_float(quote.get("pullback_amount")),
            _safe_float(quote.get("pullback_amount_ratio")),
            _safe_float(quote.get("pullback_sell_ratio")),
            evidence,
        )

    active_buy = 0.0
    active_sell = 0.0
    positive_amount = 0.0
    positive_price_gain = 0.0
    previous_price = _safe_float(rows[0].get("open") or rows[0].get("price") or rows[0].get("close"))
    volumes: list[float] = []
    highs = [_safe_float(row.get("high") or row.get("price") or row.get("close")) for row in rows]
    latest_price = _safe_float(rows[-1].get("price") or rows[-1].get("close"))
    peak_price = max([value for value in highs if value > 0], default=0.0)
    peak_index = max(range(len(highs)), key=lambda idx: highs[idx]) if any(highs) else 0
    attack_amount = 0.0
    pullback_amount = 0.0
    pullback_sell_amount = 0.0

    for index, row in enumerate(rows):
        amount = _minute_amount(row)
        volume = _safe_float(row.get("volume"))
        volumes.append(volume)
        explicit_buy = _safe_float(row.get("active_buy_amount") or row.get("buy_amount"))
        explicit_sell = _safe_float(row.get("active_sell_amount") or row.get("sell_amount"))
        price = _safe_float(row.get("price") or row.get("close"))
        open_price = _safe_float(row.get("open")) or previous_price
        if explicit_buy > 0 or explicit_sell > 0:
            active_buy += explicit_buy
            active_sell += explicit_sell
            if index <= peak_index:
                attack_amount += amount or explicit_buy + explicit_sell
            else:
                pullback_amount += amount or explicit_buy + explicit_sell
                pullback_sell_amount += explicit_sell
        elif amount > 0 and price > 0:
            if price >= max(previous_price, open_price):
                active_buy += amount
                positive_amount += amount
                positive_price_gain += max(0.0, price - previous_price)
            else:
                active_sell += amount
            if index <= peak_index:
                attack_amount += amount
            else:
                pullback_amount += amount
                if price < previous_price or price < open_price:
                    pullback_sell_amount += amount
        if price > 0:
            previous_price = price

    recent = volumes[-3:]
    prior = volumes[:-3]
    recent_avg = sum(recent) / len(recent) if recent else 0.0
    prior_avg = sum(prior) / len(prior) if prior else 0.0
    volume_acceleration = ((recent_avg - prior_avg) / prior_avg * 100) if prior_avg > 0 else 0.0
    attack_efficiency = positive_price_gain / positive_amount * 10000 if positive_amount > 0 else 0.0
    pullback_pct = ((peak_price - latest_price) / peak_price * 100) if peak_price > 0 and latest_price > 0 else 0.0

    if active_buy or active_sell:
        evidence.append(f"分钟主动买卖额：主动买 {active_buy:.2f}，主动卖 {active_sell:.2f}。")
    if attack_efficiency:
        evidence.append(f"上攻效率 {attack_efficiency:.2f}，量能加速度 {volume_acceleration:+.2f}%。")
    pullback_ratio = (pullback_amount / attack_amount * 100) if attack_amount > 0 else 0.0
    sell_ratio = (pullback_sell_amount / pullback_amount * 100) if pullback_amount > 0 else 0.0
    if attack_amount > 0 or pullback_amount > 0:
        evidence.append(f"上攻段成交额 {attack_amount:.2f}，回落段成交额 {pullback_amount:.2f}（{pullback_ratio:.1f}%），回落段卖出占比 {sell_ratio:.1f}%。")
    if pullback_pct >= 2 and active_sell > active_buy:
        evidence.append(f"高点回落 {pullback_pct:.2f}% 且回落段卖出额占优，属于回落量能放大。")

    return active_buy, active_sell, attack_efficiency, volume_acceleration, attack_amount, pullback_amount, pullback_ratio, sell_ratio, evidence


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
        active_flow_source=getattr(row, "active_flow_source", "unavailable"),
        active_flow_estimated=bool(getattr(row, "active_flow_estimated", False)),
        ma5=getattr(row, "ma5", 0), ma10=getattr(row, "ma10", 0), ma20=getattr(row, "ma20", 0),
        return_5d=getattr(row, "return_5d", 0), return_10d=getattr(row, "return_10d", 0),
        distance_recent_high_pct=getattr(row, "distance_recent_high_pct", 0),
        historical_volume_ratio=getattr(row, "historical_volume_ratio", 0),
        chip_profit_ratio=getattr(row, "chip_profit_ratio", 0), chip_avg_cost=getattr(row, "chip_avg_cost", 0),
        chip_70_concentration=getattr(row, "chip_70_concentration", 0), chip_90_concentration=getattr(row, "chip_90_concentration", 0),
        chip_metrics_estimated=bool(getattr(row, "chip_metrics_estimated", True)),
        large_order_net_amount=getattr(row, "large_order_net_amount", 0),
        large_order_threshold=getattr(row, "large_order_threshold", 0),
        attack_efficiency=row.attack_efficiency,
        volume_acceleration=row.volume_acceleration,
        attack_amount=getattr(row, "attack_amount", 0),
        pullback_amount=getattr(row, "pullback_amount", 0),
        pullback_amount_ratio=getattr(row, "pullback_amount_ratio", 0),
        pullback_sell_ratio=getattr(row, "pullback_sell_ratio", 0),
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
    daily_metrics: dict[str, float] | None = None,
    persist: bool = True,
) -> VolumePriceSnapshotOut:
    from app.api.helpers.decision import quote_for_code

    from app.api.helpers.quotes import _daily_history_metrics
    quote = quote or quote_for_code(code)
    daily = daily_metrics if daily_metrics is not None else _daily_history_metrics(code)
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
    minute_amount_estimated = bool(quote.get("minute_amount_estimated"))
    if minute_vwap > 0:
        vwap = minute_vwap
        vwap_source = "minute_estimated" if minute_amount_estimated else "minute"
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
    (
        active_buy_amount,
        active_sell_amount,
        attack_efficiency,
        volume_acceleration,
        attack_amount,
        pullback_amount,
        pullback_amount_ratio,
        pullback_sell_ratio,
        flow_evidence,
    ) = _minute_flow_metrics(quote)
    minute_rows = _minute_rows(quote)
    has_explicit_active_flow = any(
        _safe_float(item.get("active_buy_amount") or item.get("buy_amount")) > 0
        or _safe_float(item.get("active_sell_amount") or item.get("sell_amount")) > 0
        for item in minute_rows
    )
    active_flow_source = "provider_tick_direction" if has_explicit_active_flow else ("minute_price_direction_estimate" if minute_rows else "unavailable")
    active_flow_estimated = bool(minute_rows) and not has_explicit_active_flow
    large_order_net_amount = sum(_safe_float(item.get("large_order_net_amount")) for item in minute_rows)
    large_order_threshold = max((_safe_float(item.get("large_order_threshold")) for item in minute_rows), default=0)
    recent_high = _safe_float(daily.get("recent_high"))
    distance_recent_high_pct = (price / recent_high - 1) * 100 if price > 0 and recent_high > 0 else 0
    historical_volume_ratio = (
        (_safe_float(quote.get("volume")) / 100) / _safe_float(daily.get("five_day_avg_volume"))
        if _safe_float(quote.get("volume")) > 0 and _safe_float(daily.get("five_day_avg_volume")) > 0 else 0
    )
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
    evidence.extend(flow_evidence)

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
        active_buy_amount=round(active_buy_amount, 2),
        active_sell_amount=round(active_sell_amount, 2),
        active_flow_source=active_flow_source,
        active_flow_estimated=active_flow_estimated,
        ma5=round(_safe_float(daily.get("ma5")), 4), ma10=round(_safe_float(daily.get("ma10")), 4), ma20=round(_safe_float(daily.get("ma20")), 4),
        return_5d=round(_safe_float(daily.get("return_5d")), 2), return_10d=round(_safe_float(daily.get("return_10d")), 2),
        distance_recent_high_pct=round(distance_recent_high_pct, 2), historical_volume_ratio=round(historical_volume_ratio, 2),
        chip_profit_ratio=round(_safe_float(daily.get("chip_profit_ratio")), 2), chip_avg_cost=round(_safe_float(daily.get("chip_avg_cost")), 4),
        chip_70_concentration=round(_safe_float(daily.get("chip_70_concentration")), 2), chip_90_concentration=round(_safe_float(daily.get("chip_90_concentration")), 2),
        chip_metrics_estimated=True,
        large_order_net_amount=round(large_order_net_amount / 1e8, 4),
        large_order_threshold=round(large_order_threshold, 2),
        attack_efficiency=round(attack_efficiency, 2),
        volume_acceleration=round(volume_acceleration, 2),
        attack_amount=round(attack_amount, 2),
        pullback_amount=round(pullback_amount, 2),
        pullback_amount_ratio=round(pullback_amount_ratio, 2),
        pullback_sell_ratio=round(pullback_sell_ratio, 2),
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
