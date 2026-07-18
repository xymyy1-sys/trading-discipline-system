from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import Holding
from app.schemas.trading import (
    BoardFlowPanelOut,
    DarkTradeOut,
    HotThemesOut,
    SectorFlowOut,
    SectorDetailOut,
    SectorTemperatureOut,
    LimitUpAtmosphereOut,
    LimitUpLadderOut,
    ThemeRadarOut,
    MarketGradeOut,
    MarketRegimeOut,
    ReflexivityAssessmentOut,
    GlobalMarketOut,
    MarketSeesawOut,
    CapitalRotationOut,
    CapitalRotationAssessment,
)
from app.services.market_data import MarketDataProvider
from app.services.sector_margin import fetch_sector_margin
from app.services.sector_temperature import build_sector_temperature
from app.services.cache import _get_cached_flow, _get_response_cache, _set_response_cache
from app.services.market_regime import get_market_regime
from app.api.helpers.reflexivity import build_market_reflexivity
from app.services.global_market import global_market_service
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


def _sector_temperature_snapshot(
    board_type: str = "行业",
    force_refresh: bool = False,
) -> SectorTemperatureOut:
    normalized = "概念" if board_type == "概念" else "行业"
    response_cache_key = f"sector-temperature|{normalized}"
    if not force_refresh:
        cached = _get_response_cache(response_cache_key)
        if cached is not None:
            return cached

    with ThreadPoolExecutor(max_workers=5) as executor:
        current_future = executor.submit(market_provider.board_flow_panel, normalized, "今日", force_refresh)
        five_future = executor.submit(market_provider.board_flow_panel, normalized, "5日", force_refresh)
        ten_future = executor.submit(market_provider.board_flow_panel, normalized, "10日", force_refresh)
        margin_future = executor.submit(fetch_sector_margin, normalized, force_refresh)
        attention_future = executor.submit(market_provider.hot_themes, force_refresh)
        current = current_future.result()
        five_day = five_future.result()
        ten_day = ten_future.result()
        margin = margin_future.result()
        try:
            attention_rows = attention_future.result().items
        except Exception:
            attention_rows = []

    attention_by_name: dict[str, dict[str, object]] = {}
    for row in attention_rows:
        previous = attention_by_name.get(row.name)
        score = max(0, 100 - max(0, int(row.rank) - 1) * 3)
        if previous is None or score > int(previous.get("score") or 0):
            attention_by_name[row.name] = {
                "score": score,
                "rank": row.rank,
                "period": row.period,
                "source": row.source,
            }

    flow_type = market_provider._board_type_to_flow_type(normalized)

    def all_rows(panel, period: str):
        panel_items = [*panel.inflow, *panel.outflow]

        def item_dict(item):
            if hasattr(item, "model_dump"):
                return item.model_dump()
            if isinstance(item, dict):
                return dict(item)
            return dict(vars(item))

        enriched_by_name = {
            str(getattr(item, "name", "") or "").strip(): item_dict(item)
            for item in panel_items
            if str(getattr(item, "name", "") or "").strip()
        }
        cached_full = _get_cached_flow(f"{flow_type}|{period}")
        panel_source = str(panel.source or "")
        cache_used = "cached" in panel_source.lower()
        if cached_full and cached_full[0]:
            # Full raw rows preserve all boards and provider timestamps.  The
            # panel rows add the real intraday curve-derived speed, acceleration
            # and turning point for the visible leaders/laggards.  Merge both;
            # otherwise preferring the raw cache silently drops those kinetics.
            rows = [
                {
                    **raw,
                    **enriched_by_name.get(str(raw.get("name") or "").strip(), {}),
                    "_cache_used": cache_used,
                    "_cache_source": str(cached_full[1] or ""),
                    "_cache_trade_date": str(cached_full[2] or "")[:10],
                }
                for raw in cached_full[0]
            ]
            return rows, {
                "used": cache_used,
                "source": str(cached_full[1] or ""),
                "trade_date": str(cached_full[2] or "")[:10],
            }
        return [
            {
                **item_dict(item),
                "_cache_used": cache_used,
                "_cache_source": panel_source,
                "_cache_trade_date": "",
            }
            for item in panel_items
        ], {"used": cache_used, "source": panel_source, "trade_date": ""}

    current_rows, current_cache = all_rows(current, "今日")
    five_rows, _ = all_rows(five_day, "5日")
    ten_rows, _ = all_rows(ten_day, "10日")
    provider_updates = [
        str(row.get("provider_updated_at") or "").strip()
        for row in current_rows
        if str(row.get("provider_updated_at") or "").strip()
    ]
    effective_updated_at = max(provider_updates) if provider_updates else current.updated_at

    result = build_sector_temperature(
        current_rows,
        five_rows,
        ten_rows,
        margin_by_name=margin.get("items") or {},
        attention_by_name=attention_by_name,
        board_type=normalized,
        updated_at=effective_updated_at,
    )
    notes = list(result.get("notes") or [])
    notes.extend(margin.get("notes") or [])
    if current_cache["used"]:
        cache_time = max(provider_updates) if provider_updates else "精确更新时间缺失"
        cache_date = f"，缓存日期 {current_cache['trade_date']}" if current_cache["trade_date"] else ""
        notes.append(
            f"当日板块资金使用 {current_cache['source'] or '未知来源'} 缓存{cache_date}，快照更新时间 {cache_time}。"
        )
    result["notes"] = list(dict.fromkeys(notes))
    current_source = str(current.source or "").lower()
    cached_suffix = "缓存" if "cached" in current_source else ""
    fund_source = (
        f"东方财富板块资金{cached_suffix}"
        if "eastmoney" in current_source
        else f"新浪备用板块资金{cached_suffix}"
        if "sina" in current_source
        else "板块资金暂不可用"
    )
    result["source"] = (
        f"{fund_source}+东方财富两融T+1"
        if margin.get("items")
        else fund_source
    )
    validated = SectorTemperatureOut.model_validate(result)
    _set_response_cache(response_cache_key, validated)
    return validated


@router.get("/market/sector-temperature", response_model=SectorTemperatureOut)
@limiter.limit("12/minute")
def sector_temperature(
    request: Request,
    board_type: str = "行业",
    force_refresh: bool = False,
) -> SectorTemperatureOut:
    """Combine multi-window real flows with explicitly T+1 crowding data."""

    return _sector_temperature_snapshot(board_type=board_type, force_refresh=force_refresh)


@router.get("/market/limit-up-atmosphere", response_model=LimitUpAtmosphereOut)
@limiter.limit("20/minute")
def limit_up_atmosphere(
    request: Request,
    trade_date: str | None = None,
    force_refresh: bool = False,
) -> LimitUpAtmosphereOut:
    return market_provider.limit_up_atmosphere(
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


@router.get("/market/regime", response_model=MarketRegimeOut)
@limiter.limit("12/minute")
def market_regime(
    request: Request,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> MarketRegimeOut:
    """Return the persisted, evidence-backed full A-share market regime."""
    return get_market_regime(db, force_refresh=force_refresh)


@router.get("/market/reflexivity", response_model=ReflexivityAssessmentOut)
@limiter.limit("12/minute")
def market_reflexivity(
    request: Request,
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> ReflexivityAssessmentOut:
    """Return falsifiable market crowding scenarios from persisted evidence."""
    regime = get_market_regime(db, force_refresh=force_refresh)
    try:
        sector_opening = market_provider.sector_opening_breadth(
            trade_date=regime.trade_date,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        sector_opening = {
            "trade_date": regime.trade_date,
            "data_quality": "missing",
            "source": "",
            "sample_count": 0,
            "notes": [f"行业板块真实开盘广度暂不可用：{type(exc).__name__}"],
        }
    return ReflexivityAssessmentOut.model_validate(
        build_market_reflexivity(db, regime, sector_opening)
    )


@router.get("/market/global-cues", response_model=GlobalMarketOut)
@limiter.limit("12/minute")
def global_market_cues(
    request: Request,
    force_refresh: bool = False,
) -> GlobalMarketOut:
    """Return traceable overseas evidence; unavailable quotes remain null."""
    return GlobalMarketOut.model_validate(
        global_market_service.snapshot(force_refresh=force_refresh)
    )

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
    return CapitalRotationOut(generated_at=shanghai_now_naive(), assessments=sorted(assessments, key=lambda row: row.confidence, reverse=True))
