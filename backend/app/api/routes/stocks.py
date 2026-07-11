from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.helpers.decision import _json_list, build_expectation_snapshot, decision_card
from app.core.database import get_db
from app.models.trading import IntradayEvidenceEvent
from app.schemas.trading import ExpectationSnapshotOut, IntradayEvidenceEventOut, StockDecisionCardOut

router = APIRouter()


@router.get("/stocks/{code}/decision-card", response_model=StockDecisionCardOut)
def get_stock_decision_card(code: str, db: Session = Depends(get_db)) -> StockDecisionCardOut:
    return decision_card(db, code)


@router.get("/stocks/{code}/expectation", response_model=ExpectationSnapshotOut)
def get_stock_expectation(code: str, db: Session = Depends(get_db)) -> ExpectationSnapshotOut:
    return build_expectation_snapshot(db, code)


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
