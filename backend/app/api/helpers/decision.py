from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.execution import build_position_execution_state
from app.api.helpers.holdings_calc import _find_holding_by_code, _read_account_total_asset
from app.api.helpers.quotes import _latest_a_share_quotes, _quote_lookup_code, _safe_float
from app.api.helpers.seesaw import _holding_theme_profile
from app.api.helpers.volume_price import _snapshot_out, build_volume_price_snapshot
from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.models.trading import (
    DataCaptureSnapshot,
    ExpectationRevision,
    ExpectationRule,
    ExpectationScenario,
    ExpectationSnapshot,
    Holding,
    IntradayEvidenceEvent,
    MarketRegimeSnapshot,
    NextDayPlan,
    TTradePlan,
    VolumePriceSnapshot,
)
from app.schemas.trading import (
    ExpectationSnapshotIn,
    ExpectationSnapshotOut,
    ExpectationSnapshotUpdate,
    IntradayEvidenceEventOut,
    StockDecisionCardOut,
    TEligibilityOut,
    TTradePlanIn,
    TTradePlanOut,
)
from app.services.t_trading_engine import (
    build_t_eligibility as engine_build_t_eligibility,
    create_t_plan as engine_create_t_plan,
    normalize_t_type,
    update_t_plan as engine_update_t_plan,
)
from app.services.entry_gate import evaluate_entry_gate
from app.services.effective_flow import analyze_effective_flow
from app.services.cache import _get_response_cache


def _today(now: datetime | None = None) -> str:
    return shanghai_today(now).isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def expectation_evidence_coverage(
    *,
    quote: dict[str, Any] | None,
    volume: VolumePriceSnapshot | None,
    reference_trade_date: str,
) -> tuple[float, list[str], list[str]]:
    """Calculate explainable evidence coverage for an expectation snapshot.

    Confidence is intentionally evidence coverage rather than a statistical
    probability.  Five independently inspectable inputs each contribute 20%.
    A present but degraded field is reported as a gap instead of receiving a
    hard-coded fallback score.
    """

    quote = quote or {}
    current_price = _safe_float(quote.get("price"))
    open_price = _safe_float(quote.get("open"))
    previous_close = _safe_float(quote.get("prev_close"))
    if current_price <= 0 and volume is not None:
        current_price = float(volume.price or 0)
    if open_price <= 0 and volume is not None:
        open_price = float(volume.open_price or 0)
    if previous_close <= 0 and volume is not None:
        previous_close = float(volume.prev_close or 0)

    fresh_volume = False
    volume_age_days: int | None = None
    if volume is not None and volume.trade_date:
        try:
            reference_date = datetime.fromisoformat(reference_trade_date).date()
            volume_date = datetime.fromisoformat(volume.trade_date).date()
            volume_age_days = (reference_date - volume_date).days
            fresh_volume = 0 <= volume_age_days <= 3
        except (TypeError, ValueError):
            fresh_volume = False

    quality = str(getattr(volume, "data_quality", "") or "").lower()
    quality_reliable = quality in {"realtime", "reliable", "complete", "ok"}
    vwap_reliable = bool(
        volume is not None
        and volume.vwap_reliable
        and float(volume.vwap or 0) > 0
    )
    vwap_value = float(volume.vwap or 0) if volume is not None else 0.0

    components = [
        (
            current_price > 0,
            f"证据完整度·行情：有效价格 {current_price:.2f} 可用（+20%）。",
            "证据完整度缺口·行情：没有有效现价/收盘价（+0%）。",
        ),
        (
            fresh_volume,
            (
                f"证据完整度·量价：{volume.trade_date} 量价快照为同日/最近数据（+20%）。"
                if volume is not None
                else ""
            ),
            (
                "证据完整度缺口·量价：没有同日/最近量价快照（+0%）。"
                if volume_age_days is None
                else f"证据完整度缺口·量价：快照距参考日 {volume_age_days} 天（+0%）。"
            ),
        ),
        (
            quality_reliable,
            f"证据完整度·质量：量价数据质量为 {quality}（+20%）。",
            f"证据完整度缺口·质量：量价数据质量为 {quality or '缺失'}（+0%）。",
        ),
        (
            vwap_reliable,
            f"证据完整度·VWAP：真实分时VWAP {vwap_value:.2f} 可用（+20%）。",
            "证据完整度缺口·VWAP：真实分时VWAP不可用或不可靠（+0%）。",
        ),
        (
            open_price > 0 and previous_close > 0,
            f"证据完整度·开盘基准：开盘 {open_price:.2f}、昨收 {previous_close:.2f} 可用（+20%）。",
            "证据完整度缺口·开盘基准：开盘价或昨收价缺失（+0%）。",
        ),
    ]
    evidence = [positive for present, positive, _ in components if present and positive]
    counter = [negative for present, _, negative in components if not present and negative]
    return round(sum(0.2 for present, _, _ in components if present), 2), evidence, counter


def _event_in_trade_session(row: IntradayEvidenceEvent, trade_date: str) -> bool:
    """Use publication time for news and capture time for market evidence.

    This keeps an intraday announcement collected after close in the correct
    decision session, while preventing an old headline captured today from
    leaking into today's stock card.
    """

    event_type = str(getattr(row, "event_type", "") or "")
    is_news = "NEWS_" in event_type
    value = getattr(row, "source_published_at", None) if is_news else getattr(row, "captured_at", None)
    if not isinstance(value, datetime):
        return False
    if value.tzinfo is not None:
        value = shanghai_now_naive(value)
    return value.date().isoformat() == trade_date and time(9, 15) <= value.time() <= time(15, 0)


def minute_evidence_timeline(code: str, name: str, quote: dict[str, Any]) -> list[IntradayEvidenceEventOut]:
    """Build a read-only evidence chain from the actual minute at which a condition occurred."""
    rows = [row for row in (quote.get("minute_bars") or []) if isinstance(row, dict)]
    points: list[dict[str, Any]] = []
    cumulative_amount = 0.0
    cumulative_volume = 0.0
    peak = 0.0
    was_above = False
    was_below = False
    break_added = False
    recovery_added = False
    breakout_added = False
    pullback_hold_added = False
    drawdown_added = False
    processed: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        price = _safe_float(row.get("price") or row.get("close"))
        volume = _safe_float(row.get("volume"))
        amount = _safe_float(row.get("amount")) or price * volume
        if price <= 0 or volume <= 0:
            continue
        cumulative_amount += amount
        cumulative_volume += volume
        vwap = cumulative_amount / cumulative_volume if cumulative_volume else 0
        peak = max(peak, _safe_float(row.get("high")) or price)
        point = {"row": row, "price": price, "vwap": vwap, "peak": peak}
        processed.append(point)
        if not points:
            points.append({**point, "type": "OPENING_CONFIRMATION", "severity": "info", "text": f"开盘分钟价格 {price:.2f}，分时均价 {vwap:.2f}。"})
        if price >= vwap:
            if was_below and not recovery_added:
                points.append({**point, "type": "VWAP_RECOVERED", "severity": "info", "text": f"{row.get('time')} 收复分时均价线，价格 {price:.2f}，VWAP {vwap:.2f}。"})
                recovery_added = True
            was_above = True
        elif was_above and not break_added:
            points.append({**point, "type": "VWAP_BROKEN", "severity": "warning", "text": f"{row.get('time')} 首次由分时均价线上方跌破，价格 {price:.2f}，VWAP {vwap:.2f}。"})
            break_added = True
            was_below = True
        elif price < vwap:
            was_below = True
        if index >= 5:
            prior_high = max(_safe_float(item.get("high") or item.get("price") or item.get("close")) for item in rows[:index])
            if prior_high > 0 and price >= prior_high * 1.002 and not breakout_added:
                points.append({**point, "type": "UPSIDE_BREAKOUT", "severity": "info", "text": f"{row.get('time')} 向上突破此前日内高点 {prior_high:.2f}，价格 {price:.2f}。"})
                breakout_added = True
        drawdown = (peak - price) / peak * 100 if peak else 0
        if drawdown >= 3 and not drawdown_added:
            points.append({**point, "type": "PROFIT_DRAWDOWN_WARNING", "severity": "warning", "text": f"相对日内高点首次回撤达到 {drawdown:.2f}%，价格 {price:.2f}。"})
            drawdown_added = True
        if breakout_added and not pullback_hold_added and 0.8 <= drawdown <= 2.5 and price >= vwap:
            points.append({**point, "type": "PULLBACK_SUPPORT_HELD", "severity": "info", "text": f"突破后回踩未破分时均价 {vwap:.2f}，价格 {price:.2f}，承接暂时有效。"})
            pullback_hold_added = True
    if not rows or not points:
        return []
    valid_rows = [row for row in rows if _safe_float(row.get("price") or row.get("close")) > 0]
    if valid_rows:
        high_row = max(valid_rows, key=lambda row: _safe_float(row.get("high") or row.get("price") or row.get("close")))
        high_price = _safe_float(high_row.get("high") or high_row.get("price") or high_row.get("close"))
        points.append({"row": high_row, "price": high_price, "vwap": 0, "peak": high_price, "type": "INTRADAY_HIGH_CONFIRMED", "severity": "info", "text": f"日内高点 {high_price:.2f} 在 {high_row.get('time')} 形成。"})
        low_index, low_row = min(enumerate(valid_rows), key=lambda pair: _safe_float(pair[1].get("low") or pair[1].get("price") or pair[1].get("close")))
        low_price = _safe_float(low_row.get("low") or low_row.get("price") or low_row.get("close"))
        points.append({"row": low_row, "price": low_price, "vwap": 0, "peak": high_price, "type": "INTRADAY_LOW_CONFIRMED", "severity": "info", "text": f"日内低点 {low_price:.2f} 在 {low_row.get('time')} 形成，作为盘中支撑参考。"})
        for later in valid_rows[low_index + 1:]:
            later_price = _safe_float(later.get("price") or later.get("close"))
            if low_price > 0 and later_price >= low_price * 1.01:
                points.append({"row": later, "price": later_price, "vwap": low_price, "peak": high_price, "type": "SUPPORT_CONFIRMED", "severity": "info", "text": f"日内低点后反弹超过1%，{low_price:.2f}附近支撑得到初步确认。"})
                break
        last = valid_rows[-1]
        last_price = _safe_float(last.get("price") or last.get("close"))
        from app.api.helpers.volume_price import _minute_reversal_signals
        final_vwap = float(processed[-1]["vwap"]) if processed else 0.0
        reversal_pattern, reversal_evidence = _minute_reversal_signals(quote, final_vwap)
        if reversal_pattern:
            points.append({
                "row": last,
                "price": last_price,
                "vwap": final_vwap,
                "peak": peak,
                "type": "INTRADAY_REVERSAL_PENDING" if "待确认" in reversal_pattern else "INTRADAY_REVERSAL_CONFIRMED",
                "severity": "info",
                "text": f"{reversal_pattern}：{'；'.join(reversal_evidence)}",
            })
        points.append({"row": last, "price": last_price, "vwap": 0, "peak": peak, "type": "CLOSE_CONFIRMATION", "severity": "info", "text": f"收盘前最后分钟价格 {last_price:.2f}，用于盘后次日预期校准。"})
    outputs: list[IntradayEvidenceEventOut] = []
    seen: set[tuple[str, str]] = set()
    for point in points:
        row = point["row"]
        minute = str(row.get("time") or "15:00")[:5]
        trade_date = str(row.get("trade_date") or quote.get("minute_bar_trade_date") or _today())
        key = (str(point["type"]), minute)
        if key in seen:
            continue
        seen.add(key)
        try:
            captured_at = datetime.fromisoformat(f"{trade_date}T{minute}:00")
        except ValueError:
            captured_at = shanghai_now_naive().replace(hour=15, minute=0, second=0, microsecond=0)
        outputs.append(IntradayEvidenceEventOut(
            captured_at=captured_at, scope="stock", target_code=code, target_name=name,
            event_type=str(point["type"]), severity=str(point["severity"]), value=round(float(point["price"]), 2),
            previous_value=round(float(point.get("vwap") or 0), 2), evidence=[str(point["text"])], confirmed=True,
        ))
    return sorted(outputs, key=lambda item: item.captured_at, reverse=True)


def current_expectation_stage(now: datetime | None = None) -> str:
    now = shanghai_now_naive(now)
    current = now.time()
    if current < time(9, 25):
        return "盘前预期"
    if current < time(9, 30):
        return "竞价确认"
    if current < time(9, 35):
        return "开盘确认"
    if current < time(10, 0):
        return "五分钟确认"
    if current < time(11, 30):
        return "第一阶段确认"
    if current < time(13, 0):
        return "午盘状态"
    if current < time(14, 30):
        return "午后确认"
    if current < time(15, 0):
        return "尾盘状态"
    return "收盘校准"


def _expectation_out(row: ExpectationSnapshot) -> ExpectationSnapshotOut:
    return ExpectationSnapshotOut(
        id=row.id,
        trade_date=row.trade_date,
        code=row.code,
        name=row.name,
        stage=row.stage,
        base_expectation=row.base_expectation,
        expected_open_low=row.expected_open_low,
        expected_open_high=row.expected_open_high,
        outperform_threshold=row.outperform_threshold,
        underperform_threshold=row.underperform_threshold,
        severe_underperform_threshold=row.severe_underperform_threshold,
        actual_open_pct=row.actual_open_pct,
        actual_change_pct=row.actual_change_pct,
        expectation_gap_score=row.expectation_gap_score,
        expectation_result=row.expectation_result,
        state_transition=row.state_transition,
        confidence=row.confidence,
        evidence=_json_list(row.evidence_json),
        counter_evidence=_json_list(row.counter_evidence_json),
        suggestion=row.suggestion,
        created_at=row.created_at,
    )


def _t_plan_out(row: TTradePlan) -> TTradePlanOut:
    return TTradePlanOut(
        id=row.id,
        holding_id=row.holding_id,
        trade_date=row.trade_date,
        code=row.code,
        name=row.name,
        t_type=normalize_t_type(row.t_type),
        planned_sell_price=row.planned_sell_price,
        planned_sell_quantity=row.planned_sell_quantity,
        buyback_price_low=row.buyback_price_low,
        buyback_price_high=row.buyback_price_high,
        buyback_conditions=_json_list(row.buyback_conditions_json),
        cancel_conditions=_json_list(row.cancel_conditions_json),
        status=row.status,
        actual_sell_price=row.actual_sell_price,
        actual_buyback_price=row.actual_buyback_price,
        actual_quantity=row.actual_quantity,
        cost_reduction=row.cost_reduction,
        evidence=_json_list(row.evidence_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def quote_for_code(code: str) -> dict[str, Any]:
    try:
        quotes = _latest_a_share_quotes([code])
    except Exception:
        return {}
    return quotes.get(_quote_lookup_code(code, quotes), {})


def _as_naive_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo is None else shanghai_now_naive(value)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo is None else shanghai_now_naive(parsed)


def decision_market_data_status(
    quote: dict[str, Any],
    capture: DataCaptureSnapshot | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dynamically validate a stored quote against the expected market date.

    ``is_stale`` is a capture-time fact.  A snapshot that was realtime
    yesterday becomes historical today, so read paths must recalculate this
    status instead of trusting the persisted boolean forever.
    """

    from app.services.market_data import _limit_up_catcher_expected_trade_date
    from app.services.trading_calendar import is_a_share_trading_day

    current = shanghai_now_naive(now)
    expected_date = _limit_up_catcher_expected_trade_date(current)
    calendar_date = current.date().isoformat()
    current_market_day = bool(
        is_a_share_trading_day(current.date())
        and expected_date == calendar_date
    )
    minute_date = str(quote.get("minute_bar_trade_date") or "").strip()
    provider_event_at = _as_naive_datetime(quote.get("provider_event_at"))
    provider_date = provider_event_at.date().isoformat() if provider_event_at else ""
    capture_date = str(capture.trade_date or "").strip() if capture is not None else ""
    minute_bar_as_of = _as_naive_datetime(quote.get("minute_bar_as_of"))
    minute_rows = list(quote.get("minute_bars") or [])
    if minute_bar_as_of is None and minute_rows and minute_date:
        last_time = str(minute_rows[-1].get("time") or "").strip()
        try:
            minute_bar_as_of = datetime.fromisoformat(
                last_time if "T" in last_time else f"{minute_date}T{last_time}"
            )
        except ValueError:
            minute_bar_as_of = None
    market_evidence_date = minute_date or provider_date
    observed_date = market_evidence_date or capture_date
    conflicting_dates = bool(
        (minute_date and provider_date and minute_date != provider_date)
        or (market_evidence_date and capture_date and market_evidence_date != capture_date)
    )
    quote_times = [value for value in (provider_event_at, minute_bar_as_of) if value is not None]
    quote_as_of = max(quote_times, default=None)
    display_as_of = quote_as_of or (capture.captured_at if capture is not None else None)
    if display_as_of is not None and display_as_of.tzinfo is not None:
        display_as_of = shanghai_now_naive(display_as_of)
    age_seconds = (current - quote_as_of).total_seconds() if quote_as_of is not None else None

    clock = current.time()
    in_continuous_window = (
        time(9, 15) <= clock <= time(11, 30)
        or time(13, 0) <= clock <= time(15, 0)
    )
    in_lunch_window = time(11, 30) < clock < time(13, 0)
    after_close = clock > time(15, 0)

    def anchored_for_window(value: datetime | None) -> bool:
        if value is None or value.date().isoformat() != expected_date:
            return False
        value_age = (current - value).total_seconds()
        if value_age < -60:
            return False
        if in_continuous_window:
            return -60 <= value_age <= 10 * 60
        if in_lunch_window:
            return time(11, 29) <= value.time() <= time(11, 31)
        if after_close:
            return time(14, 59) <= value.time() <= time(15, 1)
        return False

    # A provider's post-close f124 can be later than 15:00 while the minute
    # tape has a valid 15:00 close.  Either independently anchored clock is
    # sufficient for quote freshness; the minute tape retains its own stricter
    # reliability flag below.
    snapshot_fresh = any(
        anchored_for_window(value)
        for value in (provider_event_at, minute_bar_as_of)
        if value is not None
    )
    minute_bar_current = bool(minute_rows and anchored_for_window(minute_bar_as_of))
    has_quote = _safe_float(quote.get("price")) > 0
    current_session = bool(
        has_quote
        and current_market_day
        and market_evidence_date == expected_date
        and not conflicting_dates
        and snapshot_fresh
        and not bool(quote.get("is_delayed_endpoint"))
        and not bool(capture is not None and capture.is_stale)
    )
    minute_close_anchor = bool(
        minute_bar_as_of is not None
        and minute_bar_as_of.date().isoformat() == expected_date
        and time(14, 59) <= minute_bar_as_of.time() <= time(15, 1)
    )
    # Eastmoney's quote timestamp can be refreshed after the 15:00 close
    # (commonly around 16:xx). Accept that bounded close-publication window,
    # but never treat an arbitrary late-night timestamp as exchange evidence.
    provider_close_anchor = bool(
        provider_event_at is not None
        and provider_event_at.date().isoformat() == expected_date
        and time(14, 59) <= provider_event_at.time() <= time(18, 0)
    )
    latest_completed_reference = bool(
        has_quote
        and expected_date < calendar_date
        and market_evidence_date == expected_date
        and not conflicting_dates
        and (minute_close_anchor or provider_close_anchor)
    )
    is_latest_available = bool(current_session or latest_completed_reference)
    if not quote:
        note = "尚无可核验行情快照；请点击查询获取当日数据。"
    elif conflicting_dates:
        note = f"报价、分钟线或存储分区日期不一致（报价{provider_date or '未知'}、分钟线{minute_date or '未知'}、分区{capture_date or '未知'}），已禁止作为当日决策证据。"
    elif bool(quote.get("is_delayed_endpoint")):
        note = "当前仅取得延迟行情端点，已降级为历史参考并禁止生成实时操作结论。"
    elif not market_evidence_date:
        note = "行情缺少提供商事件时间和分钟线交易日，不能用本地入库日期冒充当日行情。"
    elif observed_date != expected_date:
        note = f"当前展示{observed_date or '未知日期'}历史快照；有效行情日应为{expected_date}，请点击查询刷新。"
    elif current_market_day and not snapshot_fresh:
        note = "提供商行情事件时间未贴近当前交易窗口、午间收盘或当日收盘，已按过期证据处理。"
    elif current_session and not minute_bar_current:
        note = f"已核验为{expected_date}当前报价，但分钟线尾点不新鲜，分时均价与量价结论已降级。"
    elif current_session:
        note = f"已核验为{expected_date}行情。"
    elif observed_date == expected_date and not current_market_day:
        note = f"当前尚无{calendar_date}实时交易行情，展示最近完成交易日{observed_date}数据，仅供盘前或复盘参考。"
    else:
        note = "行情日期或时效证据不完整，已禁止作为实时操作依据。"
    return {
        "expected_trade_date": expected_date,
        "market_data_trade_date": observed_date,
        "market_data_as_of": display_as_of,
        "provider_event_at": provider_event_at,
        "data_age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "is_current_session": current_session,
        "is_latest_available": is_latest_available,
        "minute_bar_as_of": minute_bar_as_of,
        "minute_bar_current": minute_bar_current,
        "data_status_note": note,
    }


EXPECTATION_DEFAULTS = {
    "EXTREME_STRONG": (5.0, 9.5),
    "STRONG": (2.0, 5.5),
    "NEUTRAL": (-1.0, 2.0),
    "WEAK": (-4.0, 0.5),
    "REPAIR": (-2.0, 2.5),
    "EBB": (-6.0, -1.0),
}

SCENARIO_LABELS = ("强修复", "弱修复", "延续", "分歧", "退潮")


def _scenario_probabilities(base: str) -> dict[str, float]:
    if base in {"EXTREME_STRONG", "STRONG"}:
        return {"强修复": 0.15, "弱修复": 0.10, "延续": 0.35, "分歧": 0.25, "退潮": 0.15}
    if base in {"WEAK", "EBB"}:
        return {"强修复": 0.10, "弱修复": 0.25, "延续": 0.10, "分歧": 0.20, "退潮": 0.35}
    if base == "REPAIR":
        return {"强修复": 0.25, "弱修复": 0.30, "延续": 0.15, "分歧": 0.20, "退潮": 0.10}
    return {"强修复": 0.15, "弱修复": 0.20, "延续": 0.20, "分歧": 0.30, "退潮": 0.15}


def _scenario_rows(revision: ExpectationRevision) -> list[ExpectationScenario]:
    probability = _scenario_probabilities(revision.base_expectation)
    ranges = {
        "强修复": (max(revision.expected_open_low, 1.0), max(revision.expected_open_high, 3.0)),
        "弱修复": (min(revision.expected_open_low, -1.5), max(revision.expected_open_low, 1.0)),
        "延续": (revision.expected_open_low, revision.expected_open_high),
        "分歧": (min(revision.expected_open_low, -2.0), min(revision.expected_open_high, 1.0)),
        "退潮": (min(revision.expected_open_low, -5.0), min(revision.expected_open_high, -1.0)),
    }
    validation = {
        "强修复": ["竞价高于合理区间中枢", "开盘5分钟站稳真实VWAP", "量能不低于基准"],
        "弱修复": ["低开后快速收回预期下沿", "回踩不破开盘低点", "主动卖压收敛"],
        "延续": ["竞价落在合理区间", "价格与VWAP同向", "题材和个股强弱未背离"],
        "分歧": ["竞价低于区间中枢", "开盘冲高回落", "承接需要二次确认"],
        "退潮": ["竞价跌破预期下沿", "放量跌破结构支撑", "题材订单流方向同步转弱"],
    }
    actions = {
        "强修复": "只按量价确认执行，不追瞬时高点。",
        "弱修复": "保留验证仓，修复失败立即降风险。",
        "延续": "按原计划持有并跟踪失效条件。",
        "分歧": "降低主动进攻仓位，等待方向确认。",
        "退潮": "优先减仓或退出，禁止补仓摊低。",
    }
    return [ExpectationScenario(
        revision_id=revision.id,
        scenario_type=label,
        probability=probability[label],
        expected_low=round(ranges[label][0], 2),
        expected_high=round(ranges[label][1], 2),
        validation_conditions_json=_json_dumps(validation[label]),
        invalid_conditions_json=_json_dumps([f"实际表现不满足“{condition}”" for condition in validation[label][:2]]),
        action_discipline=actions[label],
    ) for label in SCENARIO_LABELS]


def _persist_expectation_revision(db: Session, row: ExpectationSnapshot, trigger: str = "collector") -> ExpectationRevision | None:
    aliases = {row.code, row.code.zfill(6), row.code.lstrip("0")}
    latest = (
        db.query(ExpectationRevision)
        .filter(ExpectationRevision.trade_date == row.trade_date, ExpectationRevision.code.in_(list(aliases)))
        .order_by(ExpectationRevision.version.desc(), ExpectationRevision.id.desc())
        .first()
    )
    volume_query = (
        db.query(VolumePriceSnapshot)
        .filter(
            VolumePriceSnapshot.code.in_(list(aliases)),
        )
    )
    volume = (
        volume_query.filter(VolumePriceSnapshot.trade_date == row.trade_date)
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .first()
    )
    if volume is None and trigger == "close_baseline":
        # A next-day baseline is intentionally derived from the just-finished
        # session, whose trade_date precedes the expectation's validation date.
        volume = (
            volume_query.filter(VolumePriceSnapshot.trade_date <= row.trade_date)
            .order_by(VolumePriceSnapshot.trade_date.desc(), VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
            .first()
        )

    def volume_signal(pattern: str) -> str:
        text = pattern or ""
        if any(value in text for value in ("跌停开板V形修复", "深水开板V形修复", "水下V形反转", "水下V形修复", "重新站回VWAP且低点抬高")):
            return "REVERSAL_CONFIRMED"
        if "深水V形反抽待确认" in text:
            return "REVERSAL_PENDING"
        if "冲高回落跌破VWAP" in text:
            return "VOLUME_PRICE_WEAKENING"
        if "跌破VWAP" in text:
            return "VWAP_BREAKDOWN"
        if "VWAP上方强势" in text:
            return "VWAP_STRONG"
        return text

    current_volume_signal = volume_signal(volume.pattern if volume else "")
    latest_volume_signal = volume_signal(latest.volume_price_state if latest else "")
    # Opening change is an immutable fact for the trading day.  A revision is
    # appended only when the validation stage or a decision-relevant conclusion
    # changes; price ticks and intermittent quote gaps must not create dozens of
    # near-identical versions.
    fingerprint = (
        row.stage,
        row.base_expectation,
        round(row.expected_open_low, 2),
        round(row.expected_open_high, 2),
        row.expectation_result,
        row.state_transition,
        current_volume_signal,
        "usable" if volume and volume.data_quality not in {"missing", "manual"} else "missing",
    )
    latest_fingerprint = (
        latest.stage,
        latest.base_expectation,
        round(latest.expected_open_low, 2),
        round(latest.expected_open_high, 2),
        latest.expectation_result,
        latest.state_transition,
        latest_volume_signal,
        "usable" if latest.data_quality not in {"missing", "manual"} else "missing",
    ) if latest else None
    if latest and fingerprint == latest_fingerprint:
        manual_content_changed = trigger == "manual_update" and (
            latest.evidence_json != row.evidence_json
            or latest.counter_evidence_json != row.counter_evidence_json
            or latest.suggestion != row.suggestion
            or latest.expectation_gap_score != row.expectation_gap_score
        )
        if not manual_content_changed:
            return None
    invalid_conditions = [
        f"竞价/开盘低于 {row.underperform_threshold:+.2f}%",
        "开盘后跌破真实VWAP且量能转弱",
        "关键结构支撑失守后不能快速收回",
    ]
    revision = ExpectationRevision(
        expectation_snapshot_id=int(row.id), previous_revision_id=latest.id if latest else None,
        version=(latest.version + 1) if latest else 1, trade_date=row.trade_date,
        code=row.code, name=row.name, stage=row.stage, trigger=trigger,
        base_expectation=row.base_expectation, expected_open_low=row.expected_open_low,
        expected_open_high=row.expected_open_high, actual_open_pct=row.actual_open_pct,
        actual_change_pct=row.actual_change_pct, expectation_gap_score=row.expectation_gap_score,
        expectation_result=row.expectation_result, state_transition=row.state_transition,
        confidence=row.confidence, volume_price_state=(volume.pattern if volume else ""),
        vwap=(volume.vwap if volume else 0), price_vs_vwap=(volume.price_vs_vwap if volume else 0),
        data_quality=(volume.data_quality if volume else "missing"), evidence_json=row.evidence_json,
        counter_evidence_json=row.counter_evidence_json,
        invalid_conditions_json=_json_dumps(invalid_conditions), suggestion=row.suggestion,
        created_at=shanghai_now_naive(),
    )
    db.add(revision)
    db.flush()
    for scenario in _scenario_rows(revision):
        db.add(scenario)
    return revision


def infer_script_type(base_hint: str) -> str:
    if any(value in base_hint for value in ("打板", "冲板", "首板", "连板")):
        return "breakout"
    if any(value in base_hint for value in ("趋势", "容量", "低吸", "突破")):
        return "trend"
    return "default"


def ensure_expectation_rules(db: Session) -> list[ExpectationRule]:
    if db.query(ExpectationRule).count() == 0:
        for base, (low, high) in EXPECTATION_DEFAULTS.items():
            db.add(ExpectationRule(
                script_type="default",
                stage="*",
                base_expectation=base,
                display_name=f"默认 {base}",
                expected_open_low=low,
                expected_open_high=high,
                outperform_threshold=high + 1.0,
                underperform_threshold=low - 1.0,
                severe_underperform_threshold=min(low - 3.0, -3.0),
                enabled=True,
            ))
        db.commit()
    return db.query(ExpectationRule).order_by(ExpectationRule.script_type, ExpectationRule.stage, ExpectationRule.base_expectation).all()


def expectation_rule_for(
    db: Session,
    script_type: str,
    stage: str,
    base_expectation: str,
    *,
    seed_defaults: bool = True,
) -> ExpectationRule | None:
    if seed_defaults:
        ensure_expectation_rules(db)
    for candidate_script, candidate_stage in ((script_type, stage), (script_type, "*"), ("default", stage), ("default", "*")):
        row = db.query(ExpectationRule).filter(
            ExpectationRule.script_type == candidate_script,
            ExpectationRule.stage == candidate_stage,
            ExpectationRule.base_expectation == base_expectation,
            ExpectationRule.enabled.is_(True),
        ).first()
        if row:
            return row
    return None


def build_expectation_snapshot(
    db: Session,
    code: str,
    name: str = "",
    stage: str = "盘中状态",
    quote: dict[str, Any] | None = None,
    base_hint: str = "",
    persist: bool = True,
) -> ExpectationSnapshotOut:
    stage = stage or current_expectation_stage()
    quote = quote_for_code(code) if quote is None else quote
    existing = db.query(ExpectationSnapshot).filter(
        ExpectationSnapshot.trade_date == _today(),
        ExpectationSnapshot.code.in_([code, code.zfill(6), code.lstrip("0")]),
        ExpectationSnapshot.stage == stage,
    ).order_by(ExpectationSnapshot.created_at.desc(), ExpectationSnapshot.id.desc()).first()
    open_pct = _safe_float(quote.get("open_pct"))
    change_pct = _safe_float(quote.get("change_pct"))
    current = _safe_float(quote.get("price"))
    previous_close = _safe_float(quote.get("prev_close"))
    if not open_pct and quote.get("open") and previous_close:
        open_pct = (_safe_float(quote.get("open")) - previous_close) / previous_close * 100
    quote_is_usable = bool(
        (current > 0 or _safe_float(quote.get("open")) > 0)
        and (previous_close > 0 or quote.get("open_pct") is not None)
    )
    if not quote_is_usable and existing is not None:
        # Keep the last verified stage snapshot when a provider briefly returns
        # an empty quote.  Replacing it with 0% creates alternating +0/+6
        # revisions and can also resurrect a stale sell instruction.
        return _expectation_out(existing)
    base_expectation = base_hint if base_hint in EXPECTATION_DEFAULTS else "NEUTRAL"
    if any(key in base_hint for key in ("一字", "极强", "超强", "核心总龙")):
        base_expectation = "EXTREME_STRONG"
    elif any(key in base_hint for key in ("超预期", "强预期", "主线前排", "打板")):
        base_expectation = "STRONG"
    if any(key in base_hint for key in ("弱于预期", "分歧转弱", "退出")):
        base_expectation = "WEAK"
    if any(key in base_hint for key in ("修复", "低吸")):
        base_expectation = "REPAIR"
    if any(key in base_hint for key in ("退潮", "衰退", "兑现", "禁止")):
        base_expectation = "EBB"

    expected_low, expected_high = EXPECTATION_DEFAULTS[base_expectation]
    outperform = expected_high + 1.0
    underperform = expected_low - 1.0
    severe_under = min(underperform - 2.0, -3.0)
    rule = expectation_rule_for(
        db,
        infer_script_type(base_hint),
        stage,
        base_expectation,
        seed_defaults=persist,
    )
    if rule:
        expected_low = rule.expected_open_low
        expected_high = rule.expected_open_high
        outperform = rule.outperform_threshold
        underperform = rule.underperform_threshold
        severe_under = rule.severe_underperform_threshold

    open_score = 0
    evidence: list[str] = []
    counter: list[str] = []
    if open_pct >= outperform:
        open_score += 10
        evidence.append(f"竞价/开盘 {open_pct:+.2f}% 高于超预期阈值 {outperform:+.2f}%。")
    elif open_pct <= severe_under:
        open_score -= 18
        evidence.append(f"竞价/开盘 {open_pct:+.2f}% 严重低于预期阈值 {severe_under:+.2f}%。")
    elif open_pct <= underperform:
        open_score -= 10
        evidence.append(f"竞价/开盘 {open_pct:+.2f}% 低于预期阈值 {underperform:+.2f}%。")
    else:
        counter.append(f"竞价/开盘 {open_pct:+.2f}% 未明显偏离预期区间。")
    if change_pct >= open_pct + 2:
        open_score += 6
        evidence.append(f"当前涨幅 {change_pct:+.2f}% 较开盘继续走强。")
    if change_pct <= open_pct - 3:
        open_score -= 8
        evidence.append(f"当前涨幅 {change_pct:+.2f}% 较开盘明显走弱。")
    if current <= 0:
        counter.append("实时行情缺口，预期差可信度降低。")

    if not quote_is_usable:
        result, transition, suggestion = "UNKNOWN", "DATA_GAP", "实时行情缺失，保留上一阶段计划，不生成新的买卖结论。"
        evidence = ["实时价格或昨收数据缺失，本阶段不参与预期证伪。"]
        counter = []
        open_score = 0
    elif open_score >= 16:
        result, transition, suggestion = "STRONGER", "STRONG_TO_STRONGER", "超预期强化，只允许按计划确认，不追最高点。"
    elif open_score >= 8:
        result, transition, suggestion = "STRONGER", "WEAK_TO_STRONG", "小幅超预期，等待量价确认后再提高仓位。"
    elif open_score <= -18:
        result, transition, suggestion = "INVALID", "EXPECTATION_INVALIDATED", "显著低于预期，优先降风险，禁止补仓。"
    elif open_score <= -8:
        result, transition, suggestion = "WEAKER", "CONSENSUS_TO_DIVERGENCE", "预期转分歧，观察修复失败就减仓。"
    else:
        result, transition, suggestion = "MATCHED", "MATCHED", "基本符合预期，按原计划和失效条件执行。"
    latest_volume = (
        db.query(VolumePriceSnapshot)
        .filter(
            VolumePriceSnapshot.trade_date == _today(),
            VolumePriceSnapshot.code.in_([code, code.zfill(6), code.lstrip("0")]),
        )
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .first()
    )
    volume_pattern = str(latest_volume.pattern or "") if latest_volume else ""
    reversal_confirmed = any(value in volume_pattern for value in (
        "跌停开板V形修复", "深水开板V形修复", "水下V形反转",
        "水下V形修复", "重新站回VWAP且低点抬高",
    ))
    reversal_pending = "深水V形反抽待确认" in volume_pattern
    if reversal_confirmed:
        prior_transition = transition
        transition = "INVALIDATION_TO_REVERSAL" if result in {"WEAKER", "INVALID"} else "INTRADAY_REVERSAL_CONFIRMED"
        suggestion = "盘中V形修复已确认：暂缓沿用低点时的减仓结论，禁止追高；再次跌破真实VWAP和抬高后的次低点才恢复降风险。"
        evidence.extend(_json_list(latest_volume.evidence_json)[:3])
        counter.append(f"开盘预期结论仍为{result}（原状态{prior_transition}），但盘中新增反转证据，执行建议已动态修正。")
    elif reversal_pending:
        transition = "INTRADAY_REVERSAL_PENDING"
        suggestion = "价格已脱离深水低点，但V形反转尚待VWAP与次低点确认；不追高，确认失败仍按原风险计划处理。"
        evidence.extend(_json_list(latest_volume.evidence_json)[:2])
    confidence, coverage_evidence, coverage_counter = expectation_evidence_coverage(
        quote=quote,
        volume=latest_volume,
        reference_trade_date=_today(),
    )
    evidence.extend(coverage_evidence)
    counter.extend(coverage_counter)
    row = existing
    if row is None:
        row = ExpectationSnapshot(trade_date=_today(), code=code, stage=stage)
    row.name = name or code
    row.base_expectation = base_expectation
    row.expected_open_low = expected_low
    row.expected_open_high = expected_high
    row.outperform_threshold = outperform
    row.underperform_threshold = underperform
    row.severe_underperform_threshold = severe_under
    row.actual_open_pct = round(open_pct, 2)
    row.actual_change_pct = round(change_pct, 2)
    row.expectation_gap_score = open_score
    row.expectation_result = result
    row.state_transition = transition
    row.confidence = confidence
    row.evidence_json = _json_dumps(evidence)
    row.counter_evidence_json = _json_dumps(counter)
    row.suggestion = suggestion
    row.created_at = shanghai_now_naive()
    if persist:
        db.add(row)
        db.flush()
        _persist_expectation_revision(db, row, trigger=stage)
        db.commit()
        db.refresh(row)
    return _expectation_out(row)


def create_expectation_snapshot(db: Session, payload: ExpectationSnapshotIn) -> ExpectationSnapshotOut:
    quote = quote_for_code(payload.code)
    if payload.actual_open_pct is not None:
        quote["open_pct"] = payload.actual_open_pct
    if payload.actual_change_pct is not None:
        quote["change_pct"] = payload.actual_change_pct
    return build_expectation_snapshot(
        db,
        payload.code,
        name=payload.name,
        stage=payload.stage or current_expectation_stage(),
        quote=quote,
        base_hint=payload.base_hint,
        persist=payload.persist,
    )


def update_expectation_snapshot(
    db: Session,
    row: ExpectationSnapshot,
    payload: ExpectationSnapshotUpdate,
) -> ExpectationSnapshotOut:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if value is None:
            continue
        if key == "evidence":
            row.evidence_json = _json_dumps(value)
        elif key == "counter_evidence":
            row.counter_evidence_json = _json_dumps(value)
        else:
            setattr(row, key, value)
    db.add(row)
    db.flush()
    _persist_expectation_revision(db, row, trigger="manual_update")
    db.commit()
    db.refresh(row)
    return _expectation_out(row)


def build_t_eligibility(db: Session, holding: Holding) -> TEligibilityOut:
    return engine_build_t_eligibility(db, holding)


def create_t_plan(db: Session, holding: Holding, payload: TTradePlanIn | None = None) -> TTradePlanOut:
    return engine_create_t_plan(db, holding, payload)


def update_t_plan(db: Session, row: TTradePlan, payload: Any) -> TTradePlanOut:
    return engine_update_t_plan(db, row, payload)


def _entry_plan_context(db: Session, code: str, *, is_holding: bool) -> tuple[bool, bool, NextDayPlan | None]:
    """Return whether today's plan explicitly permits a new/add order.

    Holding membership and watch-list membership are deliberately ignored.  A
    holding needs an explicit buyback plan; a non-holding needs a generated
    limit-up plan whose mainline/stage rules still allow a position.
    """

    normalized = str(code or "").strip()
    candidates = {normalized, normalized.lstrip("0")}
    plan = (
        db.query(NextDayPlan)
        .filter(
            NextDayPlan.code.in_([item for item in candidates if item]),
            NextDayPlan.plan_date == _today(),
            NextDayPlan.plan_type == ("holding" if is_holding else "limit_up_auction"),
        )
        .order_by(NextDayPlan.updated_at.desc(), NextDayPlan.id.desc())
        .first()
    )
    if not plan:
        return False, False, None
    if is_holding:
        permitted = bool(
            plan.allow_buyback
            and int(plan.max_buyback_quantity or 0) > 0
            and str(plan.buyback_condition or "").strip()
        )
        return permitted, permitted, plan

    auction = _json_dict(plan.auction_plan)
    approved_cap = _safe_float(
        auction.get("max_position_ratio")
        if auction.get("max_position_ratio") is not None
        else auction.get("approved_position_cap")
    )
    is_mainline = auction.get("is_mainline") is True
    has_plan = bool(
        float(plan.confirm_price or 0) > 0
        and float(plan.final_risk_price or 0) > 0
        and str(plan.expected_condition or "").strip()
        and str(plan.underperform_condition or "").strip()
    )
    # A generated plan outside the mainline/stage boundary remains a research
    # record, not an executable trading mode.
    mode_match = bool(has_plan and approved_cap > 0 and is_mainline)
    return has_plan, mode_match, plan


def _entry_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    dumper = getattr(value, "model_dump", None)
    if callable(dumper):
        dumped = dumper()
        return dumped if isinstance(dumped, dict) else {}
    return {}


def _entry_plan_conditions(
    plan: NextDayPlan,
    quote: dict[str, Any],
    expectation: Any,
    volume_price: Any,
    sector_context: dict[str, Any],
    market_context: dict[str, Any],
    *,
    is_holding: bool,
    risk_reward_passed: bool,
) -> tuple[bool, list[str]]:
    """Evaluate only observable plan clauses; unknown prose is fail-closed."""

    expectation_data = _entry_object(expectation)
    volume_data = _entry_object(volume_price)
    auction = _json_dict(plan.auction_plan)
    current = _safe_float(quote.get("price"))
    open_price = _safe_float(quote.get("open"))
    prev_close = _safe_float(quote.get("prev_close"))
    open_pct = ((open_price / prev_close - 1) * 100) if open_price > 0 and prev_close > 0 else None
    vwap = _safe_float(volume_data.get("vwap"))
    volume_quality = str(volume_data.get("data_quality") or "").lower()
    vwap_reliable = bool(volume_data.get("vwap_reliable")) and vwap > 0 and volume_quality not in {"", "missing", "manual"}
    above_vwap = bool(vwap_reliable and current >= vwap * 0.998)
    pattern = str(volume_data.get("pattern") or "").upper()
    positive_pattern = any(
        token in pattern
        for token in ("V形", "低点抬高", "站回VWAP", "重新站回", "回踩不破", "支撑确认", "REVERSAL_CONFIRMED")
    )
    weak_pattern = any(
        token in pattern
        for token in ("冲高回落", "跌破VWAP", "量价转弱", "放量下跌", "VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING")
    )
    retest_confirmed = bool(above_vwap and positive_pattern)

    sector_text = " ".join(
        str(sector_context.get(key) or "")
        for key in ("status", "flow_turning", "turning", "signal", "flow_signal")
    ).upper()
    sector_ready = sector_context.get("crowding_evaluated") is True
    sector_negative = any(
        token in sector_text
        for token in ("OUTFLOW_ACCELERATING", "TURN_TO_OUTFLOW", "INFLOW_FADING", "退潮", "流出", "转弱", "弱势")
    )
    sector_supportive = sector_ready and not sector_negative and any(
        token in sector_text
        for token in ("INFLOW_ACCELERATING", "TURN_TO_INFLOW", "FLOW_IMPROVING", "前排", "健康", "修复", "企稳", "转强", "流入")
    )
    market_open = not bool(market_context.get("expansion_frozen")) and str(market_context.get("entry_gate") or "").upper() not in {"", "BLOCK"}

    expectation_text = " ".join(
        str(expectation_data.get(key) or "")
        for key in ("expectation_result", "state_transition", "actual_performance", "status")
    ).upper()
    gap = _safe_float(expectation_data.get("expectation_gap_score"))
    expectation_negative = gap <= -8 or any(
        token in expectation_text
        for token in ("INVALIDATED", "WEAKER", "SEVERE_UNDERPERFORM", "预期证伪", "弱于预期", "转弱")
    )

    expected_text = str(plan.expected_condition or "").strip()
    underperform_text = str(plan.underperform_condition or "").strip()
    buyback_text = str(plan.buyback_condition or "").strip() if is_holding else ""
    cancel_text = str(auction.get("cancel_condition") or "").strip()
    keep_text = str(auction.get("keep_order_condition") or "").strip() if not is_holding else ""
    required_texts = [expected_text, underperform_text, cancel_text]
    if is_holding:
        required_texts.append(buyback_text)
    else:
        required_texts.append(keep_text)
    structured_markers = (
        "VWAP", "分时均价", "回踩", "承接", "确认位", "高开", "低开", "板块", "资金", "风险收益",
        "止损", "撤单", "炸板", "回封", "支撑", "RETEST", "SUPPORT", "BREAKOUT",
    )
    unstructured = [text for text in required_texts if not text or not any(marker in text.upper() for marker in structured_markers)]
    reasons: list[str] = []
    if unstructured:
        reasons.append("计划存在空白或无法可靠结构化的条件，系统不猜测其含义。")

    combined_positive = " ".join([expected_text, buyback_text, keep_text]).upper()
    if any(token in combined_positive for token in ("全市场", "市场闸门")) and not market_open:
        reasons.append("计划要求全市场扩仓闸门开放，当前未通过。")
    if any(token in combined_positive for token in ("板块", "题材", "主线")) and not sector_supportive:
        reasons.append("计划要求板块/题材同步转强或保持前排，当前没有可验证的正向订单流方向证据。")
    if any(token in combined_positive for token in ("VWAP", "分时均价", "回踩", "承接", "V形", "低点抬高", "RETEST", "SUPPORT")) and not retest_confirmed:
        reasons.append("计划要求回踩承接、V形/低点抬高并站回真实VWAP，当前尚未确认。")
    opening_range = re.search(r"高开\s*(\d+(?:\.\d+)?)%\s*[-~～至]\s*(\d+(?:\.\d+)?)%", expected_text)
    if opening_range:
        low, high = float(opening_range.group(1)), float(opening_range.group(2))
        if open_pct is None or not low <= open_pct <= high:
            actual = "缺失" if open_pct is None else f"{open_pct:+.2f}%"
            reasons.append(f"计划要求高开{low:g}%-{high:g}%，实际开盘为{actual}。")

    invalidation_active = bool(
        expectation_negative
        or weak_pattern
        or sector_negative
        or (float(plan.final_risk_price or 0) > 0 and current <= float(plan.final_risk_price or 0))
        or (float(plan.reduce_price or 0) > 0 and current <= float(plan.reduce_price or 0))
    )
    if invalidation_active:
        reasons.append("弱于预期/撤单条件已触发：预期、量价、板块或风险位至少一项失效。")
    if not risk_reward_passed:
        reasons.append("计划风险收益比未达到1.50。")
    return not reasons, reasons


def _entry_plan_execution_context(
    plan: NextDayPlan | None,
    quote: dict[str, Any],
    *,
    is_holding: bool,
    expectation: Any = None,
    volume_price: Any = None,
    sector_context: dict[str, Any] | None = None,
    market_context: dict[str, Any] | None = None,
    account_total_asset: float | None = None,
) -> dict[str, Any]:
    """Validate the live trigger, observable plan clauses, odds and cap."""

    if plan is None:
        return {
            "triggered": None,
            "risk_reward_passed": None,
            "risk_reward": None,
            "position_cap_pct": None,
            "evidence": [],
        }
    current = _safe_float(quote.get("price"))
    stop = float(plan.final_risk_price or 0)
    auction = _json_dict(plan.auction_plan)
    raw_cap = _safe_float(
        auction.get("max_position_ratio")
        if auction.get("max_position_ratio") is not None
        else auction.get("approved_position_cap")
    )
    position_cap_pct = None
    if not is_holding:
        position_cap_pct = raw_cap * 100 if 0 < raw_cap <= 1 else raw_cap
        position_cap_pct = max(0.0, min(100.0, position_cap_pct))
    elif current > 0 and float(account_total_asset or 0) > 0 and int(plan.max_buyback_quantity or 0) > 0:
        position_cap_pct = min(
            100.0,
            int(plan.max_buyback_quantity or 0) * current / float(account_total_asset) * 100,
        )

    if is_holding:
        trigger_price = float(plan.buyback_price or 0)
        price_triggered = bool(
            current > 0
            and trigger_price > 0
            and current <= trigger_price * 1.005
            and (stop <= 0 or current > stop)
        )
        trigger_label = f"买回触发价 {trigger_price:.2f}"
    else:
        trigger_price = float(plan.confirm_price or 0)
        limit_price = float(plan.limit_up_price or auction.get("limit_up_price") or 0)
        price_triggered = bool(
            current > 0
            and trigger_price > 0
            and current >= trigger_price * 0.997
            and (limit_price <= 0 or current <= limit_price * 1.001)
            and (stop <= 0 or current > stop)
        )
        trigger_label = f"确认触发价 {trigger_price:.2f}"

    target_candidates = [
        float(plan.trim_price or 0),
        float(plan.limit_up_price or auction.get("limit_up_price") or 0),
    ]
    target = min((value for value in target_candidates if value > current), default=0.0)
    risk = current - stop if current > 0 and stop > 0 and current > stop else 0.0
    reward = target - current if target > current else 0.0
    risk_reward = reward / risk if risk > 0 and reward > 0 else None
    risk_reward_passed = bool(risk_reward is not None and risk_reward >= 1.5)
    conditions_passed, condition_reasons = _entry_plan_conditions(
        plan,
        quote,
        expectation,
        volume_price,
        sector_context or {},
        market_context or {},
        is_holding=is_holding,
        risk_reward_passed=risk_reward_passed,
    )
    triggered = bool(price_triggered and conditions_passed)
    evidence = [
        f"计划实时校验：现价 {current:.2f}，{trigger_label}，风险位 {stop:.2f}。",
        (
            f"计划目标位 {target:.2f}，风险收益比 {risk_reward:.2f}（门槛1.50）。"
            if risk_reward is not None
            else "计划缺少高于现价的目标位或有效风险位，风险收益比无法通过。"
        ),
        *[f"计划条件未通过：{reason}" for reason in condition_reasons],
    ]
    return {
        "triggered": triggered,
        "risk_reward_passed": risk_reward_passed,
        "risk_reward": round(risk_reward, 3) if risk_reward is not None else None,
        "position_cap_pct": position_cap_pct,
        "evidence": evidence,
    }


def _market_entry_context(db: Session, theme: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    latest = (
        db.query(MarketRegimeSnapshot)
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )
    if not latest:
        return (
            {
                "entry_gate": "BLOCK",
                "risk_level": "UNKNOWN",
                "regime": "MISSING",
                "data_quality": "missing",
                "expansion_frozen": True,
            },
            {"status": "数据不足"},
        )

    forbidden = _json_list(latest.forbidden_actions_json)
    stale = str(latest.trade_date or "") != _today()
    captured_at = latest.captured_at
    if captured_at and captured_at.tzinfo is not None:
        captured_at = shanghai_now_naive(captured_at)
    snapshot_age_seconds = (
        (shanghai_now_naive() - captured_at).total_seconds()
        if captured_at
        else None
    )
    snapshot_expired = bool(
        snapshot_age_seconds is None
        or snapshot_age_seconds > timedelta(minutes=15).total_seconds()
        or snapshot_age_seconds < -60
    )
    blocked_regimes = {"EXTREME_SHRINK_DECLINE", "VOLUME_SELL_OFF", "UNKNOWN"}
    explicit_open_block = any(
        token in str(item)
        for item in forbidden
        for token in ("禁止新开仓", "禁止开仓", "冻结扩仓", "禁止主动扩大仓位")
    )
    missing_quality = str(latest.data_quality or "").lower() in {"", "missing", "unavailable"}
    blocked = stale or snapshot_expired or missing_quality or latest.regime_code in blocked_regimes or explicit_open_block
    market_context = {
        "entry_gate": "BLOCK" if blocked else "OPEN_WITH_DISCIPLINE",
        "risk_level": latest.risk_level,
        "regime": latest.regime_code,
        "status": latest.regime_name,
        "data_quality": "stale" if stale or snapshot_expired else latest.data_quality,
        "expansion_frozen": blocked,
        "captured_at": latest.captured_at.isoformat() if latest.captured_at else None,
        "age_seconds": round(snapshot_age_seconds, 1) if snapshot_age_seconds is not None else None,
    }

    theme_names = {
        str(theme.get("industry") or "").strip(),
        *[str(item).strip() for item in theme.get("concepts", []) if str(item).strip()],
    }
    try:
        strongest_raw = json.loads(latest.strongest_sectors_json or "[]")
    except Exception:
        strongest_raw = []
    try:
        weakest_raw = json.loads(latest.weakest_sectors_json or "[]")
    except Exception:
        weakest_raw = []
    strongest = strongest_raw if isinstance(strongest_raw, list) else []
    weakest = weakest_raw if isinstance(weakest_raw, list) else []

    def names(rows: list[Any]) -> set[str]:
        output: set[str] = set()
        for row in rows:
            if isinstance(row, dict):
                output.add(str(row.get("name") or row.get("sector") or "").strip())
            else:
                output.add(str(row).strip())
        return {item for item in output if item}

    strongest_names = names(strongest)
    weakest_names = names(weakest)
    sector_context: dict[str, Any] = {
        "status": "中性或尚未形成可确认方向",
        "crowding_evaluated": False,
        "temperature_data_quality": "missing",
    }
    if theme_names & weakest_names:
        sector_context.update(
            status="板块订单流弱势",
            flow_turning="OUTFLOW_ACCELERATING",
        )
    elif theme_names & strongest_names:
        sector_context.update(
            status="板块订单流前排",
            flow_turning="INFLOW_ACCELERATING",
        )

    matched_temperature: dict[str, Any] | None = None
    board_types: list[str] = []
    if str(theme.get("industry") or "").strip():
        board_types.append("行业")
    if any(str(item).strip() for item in theme.get("concepts", [])):
        board_types.append("概念")
    for board_type in board_types:
        cached = _get_response_cache(f"sector-temperature|{board_type}")
        cached_items = cached.get("items") if isinstance(cached, dict) else getattr(cached, "items", None)
        for item in cached_items or []:
            data = item.model_dump() if hasattr(item, "model_dump") else item
            if not isinstance(data, dict) or str(data.get("name") or "").strip() not in theme_names:
                continue
            if matched_temperature is None or int(data.get("heat_score") or 0) > int(matched_temperature.get("heat_score") or 0):
                matched_temperature = data
    if matched_temperature:
        temperature_quality = str(matched_temperature.get("data_quality") or "missing").lower()
        crowding_evaluated = temperature_quality in {"high", "good"}
        distribution_level = str(
            matched_temperature.get("distribution_risk_level") or "UNKNOWN"
        ).upper()
        distribution_state = str(
            matched_temperature.get("distribution_state") or "数据不足"
        )
        sector_context.update(
            name=str(matched_temperature.get("name") or ""),
            board_type=str(matched_temperature.get("board_type") or ""),
            status=str(matched_temperature.get("status") or sector_context["status"]),
            heat_status=str(matched_temperature.get("status") or ""),
            heat_score=int(matched_temperature.get("heat_score") or 0),
            flow_turning=matched_temperature.get("flow_turning") or sector_context.get("flow_turning"),
            margin_score=matched_temperature.get("margin_score"),
            attention_score=matched_temperature.get("attention_score"),
            distribution_state=distribution_state,
            distribution_risk_level=distribution_level,
            distribution_risk_score=int(matched_temperature.get("distribution_risk_score") or 0),
            distribution_risk=distribution_level in {"HIGH", "CRITICAL"},
            order_flow_exhausted=bool(matched_temperature.get("order_flow_exhausted")),
            leverage_crowding=bool(matched_temperature.get("leverage_crowding")),
            price_response_weak=bool(matched_temperature.get("price_response_weak")),
            distribution_confirmation_count=int(
                matched_temperature.get("distribution_confirmation_count") or 0
            ),
            distribution_evidence=list(matched_temperature.get("distribution_evidence") or []),
            distribution_counter_evidence=list(
                matched_temperature.get("distribution_counter_evidence") or []
            ),
            distribution_actions=list(matched_temperature.get("distribution_actions") or []),
            overheated=(
                "过热" in str(matched_temperature.get("status") or "")
                or distribution_state == "高位派发风险"
            ),
            crowding_evaluated=crowding_evaluated,
            temperature_data_quality=temperature_quality,
            provider_trade_date=matched_temperature.get("provider_trade_date"),
            updated_at=matched_temperature.get("updated_at"),
        )
    return market_context, sector_context


def _persisted_quote_for_code(
    db: Session,
    code: str,
) -> tuple[dict[str, Any], DataCaptureSnapshot | None]:
    """Return the newest *market event* snapshot without provider I/O.

    Collector receipt time is not market time.  A delayed endpoint can be
    polled today and therefore have a newer ``captured_at`` than yesterday's
    valid close while its underlying quote is two sessions older.  Current
    session evidence is preferred first; historical fallback is ranked by the
    provider/minute trade date and event time, never by poll time alone.
    """

    normalized = code.zfill(6)
    rows = (
        db.query(DataCaptureSnapshot)
        .filter(
            DataCaptureSnapshot.target_code.in_([code, normalized, normalized.lstrip("0")]),
            DataCaptureSnapshot.data_type.in_(["stock_minute", "tracked_stock_minute"]),
        )
        .order_by(DataCaptureSnapshot.captured_at.desc(), DataCaptureSnapshot.id.desc())
        .limit(50)
        .all()
    )
    if not rows:
        return {}, None
    expected_date = str(decision_market_data_status({}, None)["expected_trade_date"] or "")
    historical_candidates: list[
        tuple[str, datetime, datetime, int, dict[str, Any], DataCaptureSnapshot]
    ] = []
    future_candidates: list[
        tuple[str, datetime, datetime, int, dict[str, Any], DataCaptureSnapshot]
    ] = []
    for row in rows:
        try:
            value = json.loads(row.raw_value_json or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(value, dict) or _safe_float(value.get("price")) <= 0:
            continue
        status = decision_market_data_status(value, row)
        if status["is_current_session"]:
            return value, row
        evidence_date = str(status["market_data_trade_date"] or "")
        if not evidence_date:
            evidence_date = str(row.trade_date or "")
        event_at = (
            _as_naive_datetime(value.get("provider_event_at"))
            or _as_naive_datetime(value.get("minute_bar_as_of"))
            or datetime.min
        )
        captured_at = _as_naive_datetime(row.captured_at) or datetime.min
        candidate = (evidence_date, event_at, captured_at, int(row.id or 0), value, row)
        # A future-dated row must remain visible to the data-quality guard so
        # it can report FUTURE_TIMESTAMP, but it may never outrank a usable
        # current/historical snapshot.
        if expected_date and evidence_date and evidence_date > expected_date:
            future_candidates.append(candidate)
        else:
            historical_candidates.append(candidate)
    if historical_candidates:
        _date, _event_at, _captured_at, _id, value, row = max(
            historical_candidates,
            key=lambda item: item[:4],
        )
        return value, row
    if future_candidates:
        _date, _event_at, _captured_at, _id, value, row = max(
            future_candidates,
            key=lambda item: item[:4],
        )
        return value, row
    # Keep the newest failed capture for diagnostics, but never let its empty
    # payload masquerade as the stock's latest usable market evidence.
    return {}, rows[0]


def _persisted_volume_row(
    db: Session,
    code: str,
    *,
    trade_date: str = "",
) -> VolumePriceSnapshot | None:
    normalized = code.zfill(6)
    query = db.query(VolumePriceSnapshot).filter(
        VolumePriceSnapshot.code.in_([code, normalized, normalized.lstrip("0")])
    )
    if trade_date:
        query = query.filter(VolumePriceSnapshot.trade_date == trade_date)
    rows = (
        query
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .limit(50)
        .all()
    )
    for row in rows:
        quality = str(row.data_quality or "").strip().lower()
        if row.price > 0 and quality not in {"", "missing", "unavailable", "manual"}:
            return row
    # A provider failure can persist a diagnostic zero-price row after a valid
    # snapshot.  Returning that newest failure would make the decision card
    # look like it has rolled back to yesterday even though today's quote was
    # already collected.  When no usable row exists the caller rebuilds a
    # non-persistent read model from the selected usable capture instead.
    return None


def _persisted_expectation_row(
    db: Session,
    code: str,
    stage: str,
    *,
    trade_date: str,
) -> ExpectationSnapshot | None:
    normalized = code.zfill(6)
    candidates = [code, normalized, normalized.lstrip("0")]
    row = (
        db.query(ExpectationSnapshot)
        .filter(
            ExpectationSnapshot.code.in_(candidates),
            ExpectationSnapshot.stage == stage,
            ExpectationSnapshot.trade_date == trade_date,
        )
        .order_by(ExpectationSnapshot.created_at.desc(), ExpectationSnapshot.id.desc())
        .first()
    )
    if row is not None:
        return row
    return None


def _daily_metrics_from_volume(row: VolumePriceSnapshot | None) -> dict[str, float]:
    if row is None:
        return {}
    return {
        "ma5": float(row.ma5 or 0),
        "ma10": float(row.ma10 or 0),
        "ma20": float(row.ma20 or 0),
        "return_5d": float(row.return_5d or 0),
        "return_10d": float(row.return_10d or 0),
        "distance_recent_high_pct": float(row.distance_recent_high_pct or 0),
        "historical_volume_ratio": float(row.historical_volume_ratio or 0),
    }


def decision_card(db: Session, code: str) -> StockDecisionCardOut:
    from app.services.consensus_risk import build_consensus_risk
    holding = _find_holding_by_code(db, code)
    quote, capture = _persisted_quote_for_code(db, code)
    now = shanghai_now_naive()
    market_data_status = decision_market_data_status(quote, capture, now=now)
    name = holding.name if holding else str(quote.get("name") or code)
    theme = (
        _holding_theme_profile(holding, allow_network=False)
        if holding
        else {"industry": "", "concepts": [], "source": "quote-only"}
    )
    base_hint = holding.position_type if holding else ""
    stage = current_expectation_stage(now)
    baseline = (
        db.query(ExpectationSnapshot)
        .filter(
            ExpectationSnapshot.code.in_([code, code.lstrip("0")]),
            ExpectationSnapshot.stage == "次日盘前预期",
            ExpectationSnapshot.trade_date >= _today(),
        )
        .order_by(ExpectationSnapshot.trade_date.asc(), ExpectationSnapshot.created_at.desc())
        .first()
    )
    # A decision card is a read model.  It may calculate from the newest quote,
    # but opening or switching to the page must never append snapshots,
    # revisions, evidence events or recommendations.  The scheduled collector
    # and explicit collection POST own persistence.
    volume_row = _persisted_volume_row(
        db,
        code,
        trade_date=str(market_data_status["market_data_trade_date"] or ""),
    )
    daily_metrics = _daily_metrics_from_volume(volume_row)
    volume_price = (
        _snapshot_out(volume_row)
        if volume_row is not None
        else build_volume_price_snapshot(
            db, code, name=name, stage=stage, quote=quote,
            daily_metrics=daily_metrics, persist=False,
        )
    )
    if not market_data_status["is_current_session"]:
        is_prior_close = bool(
            market_data_status["market_data_trade_date"]
            and market_data_status["market_data_trade_date"] < now.date().isoformat()
            and volume_price.vwap_reliable
        )
        volume_price = volume_price.model_copy(update={
            "data_quality": "historical_close" if is_prior_close else "historical",
            "vwap_reliable": bool(is_prior_close),
        })
    elif not market_data_status["minute_bar_current"]:
        volume_price = volume_price.model_copy(update={
            "data_quality": "partial",
            "vwap_reliable": False,
        })
    expectation_row = _persisted_expectation_row(
        db,
        code,
        stage,
        trade_date=_today(now),
    )
    if expectation_row is not None:
        expectation = _expectation_out(expectation_row)
    elif baseline:
        expectation = _expectation_out(baseline)
    else:
        expectation = build_expectation_snapshot(
            db, code, name=name, stage=stage, quote=quote,
            base_hint=base_hint, persist=False,
        )
    consensus_risk = build_consensus_risk(quote, expectation, volume_price, daily_metrics)
    cumulative_amount = 0.0
    cumulative_volume = 0.0
    minute_chart = []
    for item in quote.get("minute_bars") or []:
        price = _safe_float(item.get("price") or item.get("close"))
        volume_value = _safe_float(item.get("volume"))
        amount_value = _safe_float(item.get("amount")) or price * volume_value
        if price <= 0 or volume_value <= 0:
            continue
        cumulative_amount += amount_value
        cumulative_volume += volume_value
        minute_chart.append({
            "time": str(item.get("time") or ""), "price": price,
            "vwap": round(cumulative_amount / cumulative_volume, 4),
            "amount": round(amount_value / 1e8, 4),
            "amount_estimated": bool(item.get("amount_estimated") or quote.get("minute_amount_estimated")),
        })
    execution = build_position_execution_state(
        db,
        holding,
        quote=quote,
        expectation=expectation,
        volume_price=volume_price,
        persist=False,
    ) if holding else None
    t_eligibility = build_t_eligibility(db, holding) if holding else None
    has_plan, mode_match, entry_plan = _entry_plan_context(db, code, is_holding=bool(holding))
    if entry_plan and not holding:
        auction = _json_dict(entry_plan.auction_plan)
        if not str(theme.get("industry") or "").strip():
            theme["industry"] = str(auction.get("industry") or "").strip()
        if not theme.get("concepts"):
            theme["concepts"] = [str(item) for item in auction.get("concepts", []) if str(item).strip()]
    market_context, sector_context = _market_entry_context(db, theme)
    if execution and str(execution.sector_state or "").strip():
        sector_context["status"] = execution.sector_state
    plan_execution = _entry_plan_execution_context(
        entry_plan,
        quote,
        is_holding=bool(holding),
        expectation=expectation,
        volume_price=volume_price,
        sector_context=sector_context,
        market_context=market_context,
        account_total_asset=(
            float(holding.total_asset or 0) or _read_account_total_asset(db)
            if holding
            else None
        ),
    )
    entry_discipline = evaluate_entry_gate(
        code,
        quote,
        expectation,
        volume_price,
        consensus_risk,
        sector_context,
        market_context,
        is_holding=bool(holding),
        has_plan=has_plan,
        mode_match=mode_match,
        plan_triggered=plan_execution["triggered"],
        risk_reward_passed=plan_execution["risk_reward_passed"],
        plan_position_cap_pct=plan_execution["position_cap_pct"],
        now=now,
    )
    minute_trade_date = str(quote.get("minute_bar_trade_date") or _today(now))
    try:
        minute_date = datetime.fromisoformat(minute_trade_date).date()
    except ValueError:
        minute_date = None
    today_date = datetime.fromisoformat(_today(now)).date()
    historical_close_mode = minute_date is not None and minute_date < today_date
    effective_now = now
    if historical_close_mode:
        effective_now = datetime.combine(minute_date, time(15, 0))
    effective_flow = analyze_effective_flow(
        quote.get("minute_bars") or [],
        now=effective_now,
        trade_date=minute_trade_date,
        vwap=float(volume_price.vwap or 0),
        vwap_reliable=bool(volume_price.vwap_reliable),
        data_quality=(
            "missing_trade_date"
            if minute_date is None
            else "historical_close"
            if historical_close_mode and bool(volume_price.vwap_reliable)
            else str(volume_price.data_quality or "missing")
        ),
        active_flow_source=str(volume_price.active_flow_source or "unavailable"),
        active_flow_estimated=bool(volume_price.active_flow_estimated),
    )
    signed_flow_yi = (
        float(effective_flow.signed_active_flow) / 1e8
        if effective_flow.signed_active_flow is not None else None
    )
    impact_per_yi = None
    if signed_flow_yi is not None and abs(signed_flow_yi) >= 0.01 and effective_flow.price_response_pct is not None:
        impact_per_yi = abs(float(effective_flow.price_response_pct)) / abs(signed_flow_yi)
    state_severity = {
        "ATTACK_CONFIRMED": "POSITIVE",
        "ABSORPTION_CANDIDATE": "WATCH",
        "RECOVERY_CANDIDATE": "WATCH",
        "DISTRIBUTION_RISK": "HIGH",
        "OUTFLOW_CONFIRMED": "HIGH",
        "LIQUIDITY_SHOCK": "HIGH",
        "INCONCLUSIVE": "UNKNOWN",
        "INSUFFICIENT_DATA": "UNKNOWN",
    }
    source_label = {
        "provider_tick_direction": "东方财富逐笔成交方向分类（非账户身份）",
        "eastmoney_tick": "东方财富逐笔成交方向分类（非账户身份）",
        "minute_price_direction_estimate": "分钟价格方向估算（非逐笔成交）",
    }.get(str(effective_flow.active_flow_source or ""), "成交方向数据口径待确认")
    if historical_close_mode:
        source_label = f"{minute_trade_date} 收盘窗口 · {source_label}"
    flow_evidence = list(effective_flow.evidence)
    if (
        effective_flow.state != "INSUFFICIENT_DATA"
        and effective_flow.active_buy_amount is not None
        and effective_flow.active_sell_amount is not None
        and effective_flow.signed_active_flow is not None
    ):
        readable_flow = (
            f"最近{int(effective_flow.exact_flow_bar_count or 0)}个分钟样本：主动买入方向估算 "
            f"{float(effective_flow.active_buy_amount) / 1e8:.2f} 亿，主动卖出方向估算 "
            f"{float(effective_flow.active_sell_amount) / 1e8:.2f} 亿，方向差额 "
            f"{float(effective_flow.signed_active_flow) / 1e8:+.2f} 亿。"
        )
        flow_evidence = [readable_flow, *flow_evidence[1:]]
    effective_capital = {
        "state": effective_flow.state,
        "state_label": effective_flow.state_label,
        "confidence": effective_flow.confidence,
        "state_severity": state_severity.get(effective_flow.state, "UNKNOWN"),
        "data_quality": (
            "realtime" if effective_flow.data_quality in {"realtime", "realtime_exact"}
            else "historical_close" if effective_flow.data_quality == "historical_close"
            else effective_flow.data_quality
        ),
        "source_label": source_label,
        "as_of": effective_flow.as_of,
        "estimated": bool(effective_flow.active_flow_estimated),
        "metrics": {
            "sample_count": int(effective_flow.exact_flow_bar_count or 0),
            "window_minutes": int(effective_flow.window_minutes or 0),
            "active_buy_yi": (
                float(effective_flow.active_buy_amount) / 1e8
                if effective_flow.active_buy_amount is not None else None
            ),
            "active_sell_yi": (
                float(effective_flow.active_sell_amount) / 1e8
                if effective_flow.active_sell_amount is not None else None
            ),
            "signed_flow_yi": signed_flow_yi,
            "buy_ratio": effective_flow.buy_ratio,
            "active_flow_coverage_ratio": effective_flow.active_flow_coverage_ratio,
            "same_time_flow_percentile": effective_flow.same_time_flow_percentile,
            "normalization_sample_count": effective_flow.normalization_sample_count,
            "price_change_pct": effective_flow.price_response_pct,
            "vwap_distance_pct": effective_flow.vwap_response_pct,
            "price_response_per_signed_yi": round(impact_per_yi, 4) if impact_per_yi is not None else None,
            "impact_retention_pct": effective_flow.impact_retention_ratio,
            "persistence_score": effective_flow.directional_persistence,
        },
        "evidence": flow_evidence,
        "warnings": [
            *list(effective_flow.counter_evidence),
            "成交方向来自供应商分类算法，不代表机构账户或所谓主力的真实资金流水。",
            "尚未接入授权Level-2订单簿深度与撤单数据，不判断挂单意图和账户身份。",
            *(
                [f"这是 {minute_trade_date} 的收盘窗口证据，不代表当前盘前或盘中的实时状态。"]
                if historical_close_mode else []
            ),
        ],
        "invalidation": list(effective_flow.invalidation_conditions),
        "discipline": [item for item in (effective_flow.discipline, effective_flow.advice) if item],
        "reason_codes": list(effective_flow.reason_codes),
    }
    if entry_plan:
        entry_discipline["evidence"] = [
            f"已读取当日{entry_plan.plan_type}计划，并实时校验触发价、失效位、风险收益与仓位上限。",
            *plan_execution["evidence"],
            *entry_discipline.get("evidence", []),
        ]
    events: list[IntradayEvidenceEventOut] = minute_evidence_timeline(code, name, quote)
    rows = (
        db.query(IntradayEvidenceEvent)
        .filter(
            IntradayEvidenceEvent.trade_date == _today(),
            IntradayEvidenceEvent.target_code.in_([code, code.lstrip("0")]),
        )
        .order_by(IntradayEvidenceEvent.captured_at.desc())
        .limit(100)
        .all()
    )
    rows = [row for row in rows if _event_in_trade_session(row, _today())][:20]
    for row in rows:
        # Generated minute-price events remain the primary timeline.  Unified
        # holding-news transitions are appended because they carry a separate
        # causal clock (publication -> subsequent market validation) and must
        # not disappear merely because minute bars are available.
        if events and not str(row.event_type or "").startswith("HOLDING_NEWS_"):
            continue
        events.append(
            IntradayEvidenceEventOut(
                id=row.id,
                captured_at=row.captured_at,
                scope=row.scope,
                target_code=row.target_code,
                target_name=row.target_name,
                event_type=row.event_type,
                severity=row.severity,
                value=row.value,
                previous_value=row.previous_value,
                priority=getattr(row, "priority", 0),
                group_key=getattr(row, "group_key", ""),
                state_key=getattr(row, "state_key", None),
                first_seen_at=getattr(row, "first_seen_at", None),
                last_seen_at=getattr(row, "last_seen_at", None),
                occurrence_count=getattr(row, "occurrence_count", 1),
                confirmed=bool(getattr(row, "confirmed", False)),
                evidence=_json_list(row.evidence_json),
                counter_evidence=_json_list(getattr(row, "counter_evidence_json", "[]")),
                source=getattr(row, "source", "") or "",
                source_url=getattr(row, "source_url", None),
                source_published_at=getattr(row, "source_published_at", None),
                metadata=_json_dict(getattr(row, "metadata_json", "{}")),
            )
        )
    gate_open = entry_discipline.get("decision") in {"ALLOW", "ALLOW_SMALL"}
    allowed = ["只允许观察，不下单"] if not execution else [execution.recommended_action]
    if gate_open:
        allowed.append(
            f"入场纪律通过：限价且仓位不超过{float(entry_discipline.get('allowed_position_ratio') or 0):.0f}%"
        )
    if t_eligibility and t_eligibility.eligible and gate_open:
        allowed.append("允许小比例正T")
    forbidden = ["禁止无计划追高", "数据缺口时不生成确定性结论"]
    if not gate_open:
        forbidden = [
            str(entry_discipline.get("label") or "当前禁止买入/加仓"),
            *[str(item) for item in entry_discipline.get("evidence", [])[:3]],
            *forbidden,
        ]
    if t_eligibility and not t_eligibility.eligible:
        forbidden.extend(t_eligibility.forbidden_reasons[:2])
    if effective_flow.state == "ABSORPTION_CANDIDATE" and execution:
        allowed.append("下方承接仍待确认：避免在窗口低点恐慌清仓，等待收回分时均价和低点抬高。")
        forbidden.append("承接候选不等于允许抄底；未重新站回分时均价前禁止逆势加仓。")
    elif effective_flow.state == "RECOVERY_CANDIDATE" and execution:
        allowed.append("深水修复候选：避免在窗口低点附近恐慌卖出，等待收回分时均价和首次回踩确认。")
        forbidden.append("修复候选不等于反转；未站稳分时均价前禁止追高、补仓或取消原失效条件。")
    elif effective_flow.state == "ATTACK_CONFIRMED":
        forbidden.append("买向成交与上涨同步不等于允许追高；偏离分时均价时仍须等待计划内回踩确认。")
    elif effective_flow.state == "DISTRIBUTION_RISK":
        forbidden.append("主动买入较多但价格推不动，禁止追高；已有利润按计划提高保护。")
    elif effective_flow.state == "OUTFLOW_CONFIRMED":
        forbidden.append("卖出方向与价格下移同步，禁止接飞刀；只按预设失效位处理风险。")
    elif effective_flow.state == "LIQUIDITY_SHOCK":
        forbidden.append("流动性冲击尚未稳定，禁止追涨、抄底和即时反手。")
    if not market_data_status["is_current_session"]:
        allowed = ["历史行情仅供复盘，不生成当日操作建议"]
        forbidden = [
            market_data_status["data_status_note"],
            "行情证据未通过当前交易日校验，禁止据此买入、加仓、减仓或清仓。",
            *forbidden,
        ]
        historical_note = "历史快照仅供复盘；取得并核验当前交易日行情后才生成操作结论。"
        expectation = expectation.model_copy(update={"suggestion": historical_note})
        if execution is not None:
            execution = execution.model_copy(update={
                "recommended_position_ratio": execution.current_position_ratio,
                "recommended_action": "历史快照：不执行",
                "recommended_reduce_ratio": 0,
                "t_eligible": False,
                "t_type": "NO_T",
                "recommendation": None,
                "high_sell_signal": None,
                "panic_sell_guard": None,
                "contrarian_add_signal": None,
                "data_quality": "historical",
            })
        if t_eligibility is not None:
            t_eligibility = t_eligibility.model_copy(update={
                "eligible": False,
                "suggested_quantity": 0,
                "current_action": "历史快照：不执行做T",
                "forbidden_reasons": [
                    historical_note,
                    *t_eligibility.forbidden_reasons,
                ],
            })
        entry_discipline = {
            **entry_discipline,
            "decision": "BLOCK",
            "label": "历史行情：禁止生成买入结论",
            "risk_level": "UNKNOWN",
            "hard_blocked": True,
            "allowed_position_ratio": 0,
            "evidence": [historical_note],
            "data_quality": "historical",
        }
        consensus_risk = consensus_risk.model_copy(update={"actions": []})
        effective_capital = {
            **effective_capital,
            "discipline": [],
            "warnings": [historical_note, *effective_capital["warnings"]],
        }
    return StockDecisionCardOut(
        code=code,
        name=name,
        industry=str(theme.get("industry") or ""),
        concepts=[str(item) for item in theme.get("concepts", [])],
        current_price=_safe_float(quote.get("price")) or (holding.current_price if holding else 0),
        change_pct=_safe_float(quote.get("change_pct")),
        expectation=expectation,
        volume_price=volume_price,
        execution_state=execution,
        timeline=events,
        allowed_actions=allowed,
        forbidden_actions=forbidden,
        t_eligibility=t_eligibility,
        evidence=(execution.evidence if execution else expectation.evidence),
        counter_evidence=(execution.counter_evidence if execution else expectation.counter_evidence),
        data_quality=(
            str(capture.quality or "missing")
            if market_data_status["is_current_session"] and capture is not None
            else "historical_close"
            if market_data_status["is_latest_available"] and quote
            else "stale" if quote
            else "missing"
        ),
        consensus_risk=consensus_risk,
        minute_chart=minute_chart,
        entry_discipline=entry_discipline,
        effective_capital=effective_capital,
        market_data_trade_date=market_data_status["market_data_trade_date"],
        market_data_as_of=market_data_status["market_data_as_of"],
        provider_event_at=market_data_status["provider_event_at"],
        data_age_seconds=market_data_status["data_age_seconds"],
        is_current_session=market_data_status["is_current_session"],
        is_latest_available=market_data_status["is_latest_available"],
        data_status_note=market_data_status["data_status_note"],
    )
