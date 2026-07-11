from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.helpers.decision import (
    _json_list,
    build_expectation_snapshot,
    create_expectation_snapshot,
    current_expectation_stage,
    decision_card,
    quote_for_code,
    update_expectation_snapshot,
)
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.core.database import get_db
from app.models.trading import ExpectationSnapshot, IntradayEvidenceEvent
from app.schemas.trading import (
    ExpectationSnapshotIn,
    ExpectationSnapshotOut,
    ExpectationSnapshotUpdate,
    IntradayEvidenceEventOut,
    StockDecisionCardOut,
    VolumePriceSnapshotOut,
)

router = APIRouter()


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
