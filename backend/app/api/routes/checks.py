import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import DataCaptureSnapshot, Holding, MarketRegimeSnapshot
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
from app.core.limiter import limiter

router = APIRouter()
market_provider = MarketDataProvider()
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
def information_differential(date: str | None = None, db: Session = Depends(get_db)) -> InformationDifferentialOut:
    holdings = {row.code: row.name for row in db.query(Holding).all()}
    return market_provider.information_differential(
        date=date,
        related_stocks=holdings,
        cache_only=True,
    )


@router.post("/intel/daily/refresh", response_model=InformationDifferentialOut)
def refresh_information_differential(
    date: str | None = None,
    db: Session = Depends(get_db),
) -> InformationDifferentialOut:
    holdings = {row.code: row.name for row in db.query(Holding).all()}
    return market_provider.information_differential(
        date=date,
        force_refresh=True,
        related_stocks=holdings,
    )


@router.get("/intel/opportunity-radar", response_model=OpportunityRadarOut)
@limiter.limit("12/minute")
def opportunity_radar(
    request: Request,
    date: str | None = None,
    db: Session = Depends(get_db),
) -> OpportunityRadarOut:
    """Read the latest collector-produced opportunity radar snapshot."""
    query = db.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.data_type == "opportunity_radar",
        DataCaptureSnapshot.target_code == "market",
    )
    if date:
        query = query.filter(DataCaptureSnapshot.trade_date == date)
    row = query.order_by(
        DataCaptureSnapshot.captured_at.desc(),
        DataCaptureSnapshot.id.desc(),
    ).first()
    if row is not None:
        try:
            return OpportunityRadarOut.model_validate(
                json.loads(row.normalized_value_json or "{}")
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    now = datetime.now(SHANGHAI_TZ).isoformat()
    return OpportunityRadarOut(
        updated_at=now,
        as_of=now,
        source=[],
        data_quality="missing",
        items=[],
        counts={},
        discipline="资讯不能单独触发买入；缺少资金与量价证据时只观察。",
        notes=["尚无机会雷达持久化快照，请点击刷新或等待后台采集器。"],
        available_sector_evidence=0,
    )


@router.post("/intel/opportunity-radar/refresh", response_model=OpportunityRadarOut)
@limiter.limit("4/minute")
def refresh_opportunity_radar(
    request: Request,
    db: Session = Depends(get_db),
) -> OpportunityRadarOut:
    """Explicitly collect a fresh radar, then read its persisted snapshot."""
    from app.services.intraday_collector import run_opportunity_radar_collection_once

    run_opportunity_radar_collection_once(trigger="manual", force_refresh=True)
    return opportunity_radar(request=request, db=db)
