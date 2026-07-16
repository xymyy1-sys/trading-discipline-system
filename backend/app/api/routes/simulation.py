from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.trading import (
    SimulationAccount,
    SimulationClosedTrade,
    SimulationDailyEquity,
    SimulationEvidenceSnapshot,
    SimulationFill,
    SimulationOrder,
    SimulationPosition,
    SimulationShadowDecision,
)
from app.schemas.simulation import (
    SimulationAccountCreate,
    SimulationAccountOut,
    SimulationCalibrationProposalOut,
    SimulationClosedTradeOut,
    SimulationDailyEquityOut,
    SimulationEvidenceOut,
    SimulationFillOut,
    SimulationOrderCreate,
    SimulationOrderOut,
    SimulationPerformanceOut,
    SimulationPositionOut,
    SimulationShadowDecisionOut,
)
from app.services.simulation import (
    cancel_order,
    create_account,
    mark_to_market,
    performance_report,
    process_open_orders,
    submit_order,
)
from app.services.simulation_calibration import simulation_calibration_proposal


router = APIRouter(prefix="/simulation", tags=["simulation"])


def _account_or_404(db: Session, account_id: int) -> SimulationAccount:
    account = db.get(SimulationAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="模拟账户不存在")
    return account


@router.post("/accounts", response_model=SimulationAccountOut)
def create_simulation_account(
    payload: SimulationAccountCreate,
    db: Session = Depends(get_db),
) -> SimulationAccount:
    return create_account(db, payload)


@router.get("/accounts", response_model=list[SimulationAccountOut])
def list_simulation_accounts(db: Session = Depends(get_db)) -> list[SimulationAccount]:
    return db.query(SimulationAccount).order_by(SimulationAccount.created_at.desc()).all()


@router.get("/accounts/{account_id}", response_model=SimulationAccountOut)
def get_simulation_account(account_id: int, db: Session = Depends(get_db)) -> SimulationAccount:
    return _account_or_404(db, account_id)


@router.post("/accounts/{account_id}/orders", response_model=SimulationOrderOut)
def place_simulation_order(
    account_id: int,
    payload: SimulationOrderCreate,
    db: Session = Depends(get_db),
) -> SimulationOrder:
    return submit_order(db, _account_or_404(db, account_id), payload)


@router.post("/accounts/{account_id}/orders/process", response_model=list[SimulationOrderOut])
def process_simulation_orders(account_id: int, db: Session = Depends(get_db)) -> list[SimulationOrder]:
    return process_open_orders(db, _account_or_404(db, account_id))


@router.post("/accounts/{account_id}/orders/{order_id}/cancel", response_model=SimulationOrderOut)
def cancel_simulation_order(
    account_id: int,
    order_id: int,
    db: Session = Depends(get_db),
) -> SimulationOrder:
    _account_or_404(db, account_id)
    order = cancel_order(db, account_id, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="模拟委托不存在")
    return order


@router.get("/accounts/{account_id}/orders", response_model=list[SimulationOrderOut])
def list_simulation_orders(
    account_id: int,
    status: str = "",
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[SimulationOrder]:
    _account_or_404(db, account_id)
    query = db.query(SimulationOrder).filter(SimulationOrder.account_id == account_id)
    if status:
        query = query.filter(SimulationOrder.status == status.upper())
    return query.order_by(SimulationOrder.submitted_at.desc(), SimulationOrder.id.desc()).limit(limit).all()


@router.get("/accounts/{account_id}/fills", response_model=list[SimulationFillOut])
def list_simulation_fills(
    account_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[SimulationFill]:
    _account_or_404(db, account_id)
    return db.query(SimulationFill).filter(
        SimulationFill.account_id == account_id,
    ).order_by(SimulationFill.filled_at.desc(), SimulationFill.id.desc()).limit(limit).all()


@router.get("/accounts/{account_id}/positions", response_model=list[SimulationPositionOut])
def list_simulation_positions(
    account_id: int,
    include_closed: bool = False,
    db: Session = Depends(get_db),
) -> list[SimulationPosition]:
    _account_or_404(db, account_id)
    query = db.query(SimulationPosition).filter(SimulationPosition.account_id == account_id)
    if not include_closed:
        query = query.filter(SimulationPosition.quantity > 0)
    return query.order_by(SimulationPosition.market_value.desc(), SimulationPosition.code.asc()).all()


@router.post("/accounts/{account_id}/equity/mark", response_model=SimulationDailyEquityOut)
def mark_simulation_equity(account_id: int, db: Session = Depends(get_db)) -> SimulationDailyEquity:
    return mark_to_market(db, _account_or_404(db, account_id))


@router.get("/accounts/{account_id}/equity", response_model=list[SimulationDailyEquityOut])
def list_simulation_equity(
    account_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[SimulationDailyEquity]:
    _account_or_404(db, account_id)
    return db.query(SimulationDailyEquity).filter(
        SimulationDailyEquity.account_id == account_id,
    ).order_by(SimulationDailyEquity.trade_date.desc()).limit(limit).all()


@router.get("/accounts/{account_id}/evidence", response_model=list[SimulationEvidenceOut])
def list_simulation_evidence(
    account_id: int,
    code: str = "",
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[SimulationEvidenceSnapshot]:
    _account_or_404(db, account_id)
    query = db.query(SimulationEvidenceSnapshot).filter(
        SimulationEvidenceSnapshot.account_id == account_id,
    )
    if code:
        from app.api.helpers.quotes import _normalize_code
        query = query.filter(SimulationEvidenceSnapshot.code == _normalize_code(code))
    return query.order_by(
        SimulationEvidenceSnapshot.captured_at.desc(),
        SimulationEvidenceSnapshot.id.desc(),
    ).limit(limit).all()


@router.get("/accounts/{account_id}/performance", response_model=SimulationPerformanceOut)
def simulation_performance(account_id: int, db: Session = Depends(get_db)) -> dict:
    return performance_report(db, _account_or_404(db, account_id))


@router.get(
    "/accounts/{account_id}/calibration-proposal",
    response_model=SimulationCalibrationProposalOut,
)
def get_simulation_calibration_proposal(
    account_id: int,
    db: Session = Depends(get_db),
) -> dict:
    return simulation_calibration_proposal(db, _account_or_404(db, account_id))


@router.get(
    "/accounts/{account_id}/shadow-decisions",
    response_model=list[SimulationShadowDecisionOut],
)
def list_simulation_shadow_decisions(
    account_id: int,
    status: str = "",
    limit: int = Query(default=200, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[SimulationShadowDecision]:
    """Expose the immutable signal-to-paper-order audit trail.

    This is read-only and never triggers matching or any real-trading path.
    """

    _account_or_404(db, account_id)
    query = db.query(SimulationShadowDecision).filter(
        SimulationShadowDecision.account_id == account_id,
    )
    if status:
        query = query.filter(SimulationShadowDecision.status == status.upper())
    return query.order_by(
        SimulationShadowDecision.evaluated_at.desc(),
        SimulationShadowDecision.id.desc(),
    ).limit(limit).all()


@router.get("/accounts/{account_id}/closed-trades", response_model=list[SimulationClosedTradeOut])
def list_simulation_closed_trades(
    account_id: int,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
) -> list[SimulationClosedTrade]:
    _account_or_404(db, account_id)
    return db.query(SimulationClosedTrade).filter(
        SimulationClosedTrade.account_id == account_id,
    ).order_by(SimulationClosedTrade.closed_at.desc(), SimulationClosedTrade.id.desc()).limit(limit).all()
