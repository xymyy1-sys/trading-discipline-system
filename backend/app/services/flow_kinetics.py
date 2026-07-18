from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Iterable

from app.core.trading_clock import shanghai_now_naive


@dataclass(frozen=True)
class FlowKinetics:
    """Causal fund-flow state calculated only from observations at ``as_of``.

    Amount fields use the same unit as the source timeline (the application
    currently uses 亿元).  Speed is amount/minute and acceleration is
    amount/minute².  ``reliable`` is deliberately false until at least two
    distinct, timestamped observations exist.
    """

    direction: str = "UNKNOWN"
    speed: float | None = None
    acceleration: float | None = None
    turning: str | None = None
    signal: str | None = None
    severity: str = "info"
    as_of: str | None = None
    window_minutes: int | None = None
    reliable: bool = False
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class PriceVolumeFlowAlert:
    event_type: str
    title: str
    severity: str
    action: str
    evidence: tuple[str, ...]


def _shanghai_now_naive() -> datetime:
    return shanghai_now_naive()


def _value(point: Any, key: str, default: Any = None) -> Any:
    if isinstance(point, dict):
        return point.get(key, default)
    return getattr(point, key, default)


def _parse_point_time(label: str, as_of: datetime) -> datetime | None:
    value = str(label or "").strip()
    if not value:
        return None
    if value == "当前":
        return as_of
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed = datetime.strptime(value, fmt).time()
            return datetime.combine(as_of.date(), parsed)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = shanghai_now_naive(parsed)
    # Provider timelines from another Shanghai trading session must never be
    # combined with the current session merely because the clock time matches.
    if parsed.date() != as_of.date():
        return None
    return parsed


def _trading_minute_index(value: datetime) -> int | None:
    current = value.time()
    if time(9, 15) <= current < time(9, 30):
        return (current.hour * 60 + current.minute) - (9 * 60 + 30)
    if time(9, 30) <= current <= time(11, 30):
        return (current.hour * 60 + current.minute) - (9 * 60 + 30)
    if time(11, 30) < current < time(13, 0):
        return None
    if time(13, 0) <= current <= time(15, 0):
        # Treat the first afternoon observation as the next tradable minute,
        # instead of diluting speed with the 90-minute lunch break.
        return 121 + (current.hour * 60 + current.minute) - (13 * 60)
    return None


def _causal_points(
    points: Iterable[Any],
    *,
    current_value: float | None,
    as_of: datetime,
) -> list[tuple[datetime, int, float]]:
    if as_of.weekday() >= 5:
        return []
    by_minute: dict[int, tuple[datetime, int, float]] = {}
    for point in points:
        observed_at = _parse_point_time(str(_value(point, "time", "")), as_of)
        if observed_at is None or observed_at > as_of:
            continue
        minute_index = _trading_minute_index(observed_at)
        if minute_index is None:
            continue
        try:
            amount = float(_value(point, "value", 0) or 0)
        except (TypeError, ValueError):
            continue
        by_minute[minute_index] = (observed_at, minute_index, amount)

    now_index = _trading_minute_index(as_of)
    if current_value is not None and now_index is not None:
        by_minute[now_index] = (as_of, now_index, float(current_value))
    return [by_minute[key] for key in sorted(by_minute)]


def analyze_flow_kinetics(
    points: Iterable[Any],
    *,
    current_value: float | None,
    change_pct: float = 0,
    as_of: datetime | None = None,
) -> FlowKinetics:
    """Return direction, speed, acceleration and turning-point semantics.

    The function intentionally refuses to extrapolate from one snapshot.  It
    also filters provider points later than ``as_of`` and observations from a
    different date, preventing a refresh from leaking future data into a
    decision or simulation snapshot.
    """

    evaluated_at = shanghai_now_naive(as_of) if as_of is not None else _shanghai_now_naive()
    causal = _causal_points(points, current_value=current_value, as_of=evaluated_at)
    if not causal:
        return FlowKinetics()

    latest_at, _, latest_value = causal[-1]
    direction = "NET_INFLOW" if latest_value > 0.05 else "NET_OUTFLOW" if latest_value < -0.05 else "NEUTRAL"
    if len(causal) < 2:
        return FlowKinetics(
            direction=direction,
            as_of=latest_at.strftime("%Y-%m-%d %H:%M:%S"),
            evidence=("只有一个带时点的供应商订单流快照，不计算流速、加速度或拐点。",),
        )

    previous_at, previous_minute, previous_value = causal[-2]
    latest_minute = causal[-1][1]
    elapsed = latest_minute - previous_minute
    if elapsed <= 0:
        return FlowKinetics(
            direction=direction,
            as_of=latest_at.strftime("%Y-%m-%d %H:%M:%S"),
            evidence=("订单流快照没有形成不同的交易分钟，不计算流速。",),
        )

    speed = (latest_value - previous_value) / elapsed
    acceleration: float | None = None
    previous_speed: float | None = None
    if len(causal) >= 3:
        _, first_minute, first_value = causal[-3]
        previous_elapsed = previous_minute - first_minute
        if previous_elapsed > 0:
            previous_speed = (previous_value - first_value) / previous_elapsed
            slope_elapsed = max(1.0, (previous_elapsed + elapsed) / 2)
            acceleration = (speed - previous_speed) / slope_elapsed

    max_abs = max(abs(item[2]) for item in causal[-6:])
    speed_noise = max(0.03, max_abs * 0.002)
    acceleration_noise = max(0.005, speed_noise / max(3, elapsed))
    turning: str | None = None
    signal: str | None = None
    severity = "info"

    if previous_value <= 0 < latest_value:
        turning, signal = "TURN_TO_INFLOW", "订单流方向由净流出拐为净流入"
    elif previous_value >= 0 > latest_value:
        turning, signal, severity = "TURN_TO_OUTFLOW", "订单流方向由净流入拐为净流出", "warning"
    elif speed >= speed_noise and latest_value < 0:
        turning, signal = "OUTFLOW_NARROWING", "净流出正在快速收窄"
    elif speed <= -speed_noise and latest_value > 0:
        turning, signal, severity = "INFLOW_FADING", "净流入正在快速回落", "warning"
    elif speed >= speed_noise and acceleration is not None and acceleration >= acceleration_noise:
        turning, signal = "INFLOW_ACCELERATING", "订单流方向流入正在加速"
    elif speed <= -speed_noise and acceleration is not None and acceleration <= -acceleration_noise:
        turning, signal, severity = "OUTFLOW_ACCELERATING", "订单流方向流出正在加速", "warning"
    elif speed >= speed_noise:
        turning, signal = "FLOW_IMPROVING", "订单流方向边际改善"
    elif speed <= -speed_noise:
        turning, signal, severity = "FLOW_WEAKENING", "订单流方向边际转弱", "warning"

    # Price/flow divergence is observable evidence, not a claim about intent.
    if change_pct >= 0.8 and (direction == "NET_OUTFLOW" or speed <= -speed_noise):
        signal, severity = "价格上涨但订单流方向转弱，形成订单流与价格背离，警惕诱多", "warning"
    elif change_pct <= -0.8 and (turning == "TURN_TO_INFLOW" or speed >= speed_noise):
        signal = "价格仍下跌但订单流方向边际回流，进入反抽观察；未收复分时均价前不确认反转"

    speed_text = f"{speed:+.3f}亿/分钟"
    acceleration_text = "不可计算" if acceleration is None else f"{acceleration:+.4f}亿/分钟²"
    evidence = (
        f"{previous_at.strftime('%H:%M:%S')} 至 {latest_at.strftime('%H:%M:%S')}，净流由 {previous_value:+.2f} 亿变为 {latest_value:+.2f} 亿。",
        f"订单流方向流速 {speed_text}，订单流方向加速度 {acceleration_text}；窗口 {elapsed} 个交易分钟；该值来自供应商算法，不代表账户真实流水。",
    )
    return FlowKinetics(
        direction=direction,
        speed=round(speed, 4),
        acceleration=round(acceleration, 6) if acceleration is not None else None,
        turning=turning,
        signal=signal,
        severity=severity,
        as_of=latest_at.strftime("%Y-%m-%d %H:%M:%S"),
        window_minutes=elapsed,
        reliable=True,
        evidence=evidence,
    )


def classify_price_volume_flow_alerts(
    *,
    change_pct: float,
    volume_ratio: float | None,
    price_vs_vwap_pct: float | None,
    vwap_reliable: bool,
    flow: FlowKinetics,
    near_intraday_low: bool = False,
    hard_stop_triggered: bool = False,
    low_rebound_pct: float = 0,
    high_drawdown_pct: float = 0,
) -> list[PriceVolumeFlowAlert]:
    """Translate causal flow and volume/price facts into Chinese guardrails."""

    alerts: list[PriceVolumeFlowAlert] = []
    ratio = float(volume_ratio or 0)
    below_vwap = bool(vwap_reliable and price_vs_vwap_pct is not None and price_vs_vwap_pct < -0.2)
    above_vwap = bool(vwap_reliable and price_vs_vwap_pct is not None and price_vs_vwap_pct > 0.2)
    flow_worsening = flow.turning in {"TURN_TO_OUTFLOW", "INFLOW_FADING", "OUTFLOW_ACCELERATING", "FLOW_WEAKENING"}
    flow_improving = flow.turning in {"TURN_TO_INFLOW", "OUTFLOW_NARROWING", "INFLOW_ACCELERATING", "FLOW_IMPROVING"}

    if change_pct >= 1 and 0 < ratio <= 0.8 and flow_worsening:
        alerts.append(PriceVolumeFlowAlert(
            event_type="SHRINKING_RISE_DIVERGENCE",
            title="缩量上涨且订单流方向转弱，警惕诱多",
            severity="warning",
            action="禁止追高；等待放量站稳分时均价且订单流方向重新拐入。",
            evidence=(f"涨幅 {change_pct:+.2f}%，量比 {ratio:.2f}。", flow.signal or "订单流方向边际转弱。"),
        ))

    if low_rebound_pct >= 1.5 and 0 < ratio <= 0.8 and below_vwap:
        alerts.append(PriceVolumeFlowAlert(
            event_type="SHRINKING_REBOUND_UNCONFIRMED",
            title="缩量反弹仍在分时均价下方，反转未确认",
            severity="warning",
            action="不追反弹、不急于买回；等待放量站回真实分时均价且板块订单流方向继续改善。",
            evidence=(
                f"自日内低点反弹 {low_rebound_pct:.2f}%，量比仅 {ratio:.2f}，价格仍在真实分时均价下方。",
                flow.signal or "订单流方向尚未形成可靠的持续回流。",
            ),
        ))

    if change_pct <= -1 and 0 < ratio <= 0.8:
        if flow_improving:
            alerts.append(PriceVolumeFlowAlert(
                event_type="SHRINKING_DECLINE_EXHAUSTION_WATCH",
                title="缩量下跌且订单流方向边际改善，抛压衰减待确认",
                severity="info",
                action="不在低位追卖，也不直接抄底；等待低点抬高并重新站回真实分时均价。",
                evidence=(
                    f"跌幅 {change_pct:+.2f}%，量比 {ratio:.2f}，下跌成交未放大。",
                    flow.signal or "订单流方向流速已边际改善。",
                ),
            ))
        elif flow_worsening:
            alerts.append(PriceVolumeFlowAlert(
                event_type="SHRINKING_DECLINE_WEAKNESS",
                title="缩量下跌但订单流方向仍在流出，不能当作见底",
                severity="warning",
                action="禁止接飞刀；缩量只说明成交不足，须等待流出收窄、低点抬高和分时均价修复。",
                evidence=(
                    f"跌幅 {change_pct:+.2f}%，量比 {ratio:.2f}。",
                    flow.signal or "订单流方向仍在边际转弱。",
                ),
            ))

    if (
        change_pct > 0
        and high_drawdown_pct >= 0.6
        and high_drawdown_pct <= 3.0
        and 0 < ratio <= 0.8
        and above_vwap
        and not flow_worsening
    ):
        alerts.append(PriceVolumeFlowAlert(
            event_type="SHRINKING_PULLBACK_SUPPORT_WATCH",
            title="缩量回踩且仍在分时均价上方，观察承接",
            severity="info",
            action="不因一次回踩恐慌卖出；只有重新放量跌破分时均价且订单流方向拐出才升级风险。",
            evidence=(
                f"当前仍上涨 {change_pct:+.2f}%，高点回撤 {high_drawdown_pct:.2f}%，量比 {ratio:.2f}。",
                flow.signal or "订单流方向未出现可靠转弱拐点。",
            ),
        ))

    if change_pct <= -2 and ratio >= 1.2 and below_vwap and flow_worsening:
        alerts.append(PriceVolumeFlowAlert(
            event_type="VOLUME_DOWN_FLOW_ACCELERATION",
            title="放量下跌且订单流方向转弱，禁止接飞刀",
            severity="critical",
            action="不逆势补仓；等待流出速度明显收窄、低点抬高并重新站回真实分时均价。",
            evidence=(f"跌幅 {change_pct:+.2f}%，量比 {ratio:.2f}，价格位于真实分时均价下方。", flow.signal or "订单流方向流出仍在延续。"),
        ))

    if near_intraday_low and not hard_stop_triggered and change_pct <= -3 and (flow_improving or low_rebound_pct >= 1.5):
        alerts.append(PriceVolumeFlowAlert(
            event_type="LOW_PANIC_SELL_GUARD",
            title="低位恐慌释放，禁止在日内低点追卖",
            severity="info",
            action="禁止在低点追卖；保留风险结论并等待首次有效反抽，只有固定硬止损实际触发才直接退出。",
            evidence=(f"当前接近日内低点，跌幅 {change_pct:+.2f}%，低点反弹 {low_rebound_pct:.2f}%。", flow.signal or "订单流方向流速已停止恶化。"),
        ))

    if change_pct > 0 and flow.turning == "TURN_TO_OUTFLOW":
        alerts.append(PriceVolumeFlowAlert(
            event_type="FLOW_TURN_OUT_DISTRIBUTION_WARNING",
            title="上涨过程中订单流方向由流入拐为流出",
            severity="warning",
            action="提高利润保护；若随后跌破真实分时均价，按冲高兑现窗口处理。",
            evidence=(flow.signal or "订单流方向由净流入拐为净流出。",),
        ))
    elif change_pct < 0 and flow.turning == "TURN_TO_INFLOW":
        alerts.append(PriceVolumeFlowAlert(
            event_type="FLOW_TURN_IN_REBOUND_WATCH",
            title="下跌过程中订单流方向由流出拐为流入",
            severity="info",
            action="进入反抽观察，不在最低点追卖；未站回真实分时均价前也不追涨或抄底。",
            evidence=(flow.signal or "订单流方向由净流出拐为净流入。",),
        ))

    if change_pct >= 1.5 and ratio >= 1.2 and above_vwap and flow_improving:
        alerts.append(PriceVolumeFlowAlert(
            event_type="VOLUME_FLOW_STRENGTH_CONFIRMED",
            title="放量上涨且订单流方向同步改善",
            severity="info",
            action="保持观察；回踩分时均价不破且订单流方向未再拐出，才视为强势延续。",
            evidence=(f"涨幅 {change_pct:+.2f}%，量比 {ratio:.2f}，价格位于真实分时均价上方。", flow.signal or "订单流方向边际改善。"),
        ))

    if low_rebound_pct >= 1.5 and ratio >= 1.2 and above_vwap and flow_improving:
        alerts.append(PriceVolumeFlowAlert(
            event_type="VOLUME_REBOUND_CONFIRMED",
            title="放量反弹站回分时均价且订单流方向改善",
            severity="info",
            action="停止沿用低点卖出结论；观察首次回踩分时均价是否不破，仍不自动加仓。",
            evidence=(
                f"自日内低点反弹 {low_rebound_pct:.2f}%，量比 {ratio:.2f}，已在真实分时均价上方。",
                flow.signal or "订单流方向边际改善。",
            ),
        ))

    return alerts
