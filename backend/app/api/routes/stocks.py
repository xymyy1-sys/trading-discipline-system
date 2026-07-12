from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.helpers.execution import build_position_execution_state
from app.api.helpers.holdings_calc import _find_holding_by_code
from app.api.helpers.decision import (
    _json_list,
    build_expectation_snapshot,
    create_expectation_snapshot,
    current_expectation_stage,
    decision_card,
    quote_for_code,
    update_expectation_snapshot,
    ensure_expectation_rules,
)
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.core.database import get_db
from app.models.trading import ExpectationRule, ExpectationSnapshot, Holding, IntradayEvidenceEvent, NextDayPlan, PositionExecutionState, VolumePriceSnapshot
from app.schemas.trading import (
    ExpectationSnapshotIn,
    ExpectationSnapshotOut,
    ExpectationSnapshotUpdate,
    ExpectationRuleIn,
    ExpectationRuleOut,
    IntradayEvidenceEventOut,
    IntradayReviewOut,
    StockDecisionCardOut,
    VolumePriceSnapshotOut,
    CandidateOut,
    ReplayReportOut,
)

router = APIRouter()


@router.get("/replay/{code}", response_model=ReplayReportOut)
def replay_stock(code: str, trade_date: str, db: Session = Depends(get_db)) -> ReplayReportOut:
    from app.services.replay_engine import ReplayEngine
    return ReplayEngine(db).replay(code, trade_date)


@router.get("/candidates", response_model=list[CandidateOut])
def list_candidates(db: Session = Depends(get_db)) -> list[CandidateOut]:
    targets: dict[str, str] = {}
    for row in db.query(NextDayPlan).order_by(NextDayPlan.updated_at.desc()).limit(300).all():
        targets.setdefault(row.code, row.name)
    for row in db.query(Holding).all():
        targets.setdefault(row.code, row.name)

    outputs: list[CandidateOut] = []
    for code, name in targets.items():
        expectation = db.query(ExpectationSnapshot).filter(ExpectationSnapshot.code == code).order_by(ExpectationSnapshot.created_at.desc()).first()
        volume = db.query(VolumePriceSnapshot).filter(VolumePriceSnapshot.code == code).order_by(VolumePriceSnapshot.captured_at.desc()).first()
        execution = db.query(PositionExecutionState).filter(PositionExecutionState.code == code).order_by(PositionExecutionState.updated_at.desc()).first()
        score = 50
        reasons: list[str] = []
        exclusions: list[str] = []
        expectation_result = expectation.expectation_result if expectation else "UNKNOWN"
        if expectation_result in {"STRONGER", "MATCHED"}:
            score += 20
            reasons.append(f"expectation {expectation_result}")
        elif expectation_result in {"WEAKER", "INVALID"}:
            score -= 30
            exclusions.append(f"expectation {expectation_result}")
        else:
            score -= 10
            exclusions.append("expectation evidence missing")

        volume_state = volume.pattern if volume else "UNKNOWN"
        data_quality = volume.data_quality if volume else "missing"
        if volume and volume.vwap_reliable:
            score += 15
            reasons.append("real minute VWAP is reliable")
        else:
            score -= 15
            exclusions.append("real minute VWAP is unavailable")
        if execution:
            if execution.state in {"EXIT_REQUIRED", "REDUCE_REQUIRED", "EXPECTATION_INVALIDATED", "STOP_LOSS_WARNING"}:
                score -= 35
                exclusions.append(f"execution state {execution.state}")
            elif execution.state in {"NORMAL_HOLD", "PROFIT_EXPANSION"}:
                score += 10
                reasons.append(f"execution state {execution.state}")
        score = max(0, min(100, score))
        pool = "A" if score >= 75 and not exclusions else "B" if score >= 55 else "C" if score >= 35 else "D"
        outputs.append(CandidateOut(
            code=code,
            name=name,
            pool=pool,
            score=score,
            expectation_result=expectation_result,
            volume_price_state=volume_state,
            execution_state=execution.state if execution else "",
            data_quality=data_quality,
            reasons=reasons,
            exclusions=exclusions,
            updated_at=max([value for value in (expectation.created_at if expectation else None, volume.captured_at if volume else None, execution.updated_at if execution else None) if value is not None], default=None),
        ))
    return sorted(outputs, key=lambda item: (-item.score, item.code))


@router.get("/expectation-rules", response_model=list[ExpectationRuleOut])
def get_expectation_rules(db: Session = Depends(get_db)) -> list[ExpectationRule]:
    return ensure_expectation_rules(db)


@router.post("/expectation-rules", response_model=ExpectationRuleOut)
def upsert_expectation_rule(payload: ExpectationRuleIn, db: Session = Depends(get_db)) -> ExpectationRule:
    if not (payload.severe_underperform_threshold <= payload.underperform_threshold < payload.expected_open_low <= payload.expected_open_high < payload.outperform_threshold):
        raise HTTPException(status_code=422, detail="expectation thresholds must be strictly ordered")
    row = db.query(ExpectationRule).filter(
        ExpectationRule.script_type == payload.script_type,
        ExpectationRule.stage == payload.stage,
        ExpectationRule.base_expectation == payload.base_expectation,
    ).first()
    if row is None:
        row = ExpectationRule(**payload.model_dump())
        db.add(row)
    else:
        for key, value in payload.model_dump().items():
            setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


@router.get("/stocks/{code}/decision-card", response_model=StockDecisionCardOut)
def get_stock_decision_card(code: str, db: Session = Depends(get_db)) -> StockDecisionCardOut:
    return decision_card(db, code)


@router.get("/stocks/{code}/expectation", response_model=ExpectationSnapshotOut)
def get_stock_expectation(code: str, db: Session = Depends(get_db)) -> ExpectationSnapshotOut:
    return build_expectation_snapshot(db, code, stage=current_expectation_stage())


@router.get("/stocks/{code}/volume-price", response_model=VolumePriceSnapshotOut)
def get_stock_volume_price(code: str, db: Session = Depends(get_db)) -> VolumePriceSnapshotOut:
    quote = quote_for_code(code)
    name = str(quote.get("name") or code)
    return build_volume_price_snapshot(db, code, name=name, stage=current_expectation_stage(), quote=quote)


@router.post("/expectations", response_model=ExpectationSnapshotOut)
def post_expectation_snapshot(payload: ExpectationSnapshotIn, db: Session = Depends(get_db)) -> ExpectationSnapshotOut:
    return create_expectation_snapshot(db, payload)


@router.put("/expectations/{expectation_id}", response_model=ExpectationSnapshotOut)
def put_expectation_snapshot(
    expectation_id: int,
    payload: ExpectationSnapshotUpdate,
    db: Session = Depends(get_db),
) -> ExpectationSnapshotOut:
    row = db.get(ExpectationSnapshot, expectation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Expectation snapshot not found")
    return update_expectation_snapshot(db, row, payload)


@router.get("/stocks/{code}/timeline", response_model=list[IntradayEvidenceEventOut])
def get_stock_timeline(code: str, db: Session = Depends(get_db)) -> list[IntradayEvidenceEventOut]:
    rows = (
        db.query(IntradayEvidenceEvent)
        .filter(IntradayEvidenceEvent.target_code.in_([code, code.lstrip("0")]))
        .order_by(IntradayEvidenceEvent.captured_at.desc())
        .limit(50)
        .all()
    )
    return [
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
        for row in rows
    ]


@router.get("/stocks/{code}/intraday-review", response_model=IntradayReviewOut)
def get_stock_intraday_review(code: str, db: Session = Depends(get_db)) -> IntradayReviewOut:
    holding = _find_holding_by_code(db, code)
    state = build_position_execution_state(db, holding) if holding else None
    if state is None:
        latest_state = (
            db.query(PositionExecutionState)
            .filter(PositionExecutionState.code.in_([code, code.lstrip("0")]))
            .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
            .first()
        )
        if not latest_state:
            raise HTTPException(status_code=404, detail="No intraday review data found")
        timeline = get_stock_timeline(code, db)
        return IntradayReviewOut(
            code=latest_state.code,
            name=latest_state.name,
            generated_at=datetime.now(),
            latest_action=latest_state.recommended_action,
            latest_state=latest_state.state,
            data_quality=latest_state.data_quality,
            timeline=timeline,
            evidence=_json_list(latest_state.evidence_json),
            counter_evidence=_json_list(latest_state.counter_evidence_json),
            next_actions=_json_list(latest_state.invalid_conditions_json)[:3],
        )
    return IntradayReviewOut(
        code=state.code,
        name=state.name,
        generated_at=datetime.now(),
        latest_action=state.recommended_action,
        latest_state=state.state,
        data_quality=state.data_quality,
        timeline=state.events,
        evidence=state.evidence,
        counter_evidence=state.counter_evidence,
        next_actions=(state.invalid_conditions + state.recovery_conditions)[:5],
    )
