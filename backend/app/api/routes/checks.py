from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import Holding
from app.schemas.trading import PreTradeCheckIn, PreTradeCheckOut, InformationDifferentialOut, RiskPositionIn, RiskPositionOut
from app.services.rules import calculate_risk_position, run_pre_trade_check
from fastapi import HTTPException
from app.services.market_data import MarketDataProvider

router = APIRouter()
market_provider = MarketDataProvider()

@router.post("/checks/pre-trade", response_model=PreTradeCheckOut)
def pre_trade_check(payload: PreTradeCheckIn) -> PreTradeCheckOut:
    return run_pre_trade_check(payload)


@router.post("/checks/risk-position", response_model=RiskPositionOut)
def risk_position(payload: RiskPositionIn) -> RiskPositionOut:
    try:
        return calculate_risk_position(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

@router.get("/intel/daily", response_model=InformationDifferentialOut)
def information_differential(date: str | None = None, force_refresh: bool = False, db: Session = Depends(get_db)) -> InformationDifferentialOut:
    holdings = {row.code: row.name for row in db.query(Holding).all()}
    return market_provider.information_differential(date=date, force_refresh=force_refresh, related_stocks=holdings)
