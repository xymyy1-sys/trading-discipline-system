from __future__ import annotations

"""Deterministic entry-discipline gate for planned and discretionary trades.

The gate is deliberately conservative.  A watch-list membership, an existing
holding, an oversold label, or a positive expectation is never sufficient on
its own to permit an order.  The caller must still provide a same-session plan,
confirm that the setup belongs to the configured trading mode, and supply
reliable minute/VWAP evidence.

All returned fields are JSON serialisable and can be passed directly to
``EntryDisciplineOut``.  ``allowed_position_ratio`` is expressed as a displayed
percentage (``5`` means five percent), matching the decision-card UI.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta, timezone
from math import isfinite
from typing import Any


SHANGHAI_TZ = timezone(timedelta(hours=8))
_EMPTY = (None, "", "-", "--")


def _payload(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if value is None:
        return {}
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        dumped = dumper()
        return dumped if isinstance(dumped, Mapping) else {}
    legacy_dumper = getattr(value, "dict", None)
    if callable(legacy_dumper):
        dumped = legacy_dumper()
        return dumped if isinstance(dumped, Mapping) else {}
    data = getattr(value, "__dict__", None)
    return data if isinstance(data, Mapping) else {}


def _number(value: Any, *keys: str) -> float | None:
    data = _payload(value)
    for key in keys:
        candidate = data.get(key)
        if candidate in _EMPTY or isinstance(candidate, bool):
            continue
        try:
            number = float(candidate)
        except (TypeError, ValueError):
            continue
        if isfinite(number):
            return number
    return None


def _text(value: Any, *keys: str) -> str:
    data = _payload(value)
    for key in keys:
        candidate = data.get(key)
        if candidate not in _EMPTY:
            return str(candidate).strip()
    return ""


def _boolean(value: Any, *keys: str) -> bool | None:
    data = _payload(value)
    for key in keys:
        candidate = data.get(key)
        if isinstance(candidate, bool):
            return candidate
        if candidate in (0, 1):
            return bool(candidate)
        if isinstance(candidate, str):
            normalized = candidate.strip().lower()
            if normalized in {"true", "yes", "y", "1", "是", "允许"}:
                return True
            if normalized in {"false", "no", "n", "0", "否", "禁止"}:
                return False
    return None


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(SHANGHAI_TZ).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
    return value


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
    return parsed


def _continuous_session(value: datetime) -> bool:
    if value.weekday() >= 5:
        return False
    current = value.time()
    return time(9, 30) <= current < time(11, 30) or time(13, 0) <= current < time(15, 0)


def _minute_event_at(bars: list[dict[str, Any]], trade_date: str) -> datetime | None:
    if not bars or not trade_date:
        return None
    raw_time = str(bars[-1].get("time") or "").strip()
    if not raw_time:
        return None
    try:
        if "T" in raw_time or " " in raw_time:
            return _datetime(raw_time)
        return datetime.fromisoformat(f"{trade_date}T{raw_time[:8]}")
    except ValueError:
        return None


def _bar_price(row: Mapping[str, Any]) -> float | None:
    return _number(row, "price", "close", "last")


def _valid_bars(quote: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = quote.get("minute_bars")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return []
    bars: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        price = _bar_price(item)
        if price is None or price <= 0:
            continue
        high = _number(item, "high") or price
        low = _number(item, "low") or price
        volume = _number(item, "volume", "vol")
        amount = _number(item, "amount", "turnover_amount")
        bars.append(
            {
                "time": str(item.get("time") or item.get("datetime") or ""),
                "price": price,
                "high": max(price, high),
                "low": min(price, low),
                "volume": volume,
                "amount": amount,
                "trade_date": str(item.get("trade_date") or "")[:10],
                "amount_estimated": bool(item.get("amount_estimated")),
            }
        )
    return bars


def _pct(current: float | None, base: float | None) -> float | None:
    if current is None or base is None or base <= 0:
        return None
    return round((current / base - 1) * 100, 3)


def _pulse(prices: list[float], minutes: int) -> float | None:
    if len(prices) <= minutes:
        return None
    return _pct(prices[-1], prices[-1 - minutes])


def _append_unique(target: list[str], value: str) -> None:
    if value and value not in target:
        target.append(value)


def _reason(reason_codes: list[str], evidence: list[str], code: str, text: str) -> None:
    _append_unique(reason_codes, code)
    _append_unique(evidence, text)


def _contains_any(value: str, tokens: set[str]) -> bool:
    normalized = value.upper().replace("-", "_").replace(" ", "_")
    return any(token in normalized for token in tokens)


def _expectation_is_negative(expectation: Mapping[str, Any]) -> bool:
    gap = _number(expectation, "expectation_gap_score", "gap_score")
    result = " ".join(
        _text(expectation, key)
        for key in ("expectation_result", "state_transition", "actual_performance", "status")
    )
    negative_tokens = {
        "WEAKER",
        "SEVERE_UNDERPERFORM",
        "INVALIDATED",
        "EXPECTATION_INVALIDATED",
        "预期证伪",
        "弱于预期",
        "转弱",
    }
    return bool((gap is not None and gap <= -8) or _contains_any(result, negative_tokens))


def _expectation_is_positive(expectation: Mapping[str, Any]) -> bool:
    gap = _number(expectation, "expectation_gap_score", "gap_score")
    result = " ".join(
        _text(expectation, key)
        for key in ("expectation_result", "state_transition", "actual_performance", "status")
    )
    positive_tokens = {"STRONGER", "OUTPERFORM", "WEAK_TO_STRONG", "超预期", "弱转强", "强于预期"}
    return bool((gap is not None and gap >= 6) or _contains_any(result, positive_tokens))


def _market_blocked(market: Mapping[str, Any]) -> bool:
    if _boolean(market, "expansion_frozen", "hard_blocked", "entry_blocked") is True:
        return True
    value = " ".join(
        _text(market, key)
        for key in ("entry_gate", "market_gate", "risk_level", "regime", "status")
    )
    tokens = {
        "BLOCK",
        "FROZEN",
        "EXTREME_RISK",
        "LIQUIDITY_STRESS",
        "PANIC",
        "禁止开仓",
        "冻结扩仓",
        "流动性危机",
    }
    return _contains_any(value, tokens)


def _sector_overheated(sector: Mapping[str, Any]) -> bool:
    if _boolean(sector, "overheated", "extreme_heat") is True:
        return True
    score = _number(sector, "heat_score", "temperature_score", "crowding_score")
    status = " ".join(_text(sector, key) for key in ("heat_status", "temperature", "phase", "status"))
    return bool(score is not None and score >= 75) or _contains_any(
        status,
        {"OVERHEATED", "EXTREME_HEAT", "HIGH_CROWDING", "过热", "高位拥挤", "极热"},
    )


def _sector_weakening(sector: Mapping[str, Any]) -> bool:
    value = " ".join(_text(sector, key) for key in ("flow_turning", "turning", "signal", "flow_signal"))
    return _contains_any(
        value,
        {"TURN_TO_OUTFLOW", "INFLOW_FADING", "OUTFLOW_ACCELERATING", "FLOW_WEAKENING", "流入转流出", "加速流出"},
    )


def _sector_supportive(sector: Mapping[str, Any]) -> bool:
    value = " ".join(_text(sector, key) for key in ("flow_turning", "turning", "signal", "flow_signal", "status"))
    return _contains_any(
        value,
        {"TURN_TO_INFLOW", "INFLOW_ACCELERATING", "FLOW_IMPROVING", "STABILIZING", "转为流入", "加速流入", "企稳"},
    )


def _volume_contraction_on_spike(bars: list[dict[str, float | str | None]], volume_ratio: float | None) -> bool:
    if volume_ratio is not None and 0 < volume_ratio <= 0.8:
        return True
    if len(bars) < 6:
        return False
    previous = [float(item["volume"]) for item in bars[-6:-3] if item.get("volume") not in _EMPTY]
    recent = [float(item["volume"]) for item in bars[-3:] if item.get("volume") not in _EMPTY]
    if len(previous) < 2 or len(recent) < 2:
        return False
    previous_average = sum(previous) / len(previous)
    recent_average = sum(recent) / len(recent)
    return previous_average > 0 and recent_average <= previous_average * 0.78


def _retest_state(
    bars: list[dict[str, float | str | None]],
    *,
    vwap: float | None,
) -> tuple[bool, list[str]]:
    """Confirm a pullback and recovery, never just a low/oversold reading."""

    if len(bars) < 7 or vwap is None or vwap <= 0:
        return False, []
    current = float(bars[-1]["price"])
    recent = bars[-6:]
    evidence: list[str] = []

    # VWAP retest: a completed bar before the latest one came back to the
    # average, did not break it materially, and price subsequently recovered.
    touch_candidates = [
        (index, float(item["low"]), float(item["price"]))
        for index, item in enumerate(recent[:-1])
        if float(item["low"]) <= vwap * 1.006 and float(item["low"]) >= vwap * 0.992
    ]
    vwap_retest = False
    if touch_candidates:
        _, touched_low, touched_close = touch_candidates[-1]
        vwap_retest = current >= vwap * 1.002 and current >= max(touched_low, touched_close) * 1.003
        if vwap_retest:
            evidence.append(f"价格回踩分时均价 {vwap:.2f} 附近后重新站回，最新价 {current:.2f}。")

    # Breakout retest: establish the resistance level before the latest six
    # bars, then require a breakout, a later touch, and a recovery in sequence.
    breakout_retest = False
    earlier = bars[:-6]
    if len(earlier) >= 3:
        prior_high = max(float(item["high"]) for item in earlier)
        breakout_index = next(
            (index for index, item in enumerate(recent[:-1]) if float(item["high"]) >= prior_high * 1.002),
            None,
        )
        if breakout_index is not None:
            later = recent[breakout_index + 1 : -1]
            held = any(
                prior_high * 0.994 <= float(item["low"]) <= prior_high * 1.006
                and float(item["price"]) >= prior_high * 0.997
                for item in later
            )
            breakout_retest = held and current >= prior_high * 1.003
            if breakout_retest:
                evidence.append(f"突破位 {prior_high:.2f} 已完成回踩承接并重新抬高。")

    return vwap_retest or breakout_retest, evidence


def evaluate_entry_gate(
    code: str,
    quote: Mapping[str, Any] | Any,
    expectation: Mapping[str, Any] | Any = None,
    volume_price: Mapping[str, Any] | Any = None,
    consensus_risk: Mapping[str, Any] | Any = None,
    sector_context: Mapping[str, Any] | Any = None,
    market_context: Mapping[str, Any] | Any = None,
    *,
    is_holding: bool = False,
    has_plan: bool = False,
    mode_match: bool = False,
    plan_triggered: bool | None = None,
    risk_reward_passed: bool | None = None,
    plan_position_cap_pct: float | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate whether a new buy/add order is currently permitted.

    ``BLOCK`` is a hard zero-position gate. ``WAIT_RETEST`` means the setup may
    be reviewed later but must not be ordered now. ``ALLOW_SMALL`` and
    ``ALLOW`` require a completed retest plus the plan/mode/data gates; they are
    not predictions and never trigger an order automatically.
    """

    evaluated_at = _now(now)
    quote_data = _payload(quote)
    expectation_data = _payload(expectation)
    volume_data = _payload(volume_price)
    consensus_data = _payload(consensus_risk)
    sector_data = _payload(sector_context)
    market_data = _payload(market_context)

    bars = _valid_bars(quote_data)
    prices = [float(item["price"]) for item in bars]
    quote_current = _number(quote_data, "price", "current_price", "last")
    bar_current = prices[-1] if prices else None
    current = quote_current or bar_current
    pulse_1m = _pulse(prices, 1)
    pulse_3m = _pulse(prices, 3)
    pulse_5m = _pulse(prices, 5)

    minute_trade_date = _text(quote_data, "minute_bar_trade_date")
    if not minute_trade_date:
        bar_dates = {str(item.get("trade_date") or "")[:10] for item in bars if item.get("trade_date")}
        if len(bar_dates) == 1:
            minute_trade_date = next(iter(bar_dates))
    minute_date_missing = not bool(minute_trade_date)
    minute_stale = bool(minute_trade_date and minute_trade_date != evaluated_at.date().isoformat())
    minute_estimated = bool(
        _boolean(quote_data, "minute_amount_estimated") is True
        or any(bool(item.get("amount_estimated")) for item in bars)
    )
    quote_bar_gap = _pct(bar_current, quote_current)
    minute_quote_mismatch = bool(quote_bar_gap is not None and abs(quote_bar_gap) > 1.0)
    minute_event_at = _minute_event_at(bars, minute_trade_date)
    minute_age_seconds = (
        (evaluated_at - minute_event_at).total_seconds()
        if minute_event_at is not None
        else None
    )
    minute_timestamp_missing = minute_event_at is None
    minute_intraday_stale = bool(
        minute_age_seconds is not None
        and (minute_age_seconds > 180 or minute_age_seconds < -60)
    )
    quote_event_at = _datetime(quote_data.get("provider_event_at"))
    quote_age_seconds = (
        (evaluated_at - quote_event_at).total_seconds()
        if quote_event_at is not None
        else _number(quote_data, "age_seconds")
    )
    quote_timestamp_missing = quote_age_seconds is None
    quote_stale = bool(
        quote_age_seconds is not None
        and (quote_age_seconds > 180 or quote_age_seconds < -60)
    )
    outside_session = not _continuous_session(evaluated_at)
    minute_source_reliable = not any(
        (
            minute_date_missing,
            minute_stale,
            minute_estimated,
            minute_quote_mismatch,
            minute_timestamp_missing,
            minute_intraday_stale,
            quote_timestamp_missing,
            quote_stale,
            outside_session,
        )
    )

    snapshot_vwap = _number(volume_data, "vwap")
    vwap_reliable = _boolean(volume_data, "vwap_reliable") is True and minute_source_reliable
    cumulative_amount = sum(float(item["amount"]) for item in bars if item.get("amount") not in _EMPTY)
    cumulative_volume = sum(float(item["volume"]) for item in bars if item.get("volume") not in _EMPTY)
    complete_amount_bars = bool(bars) and all(item.get("amount") not in _EMPTY for item in bars)
    complete_volume_bars = bool(bars) and all(item.get("volume") not in _EMPTY for item in bars)
    calculated_vwap = (
        cumulative_amount / cumulative_volume
        if cumulative_volume > 0 and complete_amount_bars and minute_source_reliable
        else None
    )
    vwap = snapshot_vwap if vwap_reliable and snapshot_vwap and snapshot_vwap > 0 else calculated_vwap
    if calculated_vwap is not None and complete_volume_bars and len(bars) >= 6:
        vwap_reliable = True

    high = _number(volume_data, "high_price") or _number(quote_data, "high", "high_price")
    if bars:
        bar_high = max(float(item["high"]) for item in bars)
        high = max(high or 0, bar_high)
    distance_vwap_pct = _pct(current, vwap)
    # Negative means below the current session high; zero means at the high.
    distance_high_pct = _pct(current, high)

    reason_codes: list[str] = []
    evidence: list[str] = []
    counter_evidence: list[str] = []
    missing_conditions: list[str] = []
    recheck_conditions: list[str] = []
    score = 0

    enough_bars = len(bars) >= 6 and pulse_3m is not None and pulse_5m is not None
    if not current or current <= 0:
        _reason(reason_codes, evidence, "MISSING_CURRENT_PRICE", "缺少有效现价，禁止用猜测替代真实行情。")
        missing_conditions.append("有效现价")
    if not enough_bars:
        _reason(reason_codes, evidence, "INSUFFICIENT_MINUTE_BARS", f"只有 {len(bars)} 根有效分钟数据，尚不能验证3至5分钟脉冲和回踩。")
        missing_conditions.append("至少6根连续、真实的分钟数据")
    if not vwap_reliable or vwap is None or vwap <= 0:
        _reason(reason_codes, evidence, "VWAP_UNRELIABLE", "真实分钟成交额不足，分时均价不可用于入场确认。")
        missing_conditions.append("可靠的真实分时均价")
    if minute_stale:
        _reason(reason_codes, evidence, "STALE_MINUTE_BARS", f"分钟数据交易日为 {minute_trade_date}，不是当前交易日，禁止据此判断入场。")
        missing_conditions.append("当前交易日的分钟量价")
    if minute_date_missing:
        _reason(reason_codes, evidence, "MISSING_MINUTE_TRADE_DATE", "分钟数据缺少可核验的交易日期，禁止按当前交易日处理。")
        missing_conditions.append("分钟线明确的交易日期")
    if minute_estimated:
        _reason(reason_codes, evidence, "ESTIMATED_MINUTE_AMOUNT", "分钟成交额为估算值，不能重算成真实分时均价并放行入场。")
        missing_conditions.append("未估算的真实分钟成交额")
    if minute_quote_mismatch:
        _reason(reason_codes, evidence, "MINUTE_QUOTE_MISMATCH", f"分钟线末价与最新报价偏差 {quote_bar_gap:+.2f}%，数据时点不一致。")
        missing_conditions.append("与最新报价一致的分钟线")
    if minute_timestamp_missing:
        _reason(reason_codes, evidence, "MISSING_MINUTE_TIMESTAMP", "分钟线缺少可核验的最后采样时间，无法确认是否仍然有效。")
        missing_conditions.append("分钟线最后采样时间")
    elif minute_intraday_stale:
        _reason(reason_codes, evidence, "STALE_MINUTE_TAPE", f"最后一分钟数据距当前 {minute_age_seconds / 60:.1f} 分钟，已超过3分钟入场时效。")
        missing_conditions.append("3分钟内的最新分钟量价")
    if quote_timestamp_missing:
        _reason(reason_codes, evidence, "MISSING_QUOTE_TIMESTAMP", "最新报价缺少交易所事件时间或可核验年龄，不能证明报价实时。")
        missing_conditions.append("带交易所时间戳的最新报价")
    elif quote_stale:
        _reason(reason_codes, evidence, "STALE_QUOTE", f"最新报价距当前 {quote_age_seconds / 60:.1f} 分钟，已超过3分钟入场时效。")
        missing_conditions.append("3分钟内的最新报价")
    if outside_session:
        _reason(reason_codes, evidence, "OUTSIDE_CONTINUOUS_SESSION", "当前不在A股连续竞价时段，纪律闸门禁止给出可下单结论。")
        missing_conditions.append("进入09:30-11:30或13:00-15:00连续竞价时段后重新验证")

    if not has_plan:
        _reason(reason_codes, evidence, "NO_TRADE_PLAN", "没有当日交易计划；观察池、临时冲高和已有持仓都不等于买点。")
        missing_conditions.append("明确的当日交易计划、触发价和失效条件")
    if not mode_match:
        _reason(reason_codes, evidence, "OUT_OF_TRADING_MODE", "当前机会不属于已定义交易模式，禁止因害怕踏空而随手下单。")
        missing_conditions.append("确认交易脚本与当前模式一致")
    if has_plan and plan_triggered is not True:
        _reason(reason_codes, evidence, "PLAN_TRIGGER_NOT_MET", "当日计划存在，但当前价格尚未触发计划买入/买回条件，继续等待。")
        missing_conditions.append("计划触发价、买回价和失效条件同时满足")
    if has_plan and risk_reward_passed is not True:
        _reason(reason_codes, evidence, "RISK_REWARD_NOT_PASSED", "按计划目标位与风险位计算，风险收益比未知或未达标，不允许下单。")
        missing_conditions.append("可核验且达标的计划风险收益比")
    if has_plan and plan_position_cap_pct is None:
        _reason(reason_codes, evidence, "PLAN_POSITION_CAP_MISSING", "计划仓位或买回数量无法换算为账户仓位上限，禁止使用默认仓位替代。")
        missing_conditions.append("可核验的计划仓位或最大买回数量")
    if _boolean(sector_data, "crowding_evaluated") is False:
        _reason(reason_codes, evidence, "SECTOR_CONTEXT_MISSING", "所属板块冷热、订单流方向拐点或拥挤度尚未取得可靠快照，禁止凭个股冲高追入。")
        missing_conditions.append("可靠的所属板块冷热与订单流方向拐点快照")
    if is_holding and (not has_plan or not mode_match):
        _append_unique(evidence, "已有持仓不构成加仓理由；加仓与新开仓使用同一纪律闸门。")

    expectation_negative = _expectation_is_negative(expectation_data)
    expectation_positive = _expectation_is_positive(expectation_data)
    if expectation_negative:
        _reason(reason_codes, evidence, "EXPECTATION_NEGATIVE", "实际表现显著弱于预期或预期已经证伪，禁止用补仓掩盖错误。")
        score += 15

    market_blocked = _market_blocked(market_data)
    if market_blocked:
        _reason(reason_codes, evidence, "MARKET_GATE_BLOCKED", "全市场风险闸门已关闭，当前环境不允许主动扩大仓位。")
        score += 15

    sector_hot = _sector_overheated(sector_data)
    sector_rolling_over = _sector_weakening(sector_data)
    sector_supportive = _sector_supportive(sector_data)
    if sector_hot:
        _reason(reason_codes, evidence, "SECTOR_OVERHEATED", "所属板块处于过热或高拥挤区，继续追价的赔率下降。")
        score += 15
    if sector_rolling_over:
        _reason(reason_codes, evidence, "SECTOR_FLOW_WEAKENING", "板块订单流方向估算由正转弱或负值加速，个股冲高缺少板块确认。")
        score += 15
    elif sector_supportive:
        counter_evidence.append("板块订单流方向估算边际改善，但仍不能替代个股回踩确认。")

    consensus_score = _number(consensus_data, "score")
    consensus_level = _text(consensus_data, "level", "risk_level").upper()
    consensus_high = consensus_level in {"HIGH", "CRITICAL", "EXTREME"} or bool(consensus_score is not None and consensus_score >= 60)
    if consensus_high:
        _reason(reason_codes, evidence, "CONSENSUS_CROWDING_HIGH", "一致性追涨风险较高，买盘拥挤时不抢最后一段脉冲。")
        score += 15

    near_high = distance_high_pct is not None and distance_high_pct >= -0.8
    far_above_vwap = distance_vwap_pct is not None and distance_vwap_pct >= 1.2
    pulse_3 = pulse_3m or 0
    pulse_5 = pulse_5m or 0
    last_six = prices[-6:]
    rising_steps = sum(1 for left, right in zip(last_six, last_six[1:]) if right >= left * 0.999)
    direct_line = enough_bars and rising_steps >= 4 and (pulse_3 >= 1.2 or pulse_5 >= 1.8)
    severe_pulse = pulse_3 >= 1.8 or pulse_5 >= 2.8
    moderate_pulse = pulse_3 >= 0.9 or pulse_5 >= 1.5

    if direct_line:
        _reason(reason_codes, evidence, "DIRECT_LINE_SURGE", f"最近5分钟有 {rising_steps} 段连续抬高，3分钟脉冲 {pulse_3:+.2f}%、5分钟脉冲 {pulse_5:+.2f}%。")
        score += 35
    elif moderate_pulse:
        _reason(reason_codes, evidence, "RAPID_PRICE_PULSE", f"3分钟脉冲 {pulse_3:+.2f}%、5分钟脉冲 {pulse_5:+.2f}%，需要先冷静等待。")
        score += 20
    if near_high:
        _reason(reason_codes, evidence, "NEAR_INTRADAY_HIGH", f"最新价距日内高点仅 {abs(distance_high_pct or 0):.2f}%，处在容易被踏空情绪放大的位置。")
        score += 15
    if far_above_vwap:
        _reason(reason_codes, evidence, "FAR_ABOVE_VWAP", f"最新价高于真实分时均价 {distance_vwap_pct:+.2f}%，安全垫不足。")
        score += 20

    volume_ratio = _number(volume_data, "volume_ratio", "historical_volume_ratio")
    shrinking_spike = moderate_pulse and _volume_contraction_on_spike(bars, volume_ratio)
    if shrinking_spike:
        _reason(reason_codes, evidence, "SHRINKING_SPIKE", "冲高阶段的成交量低于前段或量比不足，缩量冲高不能当作突破确认。")
        score += 20

    retest_confirmed, retest_evidence = _retest_state(bars, vwap=vwap if vwap_reliable else None)
    for item in retest_evidence:
        _append_unique(counter_evidence, item)
    if not retest_confirmed:
        _append_unique(reason_codes, "NO_RETEST_CONFIRMATION")
        missing_conditions.append("冲高后回踩分时均价或突破位不破，并重新抬高")
    else:
        _append_unique(counter_evidence, "已经出现可复核的回踩承接，不再把单纯冲高当作买点。")

    if sector_hot and (direct_line or near_high):
        score += 10
    if sector_rolling_over and moderate_pulse:
        score += 10
    if severe_pulse and near_high and far_above_vwap:
        score += 15
    score = int(max(0, min(100, round(score))))

    # These facts can never be repaired by a positive price pulse in the same
    # evaluation.  A new quote/plan/mode/expectation snapshot is required.
    hard_reasons = {
        "MISSING_CURRENT_PRICE",
        "INSUFFICIENT_MINUTE_BARS",
        "VWAP_UNRELIABLE",
        "STALE_MINUTE_BARS",
        "ESTIMATED_MINUTE_AMOUNT",
        "MINUTE_QUOTE_MISMATCH",
        "MISSING_MINUTE_TRADE_DATE",
        "MISSING_MINUTE_TIMESTAMP",
        "STALE_MINUTE_TAPE",
        "MISSING_QUOTE_TIMESTAMP",
        "STALE_QUOTE",
        "OUTSIDE_CONTINUOUS_SESSION",
        "NO_TRADE_PLAN",
        "OUT_OF_TRADING_MODE",
        "EXPECTATION_NEGATIVE",
        "MARKET_GATE_BLOCKED",
        "SECTOR_CONTEXT_MISSING",
        "PLAN_POSITION_CAP_MISSING",
    }
    hard_blocked = bool(hard_reasons.intersection(reason_codes))
    chase_blocked = bool(
        (direct_line and near_high and far_above_vwap)
        or (severe_pulse and near_high)
        or (score >= 70 and moderate_pulse)
    )
    if chase_blocked:
        hard_blocked = True
        _append_unique(reason_codes, "CHASE_RISK_HARD_BLOCK")
        _append_unique(evidence, "直线脉冲、接近日高或偏离均价形成追高共振，本轮评估下单上限为0。")

    supportive_count = sum(
        (
            retest_confirmed,
            expectation_positive,
            sector_supportive,
            not sector_hot,
            not sector_rolling_over,
            not consensus_high,
            distance_vwap_pct is not None and -0.2 <= distance_vwap_pct <= 1.0,
        )
    )
    if hard_blocked:
        decision = "BLOCK"
        allowed = 0.0
        risk_level = "HIGH"
        label = "禁止追高，立即冷静"
    elif plan_triggered is False or risk_reward_passed is False:
        decision = "WAIT_RETEST"
        allowed = 0.0
        risk_level = "MEDIUM"
        label = "计划触发或风险收益未通过，当前不下单"
    elif not retest_confirmed or direct_line or score >= 45:
        decision = "WAIT_RETEST"
        allowed = 0.0
        risk_level = "MEDIUM"
        label = "等待回踩确认，当前不下单"
    elif supportive_count >= 6 and score <= 20:
        decision = "ALLOW"
        allowed = 5.0 if is_holding else 10.0
        risk_level = "LOW"
        label = "纪律条件通过，仍须限仓"
    else:
        decision = "ALLOW_SMALL"
        allowed = 3.0 if is_holding else 5.0
        risk_level = "LOW" if score <= 25 else "MEDIUM"
        label = "回踩承接初步确认，仅允许试错仓"

    if allowed > 0 and plan_position_cap_pct is not None:
        cap = max(0.0, float(plan_position_cap_pct))
        allowed = min(allowed, cap)
        if cap <= 0:
            decision, risk_level, label = "WAIT_RETEST", "MEDIUM", "计划仓位上限为0，当前不下单"

    # Oversold/cold is context, never a permission.  Without a completed
    # retest the decision above remains WAIT_RETEST (or BLOCK).
    sector_status = " ".join(_text(sector_data, key) for key in ("heat_status", "temperature", "phase", "status"))
    if _contains_any(sector_status, {"OVERSOLD", "OVERCOOLED", "超跌", "过冷"}):
        counter_evidence.append("板块处于过冷/超跌观察区，但超跌不是买点，必须等待止跌与回踩承接。")
        if decision in {"ALLOW", "ALLOW_SMALL"} and not retest_confirmed:
            decision, allowed, risk_level, label = "WAIT_RETEST", 0.0, "MEDIUM", "超跌不等于见底，等待承接"

    recheck_conditions.extend(
        [
            "至少等待一个5分钟观察窗，禁止在直线拉升过程中追单。",
            "回踩真实分时均价或突破位不破，随后价格重新抬高。",
            "回踩缩量、恢复放量，且板块订单流方向估算没有由正转负。",
            "仍符合当日交易计划、交易模式、止损和风险收益比。",
        ]
    )
    if is_holding:
        recheck_conditions.append("先检查现有总仓位；已有浮亏、已有持仓或摊低成本都不是加仓触发器。")

    cooldown_minutes = 5 if decision in {"BLOCK", "WAIT_RETEST"} else 1
    cooldown_until = evaluated_at + timedelta(minutes=cooldown_minutes) if decision in {"BLOCK", "WAIT_RETEST"} else None
    expires_at = evaluated_at + timedelta(minutes=1)
    if not enough_bars or not vwap_reliable or not minute_source_reliable:
        data_quality = "missing"
    elif complete_amount_bars and complete_volume_bars:
        data_quality = "realtime"
    else:
        data_quality = _text(volume_data, "data_quality") or "partial"

    return {
        "decision": decision,
        "label": label,
        "risk_level": risk_level,
        "hard_blocked": hard_blocked,
        "chase_score": score,
        "allowed_position_ratio": allowed,
        "reason_codes": reason_codes,
        "evidence": evidence,
        "counter_evidence": counter_evidence,
        "missing_conditions": list(dict.fromkeys(missing_conditions)),
        "recheck_conditions": list(dict.fromkeys(recheck_conditions)),
        "cooldown_until": cooldown_until.isoformat(timespec="seconds") if cooldown_until else None,
        "pulse_1m": pulse_1m,
        "pulse_3m": pulse_3m,
        "pulse_5m": pulse_5m,
        "distance_vwap_pct": distance_vwap_pct,
        "distance_high_pct": distance_high_pct,
        "data_quality": data_quality,
        "expires_at": expires_at.isoformat(timespec="seconds"),
    }


__all__ = ["evaluate_entry_gate"]
