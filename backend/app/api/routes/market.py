from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import Holding
from app.schemas.trading import (
    BoardFlowPanelOut,
    DarkTradeOut,
    HotThemesOut,
    SectorFlowOut,
    SectorDetailOut,
    LimitUpLadderOut,
    ThemeRadarOut,
    MarketGradeOut,
    MarketSeesawOut,
    CapitalRotationOut,
    CapitalRotationAssessment,
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

@router.get("/market/board-flow-panel", response_model=BoardFlowPanelOut)
@limiter.limit("30/minute")
def board_flow_panel(
    request: Request,
    board_type: str = "行业",
    period: str = "今日",
    force_refresh: bool = False,
) -> BoardFlowPanelOut:
    return market_provider.board_flow_panel(
        board_type=board_type,
        period=period,
        force_refresh=force_refresh,
    )

@router.get("/market/hot-themes", response_model=HotThemesOut)
@limiter.limit("20/minute")
def hot_themes(
    request: Request,
    force_refresh: bool = False,
) -> HotThemesOut:
    return market_provider.hot_themes(force_refresh=force_refresh)

@router.get("/market/dark-trade", response_model=DarkTradeOut)
@limiter.limit("20/minute")
def dark_trade(
    request: Request,
    scope: str = "个股",
    trade_date: str | None = None,
    force_refresh: bool = False,
) -> DarkTradeOut:
    return market_provider.dark_trade(
        scope=scope,
        trade_date=trade_date,
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


@router.get("/market/capital-rotation", response_model=CapitalRotationOut)
@limiter.limit("20/minute")
def capital_rotation(request: Request, force_refresh: bool = False, db: Session = Depends(get_db)) -> CapitalRotationOut:
    from datetime import datetime
    from app.api.helpers.execution import _sector_migration_signal

    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    monitor = _market_seesaw_monitor(holdings, force_refresh=force_refresh)
    assessments: list[CapitalRotationAssessment] = []
    for item in monitor.holding_alerts:
        confirmed, confidence, evidence, source_net, source_peak = _sector_migration_signal(item)
        if not item.external_inflow_target:
            continue
        assessments.append(CapitalRotationAssessment(
            code=item.code,
            name=item.name,
            source_theme=item.holding_theme or item.sector,
            target_theme=item.external_inflow_target,
            confirmed=confirmed,
            confidence=confidence,
            source_net_inflow=source_net,
            source_flow_peak=source_peak,
            evidence=evidence,
        ))
    return CapitalRotationOut(generated_at=datetime.now(), assessments=sorted(assessments, key=lambda row: row.confidence, reverse=True))
