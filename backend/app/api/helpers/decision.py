from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.execution import build_position_execution_state
from app.api.helpers.holdings_calc import _find_holding_by_code
from app.api.helpers.quotes import _latest_a_share_quotes, _quote_lookup_code, _safe_float
from app.api.helpers.seesaw import _holding_theme_profile
from app.models.trading import ExpectationSnapshot, Holding, IntradayEvidenceEvent, TTradePlan
from app.schemas.trading import (
    ExpectationSnapshotOut,
    IntradayEvidenceEventOut,
    StockDecisionCardOut,
    TEligibilityOut,
    TTradePlanIn,
    TTradePlanOut,
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
        t_type=row.t_type,
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
    open_pct = _safe_float(quote.get("open_pct"))
    change_pct = _safe_float(quote.get("change_pct"))
    current = _safe_float(quote.get("price"))
    previous_close = _safe_float(quote.get("prev_close"))
    if not open_pct and quote.get("open") and previous_close:
        open_pct = (_safe_float(quote.get("open")) - previous_close) / previous_close * 100
    base_expectation = "NEUTRAL"
    if any(key in base_hint for key in ("超预期", "强预期", "主线前排", "打板")):
        base_expectation = "STRONG"
    if any(key in base_hint for key in ("弱于预期", "分歧转弱", "退出")):
        base_expectation = "WEAK"
    if any(key in base_hint for key in ("修复", "低吸")):
        base_expectation = "REPAIR"

    expected_low, expected_high = (-1.0, 2.0)
    if base_expectation == "STRONG":
        expected_low, expected_high = (2.0, 5.5)
    elif base_expectation == "WEAK":
        expected_low, expected_high = (-4.0, 0.5)
    elif base_expectation == "REPAIR":
        expected_low, expected_high = (-2.0, 2.5)
    outperform = expected_high + 1.0
    underperform = expected_low - 1.0
    severe_under = min(underperform - 2.0, -3.0)

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
        result, transition, suggestion = "SLIGHTLY_STRONGER", "WEAK_TO_STRONG", "小幅超预期，等待量价确认后再提高仓位。"
    elif open_score <= -18:
        result, transition, suggestion = "WEAKER", "EXPECTATION_INVALIDATED", "显著低于预期，优先降风险，禁止补仓。"
    elif open_score <= -8:
        result, transition, suggestion = "SLIGHTLY_WEAKER", "CONSENSUS_TO_DIVERGENCE", "预期转分歧，观察修复失败就减仓。"
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


def build_t_eligibility(db: Session, holding: Holding) -> TEligibilityOut:
    execution = build_position_execution_state(db, holding)
    forbidden: list[str] = []
    evidence: list[str] = []
    current = holding.current_price
    sellable = int(holding.quantity or 0)
    suggested_qty = max(0, int(sellable * 0.25 // 100) * 100) if sellable >= 100 else 0
    if execution.state in {"EXIT_REQUIRED", "REDUCE_REQUIRED"}:
        forbidden.append(f"当前执行状态为 {execution.state}，先处理降风险，不做T。")
    if not execution.t_eligible:
        forbidden.append("持仓执行状态机判定当前不具备做T资格。")
    if sellable <= 0:
        forbidden.append("没有昨日可卖底仓。")
    if execution.profit_snapshot and execution.profit_snapshot.current_profit_pct < 0:
        forbidden.append("当前浮亏，做T容易演变为补仓摊低成本。")
    eligible = not forbidden and suggested_qty > 0
    if eligible:
        evidence.append("原持仓逻辑未证伪，且仍有可卖底仓。")
        evidence.append("只允许正T小比例卖出，等待重新确认后接回。")
    buyback_low = round(current * 0.975, 2) if current else 0
    buyback_high = round(current * 0.99, 2) if current else 0
    conditions = [
        "回踩VWAP附近缩量企稳。",
        "5分钟内重新站回VWAP。",
        "所属板块资金停止下降或重新回流。",
        "主动买入重新占优，不能继续创新低。",
    ]
    return TEligibilityOut(
        holding_id=int(holding.id),
        code=holding.code,
        name=holding.name,
        t_type="POSITIVE_T" if eligible else "NO_T",
        eligible=eligible,
        sellable_quantity=sellable,
        suggested_quantity=suggested_qty,
        suggested_sell_price=round(max(current * 1.02, current), 2) if current else 0,
        buyback_price_low=buyback_low,
        buyback_price_high=buyback_high,
        buyback_conditions=conditions,
        forbidden_reasons=forbidden,
        evidence=evidence or execution.evidence[:3],
        current_action=execution.recommended_action,
    )


def create_t_plan(db: Session, holding: Holding, payload: TTradePlanIn | None = None) -> TTradePlanOut:
    eligibility = build_t_eligibility(db, holding)
    payload = payload or TTradePlanIn()
    if not eligibility.eligible:
        t_type = "NO_T"
        quantity = 0
        evidence = eligibility.forbidden_reasons
    else:
        t_type = payload.t_type if payload.t_type != "NO_T" else eligibility.t_type
        quantity = payload.planned_sell_quantity or eligibility.suggested_quantity
        evidence = eligibility.evidence
    row = TTradePlan(
        holding_id=int(holding.id),
        trade_date=_today(),
        code=holding.code,
        name=holding.name,
        t_type=t_type,
        planned_sell_price=payload.planned_sell_price or eligibility.suggested_sell_price,
        planned_sell_quantity=quantity,
        buyback_price_low=payload.buyback_price_low or eligibility.buyback_price_low,
        buyback_price_high=payload.buyback_price_high or eligibility.buyback_price_high,
        buyback_conditions_json=_json_dumps(payload.buyback_conditions or eligibility.buyback_conditions),
        cancel_conditions_json=_json_dumps(payload.cancel_conditions or [
            "跌破结构止损或反抽VWAP失败。",
            "板块资金继续流出。",
            "接回条件未满足，T仓卖出转为永久减仓。",
        ]),
        status="planned" if t_type != "NO_T" else "forbidden",
        evidence_json=_json_dumps(evidence),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _t_plan_out(row)


def update_t_plan(db: Session, row: TTradePlan, payload: Any) -> TTradePlanOut:
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(row, key, value)
    if row.actual_sell_price and row.actual_buyback_price and row.actual_quantity:
        row.cost_reduction = round((row.actual_sell_price - row.actual_buyback_price) * row.actual_quantity, 2)
        if row.status == "planned":
            row.status = "completed"
    db.commit()
    db.refresh(row)
    return _t_plan_out(row)


def decision_card(db: Session, code: str) -> StockDecisionCardOut:
    holding = _find_holding_by_code(db, code)
    quote = quote_for_code(code)
    name = holding.name if holding else str(quote.get("name") or code)
    theme = _holding_theme_profile(holding) if holding else {"industry": "", "concepts": [], "source": "quote-only"}
    base_hint = holding.position_type if holding else ""
    expectation = build_expectation_snapshot(db, code, name=name, quote=quote, base_hint=base_hint)
    execution = build_position_execution_state(db, holding, quote=quote) if holding else None
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
        execution_state=execution,
        timeline=events,
        allowed_actions=allowed,
        forbidden_actions=forbidden,
        t_eligibility=t_eligibility,
        evidence=(execution.evidence if execution else expectation.evidence),
        counter_evidence=(execution.counter_evidence if execution else expectation.counter_evidence),
        data_quality="realtime" if quote else "manual",
    )
