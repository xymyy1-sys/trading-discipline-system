from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import Holding
from app.schemas.trading import (
    SectorFlowOut,
    SectorDetailOut,
    LimitUpLadderOut,
    ThemeRadarOut,
    MarketGradeOut,
    MarketSeesawOut
)
from app.services.market_data import MarketDataProvider
from app.services.rules import grade_market
from app.api.helpers.seesaw import _market_seesaw_monitor
from app.core.limiter import limiter

router = APIRouter()
market_provider = MarketDataProvider()

@router.get("/market/sector-flow", response_model=SectorFlowOut)
@limiter.limit("30/minute")
def sector_flow(
    request: Request,
    flow_type: str = "行业资金流",
    period: str = "今日",
    force_refresh: bool = False,
) -> SectorFlowOut:
    return market_provider.sector_flow(
        flow_type=flow_type,
        period=period,
        force_refresh=force_refresh,
    )

@router.get("/market/sector-detail", response_model=SectorDetailOut)
@limiter.limit("30/minute")
def sector_detail(
    request: Request,
    name: str,
    flow_type: str = "行业资金流",
    period: str = "今日",
    board_code: str | None = None,
    provider: str | None = None,
    force_refresh: bool = False,
) -> SectorDetailOut:
    return market_provider.sector_detail(
        name=name,
        flow_type=flow_type,
        period=period,
        board_code=board_code,
        provider=provider,
        force_refresh=force_refresh,
    )

@router.get("/market/limit-up-ladder", response_model=LimitUpLadderOut)
@limiter.limit("20/minute")
def limit_up_ladder(
    request: Request,
    trade_date: str | None = None,
    force_refresh: bool = False,
) -> LimitUpLadderOut:
    return market_provider.limit_up_ladder(
        trade_date=trade_date,
        force_refresh=force_refresh,
    )

@router.get("/market/theme-radar", response_model=ThemeRadarOut)
@limiter.limit("20/minute")
def theme_radar(
    request: Request,
    force_refresh: bool = False
) -> ThemeRadarOut:
    return market_provider.theme_radar(force_refresh=force_refresh)

@router.get("/market/grade", response_model=MarketGradeOut)
def market_grade(
    turnover_score: int = 70,
    limit_up_count: int = 45,
    leader_state: str = "断板承接",
    loss_effect: str = "一般",
    theme_persistence_days: int = 2,
) -> MarketGradeOut:
    return grade_market(
        turnover_score=turnover_score,
        limit_up_count=limit_up_count,
        leader_state=leader_state,
        loss_effect=loss_effect,
        theme_persistence_days=theme_persistence_days,
    )

@router.get("/market/seesaw-monitor", response_model=MarketSeesawOut)
@limiter.limit("20/minute")
def market_seesaw_monitor(
    request: Request,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> MarketSeesawOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return _market_seesaw_monitor(holdings, force_refresh=force_refresh)
