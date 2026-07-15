from __future__ import annotations

import json
from datetime import datetime, time
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.execution import build_position_execution_state
from app.api.helpers.holdings_calc import _find_holding_by_code
from app.api.helpers.quotes import _latest_a_share_quotes, _quote_lookup_code, _safe_float
from app.api.helpers.seesaw import _holding_theme_profile
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.models.trading import (
    ExpectationRevision,
    ExpectationRule,
    ExpectationScenario,
    ExpectationSnapshot,
    Holding,
    IntradayEvidenceEvent,
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
        "退潮": ["竞价跌破预期下沿", "放量跌破结构支撑", "题材资金同步流出"],
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


def expectation_rule_for(db: Session, script_type: str, stage: str, base_expectation: str) -> ExpectationRule | None:
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
    rule = expectation_rule_for(db, infer_script_type(base_hint), stage, base_expectation)
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
    confidence = 0.72 if quote_is_usable else 0.2
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


def decision_card(db: Session, code: str) -> StockDecisionCardOut:
    from app.api.helpers.quotes import _daily_history_metrics
    from app.services.consensus_risk import build_consensus_risk
    holding = _find_holding_by_code(db, code)
    quote = quote_for_code(code)
    name = holding.name if holding else str(quote.get("name") or code)
    theme = _holding_theme_profile(holding) if holding else {"industry": "", "concepts": [], "source": "quote-only"}
    base_hint = holding.position_type if holding else ""
    now = shanghai_now_naive()
    stage = current_expectation_stage(now)
    during_market = now.weekday() < 5 and time(9, 15) <= now.time() <= time(15, 0)
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
    # Persist the latest minute/volume structure before revising expectation so
    # a newly confirmed V reversal is reflected in this response rather than
    # one refresh later.
    daily_metrics = _daily_history_metrics(code)
    volume_price = build_volume_price_snapshot(
        db, code, name=name, stage=stage, quote=quote,
        daily_metrics=daily_metrics, persist=during_market,
    )
    if during_market:
        expectation = build_expectation_snapshot(
            db, code, name=name, stage=stage, quote=quote,
            base_hint=(baseline.base_expectation if baseline else base_hint), persist=True,
        )
    elif baseline:
        expectation = _expectation_out(baseline)
    else:
        expectation = build_expectation_snapshot(
            db, code, name=name, stage=stage, quote=quote, base_hint=base_hint, persist=False,
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
    execution = build_position_execution_state(db, holding, quote=quote, expectation=expectation, volume_price=volume_price, persist=during_market) if holding else None
    t_eligibility = build_t_eligibility(db, holding) if holding else None
    events: list[IntradayEvidenceEventOut] = minute_evidence_timeline(code, name, quote)
    rows = (
        db.query(IntradayEvidenceEvent)
        .filter(IntradayEvidenceEvent.target_code.in_([code, code.lstrip("0")]))
        .order_by(IntradayEvidenceEvent.captured_at.desc())
        .limit(100)
        .all()
    )
    rows = [row for row in rows if time(9, 15) <= row.captured_at.time() <= time(15, 0)][:20]
    for row in ([] if events else rows):
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
                evidence=_json_list(row.evidence_json),
            )
        )
    allowed = ["按计划持有观察"] if not execution else [execution.recommended_action]
    if t_eligibility and t_eligibility.eligible:
        allowed.append("允许小比例正T")
    forbidden = ["禁止无计划追高", "数据缺口时不生成确定性结论"]
    if t_eligibility and not t_eligibility.eligible:
        forbidden.extend(t_eligibility.forbidden_reasons[:2])
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
        data_quality="realtime" if quote else "manual",
        consensus_risk=consensus_risk,
        minute_chart=minute_chart,
    )
