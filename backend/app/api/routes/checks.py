from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import Holding, MarketRegimeSnapshot
from app.schemas.trading import (
    PreTradeCheckIn,
    PreTradeCheckOut,
    InformationDifferentialOut,
    OpportunityRadarOut,
    RiskPositionIn,
    RiskPositionOut,
)
from app.services.rules import calculate_risk_position, run_pre_trade_check
from fastapi import HTTPException
from app.services.market_data import MarketDataProvider
from app.services.opportunity_radar import OpportunityRadarService
from app.core.limiter import limiter

router = APIRouter()
market_provider = MarketDataProvider()
opportunity_radar_service = OpportunityRadarService()
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
OPPORTUNITY_MARKET_SNAPSHOT_MAX_AGE = timedelta(minutes=15)


def _fresh_market_snapshot(
    db: Session,
    *,
    trade_date: str,
    now: datetime | None = None,
) -> tuple[MarketRegimeSnapshot | None, str | None]:
    """Return only a same-day, recent market snapshot and explain degradation."""
    current = now or datetime.now(SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    else:
        current = current.astimezone(SHANGHAI_TZ)
    latest = (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date == trade_date)
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )
    if latest is None or latest.captured_at is None:
        return None, f"{trade_date}无同交易日市场环境快照，机会雷达不使用历史市场涨跌替代。"
    captured = latest.captured_at
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=SHANGHAI_TZ)
    else:
        captured = captured.astimezone(SHANGHAI_TZ)
    age = current - captured
    if age < timedelta(minutes=-1) or age > OPPORTUNITY_MARKET_SNAPSHOT_MAX_AGE:
        age_minutes = max(0, int(age.total_seconds() // 60))
        return None, (
            f"{trade_date}市场环境快照已过期（{age_minutes}分钟），"
            "未用于板块相对强度计算。"
        )
    return latest, None

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


@router.get("/intel/opportunity-radar", response_model=OpportunityRadarOut)
@limiter.limit("12/minute")
def opportunity_radar(
    request: Request,
    date: str | None = None,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> OpportunityRadarOut:
    """Map news to sectors, then require real funds, price and VWAP confirmation."""
    holdings = {row.code: row.name for row in db.query(Holding).all()}
    information = market_provider.information_differential(
        date=date,
        force_refresh=force_refresh,
        related_stocks=holdings,
    )
    sector_flows = [
        market_provider.sector_flow(
            flow_type=flow_type,
            period="今日",
            force_refresh=force_refresh,
        )
        for flow_type in ("行业资金流", "概念资金流")
    ]
    target_trade_date = date or datetime.now(SHANGHAI_TZ).date().isoformat()
    latest_regime, regime_note = _fresh_market_snapshot(
        db,
        trade_date=target_trade_date,
    )
    result = opportunity_radar_service.assess(
        information,
        sector_flows,
        market_change_pct=(latest_regime.index_composite_change_pct if latest_regime else None),
    )
    if regime_note:
        result["notes"] = list(dict.fromkeys([*(result.get("notes") or []), regime_note]))
        if result.get("data_quality") == "ok":
            result["data_quality"] = "degraded"
    return OpportunityRadarOut.model_validate(result)
