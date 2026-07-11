from fastapi import APIRouter
from app.schemas.trading import PreTradeCheckIn, PreTradeCheckOut, InformationDifferentialOut
from app.services.rules import run_pre_trade_check
from app.services.market_data import MarketDataProvider

router = APIRouter()
market_provider = MarketDataProvider()

@router.post("/checks/pre-trade", response_model=PreTradeCheckOut)
def pre_trade_check(payload: PreTradeCheckIn) -> PreTradeCheckOut:
    return run_pre_trade_check(payload)

@router.get("/intel/daily", response_model=InformationDifferentialOut)
def information_differential(date: str | None = None) -> InformationDifferentialOut:
    return market_provider.information_differential(date=date)
