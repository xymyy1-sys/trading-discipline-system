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
            "financing_net_buy": None,
            "financing_balance_ratio": None,
            "financing_net_buy_5d": None,
            "financing_net_buy_10d": None,
            "financing_net_buy_20d": None,
            "margin_as_of": "",
            "margin_realtime": False,
            "margin_score": None,
        }
    balance = _optional_float(_value(margin, "financing_balance"))
    net_buy = _optional_float(_value(margin, "financing_net_buy"))
    balance_ratio = _optional_float(_value(margin, "financing_balance_ratio"))
    net_buy_5d = _optional_float(_value(margin, "net_buy_5d", "financing_net_buy_5d"))
    net_buy_10d = _optional_float(_value(margin, "net_buy_10d", "financing_net_buy_10d"))
    net_buy_20d = _optional_float(_value(margin, "net_buy_20d", "financing_net_buy_20d"))
    margin_score = _weighted(
        (
            (_scaled(balance_ratio, 0.0, 10.0), 0.30),
            (_smooth_flow_score(net_buy, 8.0), 0.15),
            (_smooth_flow_score(net_buy_5d, 30.0), 0.20),
            (_smooth_flow_score(net_buy_10d, 60.0), 0.20),
            (_smooth_flow_score(net_buy_20d, 100.0), 0.15),
        )
    ) if any(value is not None for value in (balance_ratio, net_buy, net_buy_5d, net_buy_10d, net_buy_20d)) else None
    return {
        "financing_balance": _round_optional(balance),
        "financing_net_buy": _round_optional(net_buy),
        "financing_balance_ratio": _round_optional(balance_ratio, 3),
        "financing_net_buy_5d": _round_optional(net_buy_5d),
        "financing_net_buy_10d": _round_optional(net_buy_10d),
        "financing_net_buy_20d": _round_optional(net_buy_20d),
        "margin_as_of": str(_value(margin, "as_of", "trade_date", default="") or "")[:10],
        # Even if an upstream field incorrectly says true, this public disclosure
        # must never be presented by this model as an intraday signal.
        "margin_realtime": False,
        "margin_score": _round_optional(margin_score),
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
    board_type: str,
) -> dict[str, Any]:
    name = _row_name(current or five_day or ten_day)
    change = _change(current)
    change_5d = _change(five_day)
    change_10d = _change(ten_day)
    current_net = _net(current)
    net_5d = _net(five_day)
    net_10d = _net(ten_day)
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
        "flow_speed": _round_optional(speed, 4),
        "flow_acceleration": _round_optional(acceleration, 6),
        "flow_turning": str(turning_raw or "") or None,
        "provider_trade_date": provider_trade_date or None,
        "provider_updated_at": provider_updated_at or None,
        "limit_up_count": limit_up_count,
        **{key: value for key, value in margin_fields.items() if key != "margin_score"},
        "evidence": evidence,
        "counter_evidence": counter_evidence,
        "actions": _actions_for(status),
        "data_quality": data_quality,
    }
    return item


def build_sector_temperature(
    current_rows: Iterable[Any] | None,
    five_day_rows: Iterable[Any] | None,
    ten_day_rows: Iterable[Any] | None,
    margin_by_name: Mapping[str, Any] | None = None,
    attention_by_name: Mapping[str, Any] | None = None,
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
