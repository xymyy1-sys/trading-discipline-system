from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import Holding, TradeLog
from app.schemas.trading import (
    HoldingCreate,
    HoldingUpdate,
    HoldingOut,
    HoldingRefreshOut,
    HoldingSyncOut,
    AccountAssetIn,
    AccountAssetOut,
    PositionExecutionStateOut,
    RecommendationFeedbackIn,
    RecommendationFeedbackOut,
    TEligibilityOut,
    TTradePlanIn,
    TTradePlanOut,
    TTradePlanUpdate
)
from app.api.helpers.holdings_calc import (
    _account_state,
    _account_total_asset,
    _holding_out,
    _refresh_holding_prices,
    _rebuild_holdings_from_trades,
    _holding_account_summary
)
from app.api.helpers.execution import build_execution_states, build_position_execution_state
from app.api.helpers.quotes import _latest_a_share_quotes, _quote_lookup_code
from app.api.helpers.decision import build_t_eligibility, create_t_plan, update_t_plan
from app.models.trading import ActionRecommendation, RecommendationFeedback, TTradePlan

router = APIRouter()

@router.get("/account/asset", response_model=AccountAssetOut)
def get_account_asset(db: Session = Depends(get_db)) -> AccountAssetOut:
    state = _account_state(db)
    return AccountAssetOut(total_asset=state.total_asset, updated_at=state.updated_at)

@router.put("/account/asset", response_model=AccountAssetOut)
def update_account_asset(
    payload: AccountAssetIn,
    db: Session = Depends(get_db),
) -> AccountAssetOut:
    state = _account_state(db)
    state.total_asset = max(0.0, payload.total_asset)
    db.add(state)
    db.commit()
    db.refresh(state)
    return AccountAssetOut(total_asset=state.total_asset, updated_at=state.updated_at)

@router.post("/holdings", response_model=HoldingOut)
def create_holding(payload: HoldingCreate, db: Session = Depends(get_db)) -> HoldingOut:
    data = payload.model_dump()
    account_total_asset = _account_total_asset(db)
    if not data.get("total_asset"):
        data["total_asset"] = account_total_asset
    holding = Holding(**data)
    db.add(holding)
    db.commit()
    db.refresh(holding)
    return _holding_out(holding, account_total_asset=account_total_asset)

@router.get("/holdings", response_model=list[HoldingOut])
def list_holdings(db: Session = Depends(get_db)) -> list[HoldingOut]:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    account_total_asset = _account_total_asset(db)
    price_notes = _refresh_holding_prices(holdings, db)
    return [
        _holding_out(
            item,
            account_total_asset=account_total_asset,
            price_note=price_notes.get(item.code, ""),
        )
        for item in holdings
    ]

@router.post("/holdings/refresh", response_model=HoldingRefreshOut)
def refresh_holdings(db: Session = Depends(get_db)) -> HoldingRefreshOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    account_total_asset = _account_total_asset(db)
    price_notes = _refresh_holding_prices(holdings, db)
    outputs = [
        _holding_out(
            item,
            account_total_asset=account_total_asset,
            price_note=price_notes.get(item.code, ""),
        )
        for item in holdings
    ]
    success_count = sum(1 for item in outputs if item.price_source == "realtime")
    fallback_count = max(0, len(outputs) - success_count)
    notes = [
        f"{item.code} {item.name}：{item.price_note or '使用手工价'}"
        for item in outputs
        if item.price_source != "realtime"
    ]
    if not notes and outputs:
        notes.append("全部持仓已按实时行情刷新。")
    if not outputs:
        notes.append("暂无持仓可刷新。")
    return HoldingRefreshOut(
        holdings=outputs,
        refreshed_at=datetime.now(),
        success_count=success_count,
        fallback_count=fallback_count,
        notes=notes,
        **_holding_account_summary(outputs, account_total_asset),
    )

@router.get("/holdings/execution-states", response_model=list[PositionExecutionStateOut])
def list_holding_execution_states(
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> list[PositionExecutionStateOut]:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return build_execution_states(db, holdings, force_refresh=force_refresh)

@router.get("/holdings/{holding_id}/execution-state", response_model=PositionExecutionStateOut)
def get_holding_execution_state(
    holding_id: int,
    db: Session = Depends(get_db),
) -> PositionExecutionStateOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    try:
        quotes = _latest_a_share_quotes([holding.code])
    except Exception:
        quotes = {}
    quote = quotes.get(_quote_lookup_code(holding.code, quotes), {})
    return build_position_execution_state(db, holding, quote=quote)

@router.post("/recommendations/{recommendation_id}/execution-feedback", response_model=RecommendationFeedbackOut)
def create_recommendation_feedback(
    recommendation_id: int,
    payload: RecommendationFeedbackIn,
    db: Session = Depends(get_db),
) -> RecommendationFeedbackOut:
    recommendation = db.get(ActionRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    feedback = RecommendationFeedback(
        recommendation_id=recommendation_id,
        status=payload.status,
        reason=payload.reason,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback

@router.get("/holdings/{holding_id}/t-eligibility", response_model=TEligibilityOut)
def get_holding_t_eligibility(
    holding_id: int,
    db: Session = Depends(get_db),
) -> TEligibilityOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    return build_t_eligibility(db, holding)

@router.post("/holdings/{holding_id}/t-plan", response_model=TTradePlanOut)
def create_holding_t_plan(
    holding_id: int,
    payload: TTradePlanIn,
    db: Session = Depends(get_db),
) -> TTradePlanOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    return create_t_plan(db, holding, payload)

@router.put("/holdings/{holding_id}/t-plan/{plan_id}", response_model=TTradePlanOut)
def update_holding_t_plan(
    holding_id: int,
    plan_id: int,
    payload: TTradePlanUpdate,
    db: Session = Depends(get_db),
) -> TTradePlanOut:
    plan = db.get(TTradePlan, plan_id)
    if plan is None or plan.holding_id != holding_id:
        raise HTTPException(status_code=404, detail="t plan not found")
    return update_t_plan(db, plan, payload)

@router.post("/holdings/sync-from-trades", response_model=HoldingSyncOut)
def sync_holdings_from_trades(db: Session = Depends(get_db)) -> HoldingSyncOut:
    trades = db.query(TradeLog).order_by(TradeLog.traded_at.asc(), TradeLog.id.asc()).all()
    notes = _rebuild_holdings_from_trades(trades, db)
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    account_total_asset = _account_total_asset(db)
    price_notes = _refresh_holding_prices(holdings, db)
    outputs = [
        _holding_out(
            item,
            account_total_asset=account_total_asset,
            price_note=price_notes.get(item.code, ""),
        )
        for item in holdings
    ]
    return HoldingSyncOut(
        holdings=outputs,
        synced_at=datetime.now(),
        trade_count=len(trades),
        notes=notes,
        **_holding_account_summary(outputs, account_total_asset),
    )

@router.put("/holdings/{holding_id}", response_model=HoldingOut)
def update_holding(
    holding_id: int,
    payload: HoldingUpdate,
    db: Session = Depends(get_db),
) -> HoldingOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(holding, key, value)
    db.commit()
    db.refresh(holding)
    return _holding_out(holding, account_total_asset=_account_total_asset(db))

@router.delete("/holdings/{holding_id}")
def delete_holding(holding_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    db.delete(holding)
    db.commit()
    return {"status": "deleted"}
