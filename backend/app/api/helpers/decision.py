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
from app.models.trading import ExpectationRule, ExpectationSnapshot, Holding, IntradayEvidenceEvent, TTradePlan
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


def current_expectation_stage(now: datetime | None = None) -> str:
    now = now or datetime.now()
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
    quote = quote or quote_for_code(code)
    stage = stage or current_expectation_stage()
    open_pct = _safe_float(quote.get("open_pct"))
    change_pct = _safe_float(quote.get("change_pct"))
    current = _safe_float(quote.get("price"))
    previous_close = _safe_float(quote.get("prev_close"))
    if not open_pct and quote.get("open") and previous_close:
        open_pct = (_safe_float(quote.get("open")) - previous_close) / previous_close * 100
    base_expectation = "NEUTRAL"
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

    if open_score >= 16:
        result, transition, suggestion = "STRONGER", "STRONG_TO_STRONGER", "超预期强化，只允许按计划确认，不追最高点。"
    elif open_score >= 8:
        result, transition, suggestion = "STRONGER", "WEAK_TO_STRONG", "小幅超预期，等待量价确认后再提高仓位。"
    elif open_score <= -18:
        result, transition, suggestion = "INVALID", "EXPECTATION_INVALIDATED", "显著低于预期，优先降风险，禁止补仓。"
    elif open_score <= -8:
        result, transition, suggestion = "WEAKER", "CONSENSUS_TO_DIVERGENCE", "预期转分歧，观察修复失败就减仓。"
    else:
        result, transition, suggestion = "MATCHED", "MATCHED", "基本符合预期，按原计划和失效条件执行。"
    confidence = 0.72 if quote else 0.42
    row = ExpectationSnapshot(
        trade_date=_today(),
        code=code,
        name=name or code,
        stage=stage,
        base_expectation=base_expectation,
        expected_open_low=expected_low,
        expected_open_high=expected_high,
        outperform_threshold=outperform,
        underperform_threshold=underperform,
        severe_underperform_threshold=severe_under,
        actual_open_pct=round(open_pct, 2),
        actual_change_pct=round(change_pct, 2),
        expectation_gap_score=open_score,
        expectation_result=result,
        state_transition=transition,
        confidence=confidence,
        evidence_json=_json_dumps(evidence),
        counter_evidence_json=_json_dumps(counter),
        suggestion=suggestion,
    )
    if persist:
        db.add(row)
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
    holding = _find_holding_by_code(db, code)
    quote = quote_for_code(code)
    name = holding.name if holding else str(quote.get("name") or code)
    theme = _holding_theme_profile(holding) if holding else {"industry": "", "concepts": [], "source": "quote-only"}
    base_hint = holding.position_type if holding else ""
    stage = current_expectation_stage()
    expectation = build_expectation_snapshot(db, code, name=name, stage=stage, quote=quote, base_hint=base_hint)
    volume_price = build_volume_price_snapshot(db, code, name=name, stage=stage, quote=quote)
    execution = build_position_execution_state(db, holding, quote=quote, expectation=expectation, volume_price=volume_price) if holding else None
    t_eligibility = build_t_eligibility(db, holding) if holding else None
    events: list[IntradayEvidenceEventOut] = []
    rows = (
        db.query(IntradayEvidenceEvent)
        .filter(IntradayEvidenceEvent.target_code.in_([code, code.lstrip("0")]))
        .order_by(IntradayEvidenceEvent.captured_at.desc())
        .limit(20)
        .all()
    )
    for row in rows:
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
    )
