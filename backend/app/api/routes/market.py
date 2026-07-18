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
    LimitUpAtmosphereMetrics,
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
from app.services.market_data import (
    MarketDataProvider,
    _is_valid_limit_up_ladder,
    _last_trading_day,
    _limit_up_default_candidate_dates,
)
from app.services.sector_margin import fetch_sector_margin
from app.services.sector_temperature import build_sector_temperature
from app.services.cache import _get_cached_flow, _get_response_cache, _set_response_cache
from app.services.market_regime import get_market_regime, read_market_regime
from app.api.helpers.reflexivity import build_market_reflexivity
from app.services.global_market import global_market_service
from app.services.rules import grade_market
from app.api.helpers.seesaw import _market_seesaw_monitor
from app.core.limiter import limiter

router = APIRouter()
market_provider = MarketDataProvider()


def _cache_miss_note(resource: str) -> str:
    return f"{resource}尚无服务端缓存；请点击刷新，由显式刷新任务采集真实数据。"


def _read_limit_up_ladder_cache(trade_date: str | None = None) -> LimitUpLadderOut:
    candidates = [trade_date] if trade_date else _limit_up_default_candidate_dates()
    for candidate in candidates:
        cached = _get_response_cache(f"limit-up-ladder|{candidate}", allow_stale=True)
        if _is_valid_limit_up_ladder(cached):
            return cached
    target_date = trade_date or _last_trading_day()
    return LimitUpLadderOut(
        source="cache-unavailable",
        trade_date=target_date,
        updated_at=shanghai_now_naive(),
        groups=[],
        clusters=[],
        summary=[],
        notes=[_cache_miss_note("涨停天梯")],
    )

@router.get("/market/sector-flow", response_model=SectorFlowOut)
@limiter.limit("30/minute")
def sector_flow(
    request: Request,
    flow_type: str = "行业资金流",
    period: str = "今日",
) -> SectorFlowOut:
    cached = _get_response_cache(f"sector-flow|{flow_type}|{period}", allow_stale=True)
    return cached or SectorFlowOut(
        source="cache-unavailable",
        updated_at=shanghai_now_naive(),
        inflow=[],
        outflow=[],
    )


@router.post("/market/sector-flow/refresh", response_model=SectorFlowOut)
@limiter.limit("4/minute")
def refresh_sector_flow(
    request: Request,
    flow_type: str = "行业资金流",
    period: str = "今日",
) -> SectorFlowOut:
    return market_provider.sector_flow(flow_type=flow_type, period=period, force_refresh=True)

@router.get("/market/board-flow-panel", response_model=BoardFlowPanelOut)
@limiter.limit("30/minute")
def board_flow_panel(
    request: Request,
    board_type: str = "行业",
    period: str = "今日",
) -> BoardFlowPanelOut:
    normalized = market_provider._normalize_board_type(board_type)
    cached = _get_response_cache(f"board-flow-panel|{normalized}|{period}", allow_stale=True)
    return cached or BoardFlowPanelOut(
        source="cache-unavailable",
        updated_at=shanghai_now_naive(),
        board_type=normalized,
        period=period,
        inflow=[],
        outflow=[],
        notes=[_cache_miss_note("板块订单流")],
    )


@router.post("/market/board-flow-panel/refresh", response_model=BoardFlowPanelOut)
@limiter.limit("4/minute")
def refresh_board_flow_panel(
    request: Request,
    board_type: str = "行业",
    period: str = "今日",
) -> BoardFlowPanelOut:
    return market_provider.board_flow_panel(board_type=board_type, period=period, force_refresh=True)

@router.get("/market/hot-themes", response_model=HotThemesOut)
@limiter.limit("20/minute")
def hot_themes(
    request: Request,
) -> HotThemesOut:
    cached = _get_response_cache("hot-themes", allow_stale=True)
    return cached or HotThemesOut(
        source="cache-unavailable",
        updated_at=shanghai_now_naive(),
        items=[],
        notes=[_cache_miss_note("热点题材")],
    )


@router.post("/market/hot-themes/refresh", response_model=HotThemesOut)
@limiter.limit("4/minute")
def refresh_hot_themes(request: Request) -> HotThemesOut:
    return market_provider.hot_themes(force_refresh=True)

@router.get("/market/dark-trade", response_model=DarkTradeOut)
@limiter.limit("20/minute")
def dark_trade(
    request: Request,
    scope: str = "个股",
    trade_date: str | None = None,
) -> DarkTradeOut:
    normalized = market_provider._normalize_dark_scope(scope)
    date_text = (trade_date or _last_trading_day()).replace("-", "")
    cached = _get_response_cache(f"dark-trade|{normalized}|{date_text}", allow_stale=True)
    return cached or DarkTradeOut(
        source="cache-unavailable",
        trade_date=date_text,
        updated_at=shanghai_now_naive(),
        scope=normalized,
        items=[],
        notes=[_cache_miss_note("成交拆单估算")],
    )


@router.post("/market/dark-trade/refresh", response_model=DarkTradeOut)
@limiter.limit("4/minute")
def refresh_dark_trade(
    request: Request,
    scope: str = "个股",
    trade_date: str | None = None,
) -> DarkTradeOut:
    return market_provider.dark_trade(scope=scope, trade_date=trade_date, force_refresh=True)

@router.get("/market/sector-detail", response_model=SectorDetailOut)
@limiter.limit("30/minute")
def sector_detail(
    request: Request,
    name: str,
    flow_type: str = "行业资金流",
    period: str = "今日",
    board_code: str | None = None,
    provider: str | None = None,
) -> SectorDetailOut:
    cache_key = f"sector-detail|{flow_type}|{period}|{name}|{board_code or ''}|{provider or ''}"
    cached = _get_response_cache(cache_key, allow_stale=True)
    return cached or SectorDetailOut(
        source="cache-unavailable",
        updated_at=shanghai_now_naive(),
        name=name,
        board_code=board_code,
        provider=provider,
        constituents=[],
        limit_up_stocks=[],
        notes=[_cache_miss_note("板块成分股明细")],
    )


@router.post("/market/sector-detail/refresh", response_model=SectorDetailOut)
@limiter.limit("4/minute")
def refresh_sector_detail(
    request: Request,
    name: str,
    flow_type: str = "行业资金流",
    period: str = "今日",
    board_code: str | None = None,
    provider: str | None = None,
) -> SectorDetailOut:
    return market_provider.sector_detail(
        name=name,
        flow_type=flow_type,
        period=period,
        board_code=board_code,
        provider=provider,
        force_refresh=True,
    )

@router.get("/market/limit-up-ladder", response_model=LimitUpLadderOut)
@limiter.limit("20/minute")
def limit_up_ladder(
    request: Request,
    trade_date: str | None = None,
) -> LimitUpLadderOut:
    return _read_limit_up_ladder_cache(trade_date)


@router.post("/market/limit-up-ladder/refresh", response_model=LimitUpLadderOut)
@limiter.limit("4/minute")
def refresh_limit_up_ladder(
    request: Request,
    trade_date: str | None = None,
) -> LimitUpLadderOut:
    return market_provider.limit_up_ladder(trade_date=trade_date, force_refresh=True)


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
            f"当日板块订单流方向估算使用 {current_cache['source'] or '未知来源'} 缓存{cache_date}，快照更新时间 {cache_time}。"
        )
    result["notes"] = list(dict.fromkeys(notes))
    current_source = str(current.source or "").lower()
    cached_suffix = "缓存" if "cached" in current_source else ""
    fund_source = (
        f"东方财富板块订单流算法{cached_suffix}"
        if "eastmoney" in current_source
        else f"新浪备用板块订单流算法{cached_suffix}"
        if "sina" in current_source
        else "板块订单流方向估算暂不可用"
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
) -> SectorTemperatureOut:
    """Read the last explicitly collected multi-window temperature snapshot."""

    normalized = "概念" if board_type == "概念" else "行业"
    cached = _get_response_cache(f"sector-temperature|{normalized}", allow_stale=True)
    return cached or SectorTemperatureOut(
        source="cache-unavailable",
        updated_at=shanghai_now_naive(),
        board_type=normalized,
        notes=[_cache_miss_note("板块冷热拥挤")],
    )


@router.post("/market/sector-temperature/refresh", response_model=SectorTemperatureOut)
@limiter.limit("4/minute")
def refresh_sector_temperature(
    request: Request,
    board_type: str = "行业",
) -> SectorTemperatureOut:
    """Explicitly collect multi-window flows and T+1 crowding evidence."""

    return _sector_temperature_snapshot(board_type=board_type, force_refresh=True)


@router.get("/market/limit-up-atmosphere", response_model=LimitUpAtmosphereOut)
@limiter.limit("20/minute")
def limit_up_atmosphere(
    request: Request,
    trade_date: str | None = None,
) -> LimitUpAtmosphereOut:
    cached = (
        _get_response_cache(f"limit-up-atmosphere|{trade_date}", allow_stale=True)
        if trade_date
        else _get_response_cache("limit-up-atmosphere-latest", allow_stale=True)
    )
    target_date = trade_date or _last_trading_day()
    return cached or LimitUpAtmosphereOut(
        source="cache-unavailable",
        trade_date=target_date,
        updated_at=shanghai_now_naive(),
        decision="DATA_GAP",
        decision_label="数据不足，禁止打板",
        score=0,
        data_quality="缺失",
        metrics=LimitUpAtmosphereMetrics(),
        risks=[_cache_miss_note("打板氛围")],
        missing_data=["显式刷新产生的真实涨停、炸板和次日溢价统计"],
        notes=["普通页面读取不会触发外部行情采集。"],
    )


@router.post("/market/limit-up-atmosphere/refresh", response_model=LimitUpAtmosphereOut)
@limiter.limit("4/minute")
def refresh_limit_up_atmosphere(
    request: Request,
    trade_date: str | None = None,
) -> LimitUpAtmosphereOut:
    return market_provider.limit_up_atmosphere(trade_date=trade_date, force_refresh=True)

@router.get("/market/theme-radar", response_model=ThemeRadarOut)
@limiter.limit("20/minute")
def theme_radar(
    request: Request,
) -> ThemeRadarOut:
    return market_provider.theme_radar(cache_only=True)


@router.post("/market/theme-radar/refresh", response_model=ThemeRadarOut)
@limiter.limit("4/minute")
def refresh_theme_radar(request: Request) -> ThemeRadarOut:
    return market_provider.theme_radar(force_refresh=True)


@router.get("/market/regime", response_model=MarketRegimeOut)
@limiter.limit("12/minute")
def market_regime(
    request: Request,
    db: Session = Depends(get_db),
) -> MarketRegimeOut:
    """Return the persisted, evidence-backed full A-share market regime."""
    return read_market_regime(db)


@router.post("/market/regime/refresh", response_model=MarketRegimeOut)
@limiter.limit("4/minute")
def refresh_market_regime(
    request: Request,
    db: Session = Depends(get_db),
) -> MarketRegimeOut:
    """Explicitly collect and persist a new market-regime snapshot."""
    return get_market_regime(db, force_refresh=True)


@router.get("/market/reflexivity", response_model=ReflexivityAssessmentOut)
@limiter.limit("12/minute")
def market_reflexivity(
    request: Request,
    db: Session = Depends(get_db),
) -> ReflexivityAssessmentOut:
    """Return falsifiable market crowding scenarios from persisted evidence."""
    persisted_regime = read_market_regime(db)
    return ReflexivityAssessmentOut.model_validate(
        build_market_reflexivity(db, persisted_regime)
    )


@router.get("/market/global-cues", response_model=GlobalMarketOut)
@limiter.limit("12/minute")
def global_market_cues(
    request: Request,
) -> GlobalMarketOut:
    """Return traceable overseas evidence; unavailable quotes remain null."""
    return GlobalMarketOut.model_validate(
        global_market_service.read_cached_snapshot()
    )


@router.post("/market/global-cues/refresh", response_model=GlobalMarketOut)
@limiter.limit("4/minute")
def refresh_global_market_cues(request: Request) -> GlobalMarketOut:
    return GlobalMarketOut.model_validate(
        global_market_service.snapshot(force_refresh=True)
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
    db: Session = Depends(get_db),
) -> MarketSeesawOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return _market_seesaw_monitor(holdings, cache_only=True)


@router.post("/market/seesaw-monitor/refresh", response_model=MarketSeesawOut)
@limiter.limit("4/minute")
def refresh_market_seesaw_monitor(
    request: Request,
    db: Session = Depends(get_db),
) -> MarketSeesawOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return _market_seesaw_monitor(holdings, force_refresh=True)


@router.get("/market/capital-rotation", response_model=CapitalRotationOut)
@limiter.limit("20/minute")
def capital_rotation(request: Request, db: Session = Depends(get_db)) -> CapitalRotationOut:
    from app.api.helpers.execution import _sector_migration_signal

    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    monitor = _market_seesaw_monitor(holdings, cache_only=True)
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
