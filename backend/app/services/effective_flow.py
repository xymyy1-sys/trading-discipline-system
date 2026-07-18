from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time
from math import isfinite
from typing import Any, Iterable, Mapping, Sequence

from app.core.trading_clock import shanghai_now_naive


ATTACK_CONFIRMED = "ATTACK_CONFIRMED"
ABSORPTION_CANDIDATE = "ABSORPTION_CANDIDATE"
RECOVERY_CANDIDATE = "RECOVERY_CANDIDATE"
DISTRIBUTION_RISK = "DISTRIBUTION_RISK"
OUTFLOW_CONFIRMED = "OUTFLOW_CONFIRMED"
LIQUIDITY_SHOCK = "LIQUIDITY_SHOCK"
INCONCLUSIVE = "INCONCLUSIVE"
INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

_STATE_LABELS = {
    ATTACK_CONFIRMED: "买向成交与上涨同步",
    ABSORPTION_CANDIDATE: "下方承接候选",
    RECOVERY_CANDIDATE: "深水修复候选",
    DISTRIBUTION_RISK: "买盘推动不足风险",
    OUTFLOW_CONFIRMED: "卖向成交与下跌同步",
    LIQUIDITY_SHOCK: "流动性冲击",
    INCONCLUSIVE: "方向未决",
    INSUFFICIENT_DATA: "数据不足",
}

_UNTRUSTED_QUALITY_TOKENS = {
    "MISSING",
    "STALE",
    "MANUAL",
    "ESTIMATED",
    "DEGRADED",
    "UNKNOWN",
    "CACHE",
    "CACHED",
    "FALLBACK",
    "PARTIAL",
    "UNAVAILABLE",
    "缺失",
    "过期",
    "手动",
    "估算",
    "降级",
    "缓存",
    "不可用",
}

_TRUSTED_ACTIVE_FLOW_SOURCES = {
    "provider_tick_direction",
    "eastmoney_tick",
}


@dataclass(frozen=True)
class EffectiveFlowEvidence:
    """Temporally aligned evidence about directional order flow and price response.

    Amounts retain the unit supplied by the provider.  The service never
    relabels active-flow estimates as exchange observations and never invents
    a historical baseline.  ``same_time_flow_percentile`` is therefore
    ``None`` until a caller supplies at least five comparable observations.
    """

    state: str
    state_label: str
    trade_date: str | None
    as_of: str | None
    data_quality: str
    active_flow_source: str | None
    active_flow_estimated: bool
    minute_amount_estimated: bool
    bar_count: int
    exact_flow_bar_count: int
    window_minutes: int | None
    active_buy_amount: float | None
    active_sell_amount: float | None
    signed_active_flow: float | None
    buy_ratio: float | None
    active_imbalance_ratio: float | None
    active_flow_coverage_ratio: float | None
    directional_persistence: float | None
    same_time_flow_percentile: float | None
    normalization_sample_count: int
    vwap: float | None
    vwap_source: str | None
    vwap_reliable: bool
    vwap_response_pct: float | None
    price_response_pct: float | None
    max_one_minute_move_pct: float | None
    price_response_per_imbalance_pct_points: float | None
    impact_retention_ratio: float | None
    confidence: int
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...]
    counter_evidence: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    discipline: str
    advice: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _date_text(value: str | date | None) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def _bar_datetime(row: Mapping[str, Any], target_date: str) -> datetime | None:
    raw = str(row.get("time") or row.get("datetime") or row.get("captured_at") or "").strip()
    if not raw:
        return None
    try:
        if "T" in raw or " " in raw:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return shanghai_now_naive(parsed)
        return datetime.fromisoformat(f"{target_date}T{raw[:8]}")
    except ValueError:
        return None


def _trading_minute_index(value: datetime) -> int | None:
    current = value.time()
    if time(9, 30) <= current <= time(11, 30):
        return value.hour * 60 + value.minute - (9 * 60 + 30)
    if time(13, 0) <= current <= time(15, 0):
        return 121 + value.hour * 60 + value.minute - (13 * 60)
    return None


def _expected_latest_at(now: datetime) -> datetime | None:
    if now.weekday() >= 5:
        return None
    current = now.time()
    if time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0):
        return now
    if time(11, 30) < current < time(13, 0):
        return datetime.combine(now.date(), time(11, 30))
    if current > time(15, 0):
        return datetime.combine(now.date(), time(15, 0))
    return None


def _quality_is_untrusted(data_quality: str | None) -> bool:
    normalized = str(data_quality or "").strip().upper()
    return bool(normalized and any(token in normalized for token in _UNTRUSTED_QUALITY_TOKENS))


def _empty_result(
    *,
    target_date: str | None,
    as_of: datetime | None,
    source: str | None,
    estimated: bool,
    amount_estimated: bool,
    bar_count: int,
    exact_count: int,
    quality: str,
    reason_codes: Sequence[str],
    evidence: Sequence[str],
) -> EffectiveFlowEvidence:
    return EffectiveFlowEvidence(
        state=INSUFFICIENT_DATA,
        state_label=_STATE_LABELS[INSUFFICIENT_DATA],
        trade_date=target_date,
        as_of=as_of.isoformat(timespec="seconds") if as_of else None,
        data_quality=quality,
        active_flow_source=source,
        active_flow_estimated=estimated,
        minute_amount_estimated=amount_estimated,
        bar_count=bar_count,
        exact_flow_bar_count=exact_count,
        window_minutes=None,
        active_buy_amount=None,
        active_sell_amount=None,
        signed_active_flow=None,
        buy_ratio=None,
        active_imbalance_ratio=None,
        active_flow_coverage_ratio=None,
        directional_persistence=None,
        same_time_flow_percentile=None,
        normalization_sample_count=0,
        vwap=None,
        vwap_source=None,
        vwap_reliable=False,
        vwap_response_pct=None,
        price_response_pct=None,
        max_one_minute_move_pct=None,
        price_response_per_imbalance_pct_points=None,
        impact_retention_ratio=None,
        confidence=0,
        reason_codes=tuple(reason_codes),
        evidence=tuple(evidence),
        counter_evidence=(),
        invalidation_conditions=("补齐同一交易日、带交易所时间戳且未经估算的分钟成交数据后重新判断。",),
        discipline="数据不足时不生成资金介入、派发、承接或流出结论。",
        advice="保持原计划，不因不完整的订单流标签交易。",
    )


def _same_time_percentile(current: float, samples: Sequence[float] | None) -> tuple[float | None, int]:
    valid = [abs(item) for raw in (samples or ()) if (item := _number(raw)) is not None]
    if len(valid) < 5:
        return None, len(valid)
    magnitude = abs(current)
    less_or_equal = sum(1 for item in valid if item <= magnitude)
    return round(less_or_equal / len(valid) * 100, 1), len(valid)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def analyze_effective_flow(
    minute_bars: Iterable[Mapping[str, Any]],
    *,
    now: datetime | None = None,
    trade_date: str | date | None = None,
    vwap: float | None = None,
    vwap_reliable: bool = False,
    data_quality: str | None = None,
    active_flow_source: str | None = None,
    active_flow_estimated: bool = False,
    same_time_signed_flow_history: Sequence[float] | None = None,
    window_bars: int = 10,
    stale_after_seconds: int = 180,
) -> EffectiveFlowEvidence:
    """Build an effective-capital evidence chain from synchronized minute bars.

    ``active_buy_amount`` and ``active_sell_amount`` must be present on every
    bar in the selected window.  They mean aggressive buy/sell classifications,
    not cash entering or leaving the security.  A state describes the observed
    interaction between those classifications, liquidity and price; it never
    asserts the identity of an institution or a so-called main force.
    """

    evaluated_at = shanghai_now_naive(now)
    target_date = _date_text(trade_date) or evaluated_at.date().isoformat()
    raw_bars = [item for item in minute_bars if isinstance(item, Mapping)]
    if not raw_bars:
        return _empty_result(
            target_date=target_date,
            as_of=None,
            source=active_flow_source,
            estimated=active_flow_estimated,
            amount_estimated=False,
            bar_count=0,
            exact_count=0,
            quality="missing",
            reason_codes=("MISSING_MINUTE_BARS",),
            evidence=("没有可验证的分钟成交记录。",),
        )

    parsed: list[dict[str, Any]] = []
    reasons: list[str] = []
    quality_evidence: list[str] = []
    estimated = bool(active_flow_estimated)
    amount_estimated = False
    wrong_date = False
    missing_timestamp = False
    future_timestamp = False

    for row in raw_bars:
        row_date = _date_text(row.get("trade_date")) or target_date
        if row_date != target_date:
            wrong_date = True
        observed_at = _bar_datetime(row, row_date)
        if observed_at is None:
            missing_timestamp = True
            continue
        if observed_at.date().isoformat() != target_date:
            wrong_date = True
        if observed_at > evaluated_at:
            future_timestamp = True
        price = _number(row.get("price", row.get("close")))
        buy = _number(row.get("active_buy_amount"))
        sell = _number(row.get("active_sell_amount"))
        amount = _number(row.get("amount"))
        volume = _number(row.get("volume"))
        row_flow_estimated = bool(row.get("active_flow_estimated") or row.get("flow_estimated"))
        row_amount_estimated = bool(row.get("amount_estimated") or row.get("is_estimated"))
        parsed.append(
            {
                "at": observed_at,
                "price": price,
                "buy": buy,
                "sell": sell,
                "amount": amount,
                "volume": volume,
                "flow_estimated": row_flow_estimated,
                "amount_estimated": row_amount_estimated,
                "tick_batch_truncated": bool(row.get("tick_batch_truncated")),
                "tick_first_at": _bar_datetime({"time": row.get("tick_first_time")}, row_date)
                if row.get("tick_first_time") else None,
            }
        )

    parsed.sort(key=lambda item: item["at"])
    selected = parsed[-max(5, int(window_bars)) :]
    selected_missing_flow = any(item["buy"] is None or item["sell"] is None for item in selected)
    selected_invalid_values = any(
        item["price"] is None
        or item["price"] <= 0
        or (item["buy"] is not None and item["buy"] < 0)
        or (item["sell"] is not None and item["sell"] < 0)
        or (item["amount"] is not None and item["amount"] < 0)
        or (item["volume"] is not None and item["volume"] < 0)
        for item in selected
    )
    estimated = estimated or any(bool(item["flow_estimated"]) for item in selected)
    amount_estimated = any(bool(item["amount_estimated"]) for item in selected)
    tick_batch_truncated = any(bool(item["tick_batch_truncated"]) for item in selected)
    tick_first_candidates = [item["tick_first_at"] for item in selected if item["tick_first_at"] is not None]
    tick_first_at = min(tick_first_candidates) if tick_first_candidates else None

    if wrong_date:
        reasons.append("WRONG_TRADE_DATE")
        quality_evidence.append(f"分钟数据包含非目标交易日 {target_date} 的记录。")
    if missing_timestamp:
        reasons.append("MISSING_EXCHANGE_TIMESTAMP")
        quality_evidence.append("至少一条分钟记录没有可验证的成交时间。")
    if future_timestamp:
        reasons.append("FUTURE_TIMESTAMP")
        quality_evidence.append("分钟记录包含晚于评估时点的数据，拒绝未来数据泄漏。")
    if estimated:
        reasons.append("ESTIMATED_ACTIVE_FLOW")
        quality_evidence.append("主动买卖额包含估算字段，不能据此判断真实资金介入。")
    if amount_estimated:
        reasons.append("ESTIMATED_MINUTE_AMOUNT")
        quality_evidence.append("分钟成交额包含估算字段，不能据此计算真实分时均价或成交覆盖率。")
    if selected_missing_flow:
        reasons.append("MISSING_ACTIVE_FLOW")
        quality_evidence.append("最近判定窗口缺少主动买入额或主动卖出额。")
    if selected_invalid_values:
        reasons.append("INVALID_MINUTE_VALUES")
        quality_evidence.append("最近判定窗口的分钟价格或成交字段存在非法值。")
    if tick_batch_truncated and (tick_first_at is None or (selected and tick_first_at > selected[0]["at"])):
        reasons.append("TRUNCATED_TICK_WINDOW")
        quality_evidence.append(
            "逐笔接口返回量达到上限，且最早逐笔时间晚于观察窗口起点，窗口成交方向不完整。"
        )
    if _quality_is_untrusted(data_quality):
        reasons.append("UNTRUSTED_DATA_QUALITY")
        quality_evidence.append(f"上游数据质量为 {data_quality}，按失败关闭处理。")
    if str(active_flow_source or "").strip().lower() not in _TRUSTED_ACTIVE_FLOW_SOURCES:
        reasons.append("UNTRUSTED_ACTIVE_FLOW_SOURCE")
        quality_evidence.append("主动成交方向没有来自受信任的供应商逐笔方向字段。")
    if target_date != evaluated_at.date().isoformat():
        reasons.append("AS_OF_TRADE_DATE_MISMATCH")
        quality_evidence.append("评估时点与目标交易日不一致；历史回放必须显式传入当日 as-of 时间。")

    latest_at = parsed[-1]["at"] if parsed else None
    expected_at = _expected_latest_at(evaluated_at)
    if expected_at is None:
        reasons.append("OUTSIDE_EVALUABLE_SESSION")
        quality_evidence.append("当前时点尚未进入可评估的A股交易时段。")
    elif latest_at is not None and (expected_at - latest_at).total_seconds() > stale_after_seconds:
        reasons.append("STALE_MINUTE_TAPE")
        quality_evidence.append(
            f"最新成交时间 {latest_at.strftime('%H:%M:%S')} 距应有行情时点超过 {stale_after_seconds} 秒。"
        )

    if reasons:
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=estimated,
            amount_estimated=amount_estimated,
            bar_count=len(parsed),
            exact_count=sum(1 for item in selected if item["buy"] is not None and item["sell"] is not None),
            quality="untrusted",
            reason_codes=reasons,
            evidence=quality_evidence,
        )

    if len(selected) < 5:
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="insufficient",
            reason_codes=("INSUFFICIENT_EXACT_FLOW_BARS",),
            evidence=(f"只有 {len(selected)} 条可验证分钟记录，至少需要 5 条。",),
        )

    indices = [_trading_minute_index(item["at"]) for item in selected]
    if any(item is None for item in indices):
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="untrusted",
            reason_codes=("BAR_OUTSIDE_CONTINUOUS_SESSION",),
            evidence=("分钟记录包含连续竞价时段之外的成交。",),
        )
    numeric_indices = [int(item) for item in indices if item is not None]
    if any(right <= left for left, right in zip(numeric_indices, numeric_indices[1:])):
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="untrusted",
            reason_codes=("DUPLICATE_OR_REVERSED_TIMESTAMPS",),
            evidence=("分钟成交时间没有形成严格递增序列。",),
        )
    window_minutes = numeric_indices[-1] - numeric_indices[0]
    if window_minutes > max(15, len(selected) * 2):
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="partial",
            reason_codes=("DISCONTINUOUS_MINUTE_WINDOW",),
            evidence=(f"{len(selected)} 条记录跨越 {window_minutes} 个交易分钟，窗口不连续。",),
        )

    prices = [float(item["price"]) for item in selected]
    buys = [float(item["buy"]) for item in selected]
    sells = [float(item["sell"]) for item in selected]
    flows = [buy - sell for buy, sell in zip(buys, sells)]
    total_buy = sum(buys)
    total_sell = sum(sells)
    directional_total = total_buy + total_sell
    if directional_total <= 0:
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="missing",
            reason_codes=("EMPTY_ACTIVE_FLOW",),
            evidence=("窗口内主动买入额和主动卖出额均为零，无法区分真实无成交与接口缺数。",),
        )

    exact_amounts = [item["amount"] for item in selected]
    exact_volumes = [item["volume"] for item in selected]
    all_amount_volume_exact = all(
        amount is not None and amount >= 0 and volume is not None and volume > 0
        for amount, volume in zip(exact_amounts, exact_volumes)
    )
    supplied_vwap = _number(vwap) if vwap_reliable else None
    resolved_vwap = supplied_vwap if supplied_vwap and supplied_vwap > 0 else None
    vwap_source: str | None = "supplied_verified" if resolved_vwap is not None else None
    if resolved_vwap is None and all_amount_volume_exact:
        total_volume = sum(float(item) for item in exact_volumes if item is not None)
        if total_volume > 0:
            resolved_vwap = sum(float(item) for item in exact_amounts if item is not None) / total_volume
            vwap_source = "exact_minute_amount_volume"
    if resolved_vwap is None or resolved_vwap <= 0:
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="missing",
            reason_codes=("VWAP_UNRELIABLE",),
            evidence=("没有可靠分时均价，也无法用未经估算的分钟成交额与成交量重算。",),
        )

    net_flow = total_buy - total_sell
    buy_ratio = total_buy / directional_total
    imbalance = net_flow / directional_total
    direction = 1 if net_flow > 0 else -1 if net_flow < 0 else 0
    aligned_flow_bar_count = sum(
        1 for item in flows if direction and item != 0 and (item > 0) == (direction > 0)
    )
    persistence = (
        aligned_flow_bar_count / len(flows)
        if direction and flows
        else 0.0
    )
    total_bar_amount = (
        sum(float(item) for item in exact_amounts if item is not None)
        if all(item is not None for item in exact_amounts)
        else None
    )
    coverage = directional_total / total_bar_amount if total_bar_amount and total_bar_amount > 0 else None
    if coverage is not None and coverage > 1.25:
        return _empty_result(
            target_date=target_date,
            as_of=latest_at,
            source=active_flow_source,
            estimated=False,
            amount_estimated=False,
            bar_count=len(parsed),
            exact_count=len(selected),
            quality="untrusted",
            reason_codes=("ACTIVE_FLOW_EXCEEDS_TURNOVER",),
            evidence=(f"主动成交额覆盖率为 {coverage:.1%}，明显超过窗口总成交额，数据口径不一致。",),
        )

    price_response = (prices[-1] / prices[0] - 1) * 100
    vwap_response = (prices[-1] / resolved_vwap - 1) * 100
    minute_moves = [abs((right / left - 1) * 100) for left, right in zip(prices, prices[1:]) if left > 0]
    max_one_minute_move = max(minute_moves) if minute_moves else 0.0
    path_responses = [(price / prices[0] - 1) * 100 for price in prices]
    if direction > 0:
        peak_impact = max(path_responses)
        retention = max(0.0, min(1.25, price_response / peak_impact)) if peak_impact > 0 else 0.0
    elif direction < 0:
        peak_impact = abs(min(path_responses))
        retention = max(0.0, min(1.25, -price_response / peak_impact)) if peak_impact > 0 else 0.0
    else:
        retention = None
    response_per_imbalance = abs(price_response) / abs(imbalance) if abs(imbalance) >= 0.01 else None
    percentile, sample_count = _same_time_percentile(net_flow, same_time_signed_flow_history)

    flow_coverage_sufficient = coverage is not None and coverage >= 0.50
    positive_flow = flow_coverage_sufficient and buy_ratio >= 0.58 and imbalance >= 0.16 and persistence >= 0.60 and aligned_flow_bar_count >= 3
    negative_flow = flow_coverage_sufficient and buy_ratio <= 0.42 and imbalance <= -0.16 and persistence >= 0.60 and aligned_flow_bar_count >= 3
    price_up_confirmed = price_response >= 0.40 and vwap_response >= 0.20 and (retention or 0) >= 0.60
    price_down_confirmed = price_response <= -0.40 and vwap_response <= -0.20 and (retention or 0) >= 0.60
    # Being below the whole-session VWAP is not, by itself, evidence that
    # aggressive buying failed. A stock can be repairing sharply from a deep
    # intraday low while it has not yet reclaimed that older cost anchor. Call
    # it distribution only when the recent window made no progress or failed
    # to retain its own price impact; otherwise leave the direction unresolved
    # until VWAP is reclaimed.
    failed_positive_impact = price_response <= 0.10 or (retention or 0) <= 0.35
    resisted_negative_impact = price_response >= -0.10 or (retention or 0) <= 0.35
    recovery_candidate = (
        positive_flow
        and price_response >= 0.80
        and (retention or 0) >= 0.60
        and vwap_response < 0.20
    )
    liquidity_shock = max_one_minute_move >= 1.50 and (
        coverage is None or coverage < 0.25 or (response_per_imbalance is not None and response_per_imbalance >= 12)
    )

    counter_evidence: list[str] = []
    if percentile is None:
        counter_evidence.append("未提供至少5个同一时点的历史主动成交差样本，不判断成交方向规模是否异常。")
    if tick_batch_truncated:
        counter_evidence.append("逐笔接口已达到单批返回上限；当前窗口起点仍在返回区间内，但结论不外推到更早时段。")
    if coverage is None:
        counter_evidence.append("缺少可比较的窗口总成交额，不判断主动成交覆盖率。")
    elif coverage < 0.25:
        counter_evidence.append(f"主动成交分类仅覆盖窗口成交额的 {coverage:.1%}，结论置信度受限。")
    elif coverage < 0.50:
        counter_evidence.append(f"主动成交分类仅覆盖窗口成交额的 {coverage:.1%}，低于50%的方向确认门槛。")

    if liquidity_shock:
        state = LIQUIDITY_SHOCK
        discipline = "流动性冲击期间停止追涨、抄底和即时反手，等待至少三个新分钟形成稳定结构。"
        advice = "先观察盘口恢复、分时均价重新有效以及主动成交方向连续，再恢复原计划。"
        invalidation = (
            "连续三个以上分钟的价格冲击回到常态且主动成交覆盖率恢复。",
            "价格重新围绕真实分时均价稳定运行。",
        )
    elif positive_flow and price_up_confirmed:
        state = ATTACK_CONFIRMED
        discipline = "观察到买向成交与价格上涨在同一窗口同步，不等于允许追涨；远离分时均价时仍须等待回踩。"
        advice = "观察首次回踩能否缩量守住分时均价，只有原计划触发且风险收益比达标才考虑执行。"
        invalidation = (
            "主动成交差转负且连续两个分钟没有修复。",
            "价格跌破真实分时均价并且上行冲击保留率降至40%以下。",
        )
    elif recovery_candidate:
        state = RECOVERY_CANDIDATE
        discipline = "深水区已有订单流与价格同步修复，避免在窗口低点附近恐慌卖出；但尚未收回真实分时均价，禁止把反抽当反转、禁止追高或逆势补仓。"
        advice = "等待放量站回真实分时均价并维持至少三个分钟，随后首次回踩缩量不破，才把修复候选升级为有效进攻。"
        invalidation = (
            "主动成交差转负且连续两个分钟没有修复。",
            "价格跌破本观察窗口低点或修复冲击保留率降至40%以下。",
        )
    elif positive_flow and failed_positive_impact:
        state = DISTRIBUTION_RISK
        discipline = "主动买入较多但价格推不动时禁止追高，不能把软件净流入直接解释为主力吸筹。"
        advice = "已有持仓提高利润保护；等待放量突破并保持，或回踩缩量承接后再判断。"
        invalidation = (
            "价格放量突破窗口高点并在真实分时均价上方保持至少三个分钟。",
            "主动买入占比继续提高且价格冲击保留率恢复到60%以上。",
        )
    elif negative_flow and price_down_confirmed:
        state = OUTFLOW_CONFIRMED
        discipline = "卖出方向与价格下移同步时禁止接飞刀；但仍应服从预先定义的止损和仓位计划。"
        advice = "等待流出速度收窄、低点抬高并重新站回真实分时均价，不因单根反抽补仓。"
        invalidation = (
            "主动成交差转正并连续两个分钟保持。",
            "价格收回真实分时均价且低点抬高。",
        )
    elif negative_flow and resisted_negative_impact:
        state = ABSORPTION_CANDIDATE
        discipline = "主动卖出较多但价格拒绝下跌，只能视为承接候选，不能直接推断机构吸筹或立即抄底。"
        advice = "避免在窗口低点恐慌卖出；等待重新站回分时均价、低点抬高和卖压衰减确认。"
        invalidation = (
            "价格放量跌破窗口低点。",
            "主动卖出占比继续上升且下行冲击保留率恢复到60%以上。",
        )
    else:
        state = INCONCLUSIVE
        if not flow_coverage_sufficient:
            discipline = "主动成交方向样本覆盖不足，不把局部逐笔样本外推为全窗口资金结论。"
            advice = "维持原计划，等待方向分类覆盖率达到50%以上且价格响应、持续性形成共振。"
        else:
            discipline = "订单流方向、价格响应或持续性没有形成共振，不用单一订单流标签做交易决定。"
            advice = "维持原计划，等待主动成交方向、分时均价和价格冲击至少两项同步。"
        invalidation = ("形成新的连续分钟窗口后重新计算。",)

    evidence = [
        f"{selected[0]['at'].strftime('%H:%M')} 至 {selected[-1]['at'].strftime('%H:%M')}：主动买入额 {total_buy:.2f}，主动卖出额 {total_sell:.2f}，成交差 {net_flow:+.2f}。",
        f"主动买入占比 {buy_ratio:.1%}，方向持续率 {persistence:.1%}；窗口价格响应 {price_response:+.2f}%。",
        f"最新价相对真实分时均价 {vwap_response:+.2f}%，价格冲击保留率 {retention:.1%}。" if retention is not None else f"最新价相对真实分时均价 {vwap_response:+.2f}%。",
    ]
    if coverage is not None:
        evidence.append(f"主动成交分类覆盖窗口成交额 {coverage:.1%}。")
    if percentile is not None:
        evidence.append(f"成交差绝对值位于 {sample_count} 个同时间历史样本的 {percentile:.1f} 分位。")

    confidence = 30
    if state in {ATTACK_CONFIRMED, OUTFLOW_CONFIRMED}:
        confidence = 64
    elif state in {DISTRIBUTION_RISK, ABSORPTION_CANDIDATE, RECOVERY_CANDIDATE}:
        confidence = 56
    elif state == LIQUIDITY_SHOCK:
        confidence = 52
    if percentile is not None:
        confidence += 8 if percentile >= 80 else 4
    if coverage is not None and coverage >= 0.50:
        confidence += 5
    elif coverage is not None and coverage < 0.25:
        confidence -= 10
    if len(selected) >= 8:
        confidence += 3
    # Without a same-time baseline the service may classify effectiveness, but
    # must not imply that the absolute flow is unusually large.
    if percentile is None:
        confidence = min(confidence, 69)
    if state in {ABSORPTION_CANDIDATE, RECOVERY_CANDIDATE}:
        confidence = min(confidence, 65)
    confidence = max(0, min(90, confidence))

    return EffectiveFlowEvidence(
        state=state,
        state_label=_STATE_LABELS[state],
        trade_date=target_date,
        as_of=latest_at.isoformat(timespec="seconds") if latest_at else None,
        data_quality=(
            "partial"
            if coverage is None or coverage < 0.50
            else "realtime_exact" if data_quality is None else str(data_quality)
        ),
        active_flow_source=active_flow_source,
        active_flow_estimated=False,
        minute_amount_estimated=False,
        bar_count=len(parsed),
        exact_flow_bar_count=len(selected),
        window_minutes=window_minutes,
        active_buy_amount=_round(total_buy, 2),
        active_sell_amount=_round(total_sell, 2),
        signed_active_flow=_round(net_flow, 2),
        buy_ratio=_round(buy_ratio),
        active_imbalance_ratio=_round(imbalance),
        active_flow_coverage_ratio=_round(coverage),
        directional_persistence=_round(persistence),
        same_time_flow_percentile=percentile,
        normalization_sample_count=sample_count,
        vwap=_round(resolved_vwap),
        vwap_source=vwap_source,
        vwap_reliable=True,
        vwap_response_pct=_round(vwap_response),
        price_response_pct=_round(price_response),
        max_one_minute_move_pct=_round(max_one_minute_move),
        price_response_per_imbalance_pct_points=_round(response_per_imbalance),
        impact_retention_ratio=_round(retention),
        confidence=confidence,
        reason_codes=(),
        evidence=tuple(evidence),
        counter_evidence=tuple(counter_evidence),
        invalidation_conditions=tuple(invalidation),
        discipline=discipline,
        advice=advice,
    )
