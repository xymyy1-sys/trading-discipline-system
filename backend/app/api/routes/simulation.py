import json

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
    SimulationValidationOut,
    SimulationRiskGuardOut,
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
from app.services.simulation_validation import validation_report
from app.services.simulation_risk import account_risk_guard
from app.services.system_evolution import system_evolution_report
from app.services.limit_up_promotion import promotion_dashboard
from app.core.trading_clock import shanghai_now_naive
from app.services.simulation_shadow import get_or_create_ai_trader_account, run_shadow_experiments
from app.services.autonomous_selection import latest_autonomous_selection, refresh_autonomous_selection


router = APIRouter(prefix="/simulation", tags=["simulation"])


def _account_or_404(db: Session, account_id: int) -> SimulationAccount:
    account = db.get(SimulationAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="模拟账户不存在")
    return account


@router.post("/ai-trader/account", response_model=SimulationAccountOut)
def ensure_ai_trader_account(db: Session = Depends(get_db)) -> SimulationAccount:
    """Create or return the dedicated 20k AI paper-trading account."""

    return get_or_create_ai_trader_account(db)


@router.post("/ai-trader/run")
def run_ai_trader_once(db: Session = Depends(get_db)) -> dict:
    """Manually trigger one forward-only AI paper-trading scan.

    It uses the same audited shadow engine as the scheduler and therefore
    respects trading-session gates, evidence freshness and T+1 sellability.
    """

    account = get_or_create_ai_trader_account(db)
    result = run_shadow_experiments(db, account)
    return {
        "account_id": result.account_id,
        "evaluated_at": result.evaluated_at,
        "created_order_ids": result.order_ids,
        "skipped_count": len(result.skipped),
        "duplicate_count": len(result.duplicate_signal_keys),
        "skipped": result.skipped,
        "duplicate_signal_keys": result.duplicate_signal_keys,
    }


@router.get("/ai-trader/candidates")
def ai_trader_candidates(
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    """Expose the independent all-A discovery list and its market gate."""
    if refresh:
        return refresh_autonomous_selection(db, force=True)
    payload = latest_autonomous_selection(db)
    if payload is not None:
        return payload
    return {
        "trade_date": "",
        "total_scanned": 0,
        "candidate_count": 0,
        "gate": {"allow_entry": False, "reason": "等待交易时段生成全A独立扫描"},
        "items": [],
        "scope_note": "候选范围为全A实时行情，不依赖观察池或涨停类模块。",
    }


@router.get("/ai-trader/promotion-dashboard")
def ai_trader_promotion_dashboard(
    signal_date: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """Read the frozen k -> k+1 promotion cohorts and their outcomes."""
    return promotion_dashboard(db, signal_date=signal_date or None)


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


@router.get("/accounts/{account_id}/system-evolution")
def get_system_evolution_report(
    account_id: int,
    trade_date: str = "",
    db: Session = Depends(get_db),
) -> dict:
    """Expose module scorecards and Codex-ready improvement proposals."""
    return system_evolution_report(
        db,
        _account_or_404(db, account_id),
        trade_date=trade_date or None,
    )


@router.get("/accounts/{account_id}/validation", response_model=SimulationValidationOut)
def simulation_validation(account_id: int, db: Session = Depends(get_db)) -> dict:
    return validation_report(db, _account_or_404(db, account_id))


@router.get("/accounts/{account_id}/risk-guard", response_model=SimulationRiskGuardOut)
def simulation_risk_guard(account_id: int, db: Session = Depends(get_db)):
    return account_risk_guard(db, _account_or_404(db, account_id), shanghai_now_naive())


@router.get(
    "/accounts/{account_id}/calibration-proposal",
    response_model=SimulationCalibrationProposalOut,
)
def get_simulation_calibration_proposal(
    account_id: int,
    db: Session = Depends(get_db),
) -> dict:
    return simulation_calibration_proposal(db, _account_or_404(db, account_id))


@router.get("/accounts/{account_id}/rule-releases")
def list_simulation_rule_releases(
    account_id: int,
    db: Session = Depends(get_db),
) -> list[dict]:
    """Expose the training/promotion/rollback audit without mutating it."""
    from app.models.trading import SimulationRuleRelease

    _account_or_404(db, account_id)
    rows = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.account_id == account_id,
    ).order_by(SimulationRuleRelease.id.desc()).limit(50).all()
    return [
        {
            "id": row.id,
            "rule_version": row.rule_version,
            "baseline_rule_version": row.baseline_rule_version,
            "status": row.status,
            "parameters": json.loads(row.parameters_json or "{}"),
            "rationale": json.loads(row.rationale_json or "[]"),
            "forward_control_samples": row.forward_control_samples,
            "forward_candidate_samples": row.forward_candidate_samples,
            "control_metrics": json.loads(row.control_metrics_json or "{}"),
            "candidate_metrics": json.loads(row.candidate_metrics_json or "{}"),
            "created_at": row.created_at,
            "validated_at": row.validated_at,
            "activated_at": row.activated_at,
            "rolled_back_at": row.rolled_back_at,
            "rollback_reason": row.rollback_reason,
        }
        for row in rows
    ]


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
