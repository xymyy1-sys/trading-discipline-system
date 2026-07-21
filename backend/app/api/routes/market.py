from concurrent.futures import ThreadPoolExecutor
import json

from fastapi import APIRouter, Depends, HTTPException, Request
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
    LimitUpCatcherCriteria,
    LimitUpCatcherOut,
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
from app.services.sector_audited_flow import fetch_sector_audited_flow
from app.services.sector_temperature import build_sector_temperature
from app.services.sector_evidence_history import (
    build_global_evidence_evolution,
    build_sector_state_evolution,
    global_evidence_recency_key,
    load_global_evidence_history,
    load_latest_global_evidence_snapshot,
    load_latest_sector_temperature_snapshot,
    load_sector_history,
    load_sector_persistence_features,
    load_sector_samples,
    persist_global_evidence_snapshot,
    persist_sector_temperature_snapshot,
)
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


@router.get("/market/limit-up-catcher", response_model=LimitUpCatcherOut)
@limiter.limit("20/minute")
def limit_up_catcher(request: Request) -> LimitUpCatcherOut:
    """Read the last explicitly collected real full-market screen."""

    cached = _get_response_cache("limit-up-catcher", allow_stale=True)
    return cached or LimitUpCatcherOut(
        source="cache-unavailable",
        updated_at=shanghai_now_naive(),
        data_status="data_gap",
        criteria=LimitUpCatcherCriteria(),
        items=[],
        notes=[_cache_miss_note("抓涨停")],
    )


@router.post("/market/limit-up-catcher/refresh", response_model=LimitUpCatcherOut)
@limiter.limit("4/minute")
def refresh_limit_up_catcher(request: Request) -> LimitUpCatcherOut:
    """Explicitly collect and screen real Eastmoney full-market quotes."""

    return market_provider.limit_up_catcher(force_refresh=True)


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
    db: Session | None = None,
) -> SectorTemperatureOut:
    normalized = "概念" if board_type == "概念" else "行业"
    response_cache_key = f"sector-temperature|{normalized}"
    if not force_refresh:
        cached = _get_response_cache(response_cache_key)
        if cached is not None:
            if db is not None and hasattr(db, "query"):
                try:
                    persist_sector_temperature_snapshot(db, cached)
                except Exception:
                    db.rollback()
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
    current_trade_dates = sorted({
        str(row.get("provider_trade_date") or "")[:10]
        for row in current_rows
        if str(row.get("provider_trade_date") or "")[:10]
    })
    current_trade_date = current_trade_dates[-1] if current_trade_dates else ""

    audited_flow_note = ""
    audited_flow_merged = False
    if current_trade_date:
        audited_flow = fetch_sector_audited_flow(current_trade_date)
        merge_map = audited_flow.get("merge_map") or {}
        if audited_flow.get("status") == "ok" and isinstance(merge_map, dict):
            for row in current_rows:
                row_trade_date = str(row.get("provider_trade_date") or "")[:10]
                if row_trade_date != current_trade_date:
                    # A mixed-date upstream cache must never receive the newest
                    # day's audited cash-flow facts before it is archived.
                    continue
                audited_item = None
                for alias in (
                    str(row.get("name") or "").strip(),
                    str(row.get("display_name") or "").strip(),
                    str(row.get("raw_name") or "").strip(),
                    str(row.get("board_code") or "").strip().upper(),
                ):
                    if alias and isinstance(merge_map.get(alias), dict):
                        audited_item = merge_map[alias]
                        break
                if audited_item is None:
                    continue
                if str(audited_item.get("trade_date") or "")[:10] != row_trade_date:
                    continue
                row["non_leveraged_net_inflow"] = audited_item.get(
                    "non_leveraged_net_inflow"
                )
                row["non_leveraged_flow_audited"] = True
                row["non_leveraged_flow_source_url"] = audited_item.get("source_url")
                row["non_leveraged_flow_published_at"] = (
                    audited_item.get("published_at") or audited_item.get("observed_at")
                )
                row["non_leveraged_net_inflow_unit"] = audited_item.get(
                    "non_leveraged_net_inflow_unit"
                )
                row["non_leveraged_methodology_id"] = audited_item.get(
                    "methodology_id"
                )
                if audited_item.get("new_high_count") is not None:
                    row["new_high_count"] = audited_item.get("new_high_count")
                    row["constituent_count"] = audited_item.get("constituent_count")
                if audited_item.get("etf_flow_audited") is True:
                    row["etf_share_net_change"] = audited_item.get("etf_share_net_change")
                    row["etf_share_change_pct"] = audited_item.get("etf_share_change_pct")
                    row["etf_id"] = audited_item.get("etf_id")
                    row["etf_share_unit"] = audited_item.get("etf_share_unit")
                    row["etf_share_base"] = audited_item.get("etf_share_base")
                    row["etf_methodology_id"] = audited_item.get("etf_methodology_id")
                    row["etf_flow_audited"] = True
                audited_flow_merged = True
            audited_flow_note = (
                f"已按 {current_trade_date} 合并经授权审计的非杠杆板块净流入；"
                "未返回的板块保持空值。"
            )
        else:
            audited_flow_note = "；".join(str(note) for note in audited_flow.get("notes") or [])

    # 晋级率与炸板率来自真实、带日期的涨停池和炸板池。只按板块/题材
    # 名称精确匹配，并且只在交易日一致时并入；数据缺失时保持 None。
    structure_note = ""
    try:
        atmosphere = market_provider.limit_up_atmosphere(
            trade_date=current_trade_date or None,
            force_refresh=False,
        )
        if current_trade_date and atmosphere.trade_date == current_trade_date:
            ladder_by_name = {
                str(item.name or "").strip(): item
                for item in atmosphere.theme_ladders
                if str(item.name or "").strip()
            }
            for row in current_rows:
                ladder_item = None
                for alias in (
                    str(row.get("name") or "").strip(),
                    str(row.get("display_name") or "").strip(),
                    str(row.get("raw_name") or "").strip(),
                ):
                    if alias and alias in ladder_by_name:
                        ladder_item = ladder_by_name[alias]
                        break
                if ladder_item is None:
                    continue
                row["limit_up_count"] = int(ladder_item.limit_up_count)
                if ladder_item.promotion_rate is not None:
                    row["promotion_rate"] = float(ladder_item.promotion_rate)
                if ladder_item.break_rate is not None:
                    row["break_rate"] = float(ladder_item.break_rate)
            structure_note = (
                f"涨停晋级率与炸板率按 {current_trade_date} 真实涨停/炸板池精确题材归属并入；"
                "未匹配板块保持空值。"
            )
        else:
            structure_note = (
                "涨停结构数据与板块行情交易日不一致，晋级率和炸板率未并入六态判断。"
            )
    except Exception as exc:
        structure_note = (
            f"真实涨停结构暂不可用：{exc.__class__.__name__}；"
            "晋级率和炸板率保持空值，不以零代替。"
        )
    provider_updates = [
        str(row.get("provider_updated_at") or "").strip()
        for row in current_rows
        if str(row.get("provider_updated_at") or "").strip()
    ]
    effective_updated_at = max(provider_updates) if provider_updates else current.updated_at

    persistence_by_name: dict[str, dict[str, object]] = {}
    persistence_note = ""
    if db is not None and hasattr(db, "query"):
        try:
            persistence_by_name = load_sector_persistence_features(
                db,
                board_type=normalized,
            )
        except Exception as exc:
            db.rollback()
            persistence_note = (
                f"板块持续性与本地历史特征暂不可用：{exc.__class__.__name__}；"
                "高危状态保持待持续确认，历史斜率与分位不使用替代值。"
            )

    # Prefer the provider's exact deep history.  When it is unavailable, use
    # only locally persisted, distinct T+1 disclosure dates as a fallback.
    # Cumulative 5/10/20-day totals are never re-labelled as daily slopes.
    margin_by_name: dict[str, dict[str, object]] = {
        str(name): dict(values)
        for name, values in (margin.get("items") or {}).items()
        if isinstance(values, dict)
    }
    local_history_fields = (
        "financing_net_buy_slope_5d",
        "financing_net_buy_slope_10d",
        "financing_net_buy_slope_20d",
        "financing_balance_ratio_percentile_60d",
        "financing_balance_ratio_percentile_120d",
    )
    for name, values in margin_by_name.items():
        feature = persistence_by_name.get(name)
        board_code = str(values.get("board_code") or "").strip().upper()
        if feature is None and board_code:
            feature = persistence_by_name.get(board_code)
        if not feature:
            values["margin_history_degraded"] = bool(
                values.get("margin_history_degraded", True)
            )
            continue
        fallback_used = False
        for field in local_history_fields:
            if values.get(field) is None and feature.get(field) is not None:
                values[field] = feature[field]
                fallback_used = True
        provider_count = int(values.get("margin_history_sample_count") or 0)
        local_count = int(feature.get("financing_net_buy_observations") or 0)
        values["margin_history_sample_count"] = max(provider_count, local_count)
        provider_sequence_complete = bool(
            values.get("margin_history_sequence_complete", False)
        )
        local_sequence_complete = bool(
            feature.get("margin_history_sequence_complete", False)
        )
        if fallback_used:
            values["margin_history_sequence_complete"] = local_sequence_complete
            values["margin_history_degraded"] = bool(
                feature.get("margin_history_degraded", True)
                or not local_sequence_complete
            )
        else:
            values["margin_history_sequence_complete"] = provider_sequence_complete
            values["margin_history_degraded"] = bool(
                values.get("margin_history_degraded", not provider_sequence_complete)
                or not provider_sequence_complete
            )
        # 两融是 T+1 日终披露，分母必须取同一披露交易日已经归档的
        # 板块真实成交额。禁止拿今天盘中的成交额除以前一交易日的
        # 融资买入额，否则比率会产生系统性错配。
        margin_date = str(values.get("as_of") or values.get("trade_date") or "")[:10]
        archived_turnover = feature.get("daily_turnover_by_trade_date")
        if margin_date and isinstance(archived_turnover, dict):
            matched_turnover = archived_turnover.get(margin_date)
            try:
                matched_turnover_value = float(matched_turnover)
            except (TypeError, ValueError):
                matched_turnover_value = 0.0
            if matched_turnover_value > 0:
                values["financing_reference_turnover"] = matched_turnover_value
                values["financing_turnover_as_of"] = margin_date
        if fallback_used and not str(values.get("margin_history_method") or "").strip():
            values["margin_history_method"] = (
                "本系统按 distinct margin_as_of 持久化的逐日融资净买入OLS斜率；"
                "融资余额占比60/120日经验历史分位"
            )

    result = build_sector_temperature(
        current_rows,
        five_rows,
        ten_rows,
        margin_by_name=margin_by_name,
        attention_by_name=attention_by_name,
        persistence_by_name=persistence_by_name,
        board_type=normalized,
        updated_at=effective_updated_at,
    )
    if db is not None and hasattr(db, "query") and result.get("items"):
        try:
            # Persistence confirmation must include the fact visible in this
            # very refresh.  Persist the instantaneous envelope first, reload
            # the immutable facts-only history, then build the final gated
            # conclusion.  The second write below is idempotent because the
            # sample fingerprint excludes derived confirmation fields.
            preliminary = SectorTemperatureOut.model_validate(result)
            persist_sector_temperature_snapshot(db, preliminary)
            persistence_by_name = load_sector_persistence_features(
                db,
                board_type=normalized,
            )
            result = build_sector_temperature(
                current_rows,
                five_rows,
                ten_rows,
                margin_by_name=margin_by_name,
                attention_by_name=attention_by_name,
                persistence_by_name=persistence_by_name,
                board_type=normalized,
                updated_at=effective_updated_at,
            )
        except Exception as exc:
            db.rollback()
            persistence_note = (
                f"当前事实先入账并重算持续性失败：{exc.__class__.__name__}；"
                "本轮保持瞬时观察结论，不提前确认高风险状态。"
            )
    notes = list(result.get("notes") or [])
    notes.extend(margin.get("notes") or [])
    if audited_flow_note:
        notes.append(audited_flow_note)
    if structure_note:
        notes.append(structure_note)
    if persistence_note:
        notes.append(persistence_note)
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
    if audited_flow_merged:
        result["source"] += "+授权审计非杠杆资金"
    validated = SectorTemperatureOut.model_validate(result)
    _set_response_cache(response_cache_key, validated)
    if db is not None and hasattr(db, "query"):
        try:
            persist_sector_temperature_snapshot(db, validated)
        except Exception:
            db.rollback()
            validated.notes.append("板块历史快照暂未写入，当前实时结果仍可查看；待数据库迁移或存储恢复后重试。")
    return validated


@router.get("/market/sector-temperature", response_model=SectorTemperatureOut)
@limiter.limit("12/minute")
def sector_temperature(
    request: Request,
    board_type: str = "行业",
    db: Session = Depends(get_db),
) -> SectorTemperatureOut:
    """Read the last explicitly collected multi-window temperature snapshot."""

    normalized = "概念" if board_type == "概念" else "行业"
    cached = _get_response_cache(f"sector-temperature|{normalized}", allow_stale=True)
    if cached is not None:
        return cached
    persisted = load_latest_sector_temperature_snapshot(db, board_type=normalized)
    if persisted is not None:
        restored = SectorTemperatureOut.model_validate(persisted)
        _set_response_cache(f"sector-temperature|{normalized}", restored)
        return restored
    return SectorTemperatureOut(
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
    db: Session = Depends(get_db),
) -> SectorTemperatureOut:
    """Explicitly collect multi-window flows and T+1 crowding evidence."""

    return _sector_temperature_snapshot(board_type=board_type, force_refresh=True, db=db)


@router.get("/market/sector-temperature/history")
@limiter.limit("20/minute")
def sector_temperature_history(
    request: Request,
    board_type: str | None = None,
    board_code: str | None = None,
    board_name: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 60,
    scope: str = "daily",
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    """Return archived evidence without reconstructing unavailable history.

    ``daily`` keeps the backward-compatible daily summaries; ``intraday``
    returns immutable provider observations; ``evolution`` returns the
    consecutive-sample/day confirmation path used by the strict six-state
    gate.
    """

    normalized_scope = (scope or "daily").strip().lower()
    if normalized_scope == "evolution":
        return build_sector_state_evolution(
            db,
            board_type=board_type,
            board_code=board_code,
            board_name=board_name,
            sample_limit=min(max(int(limit), 1), 24),
            board_limit=20,
        )
    if normalized_scope == "intraday":
        samples = load_sector_samples(
            db,
            board_type=board_type,
            board_code=board_code,
            board_name=board_name,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            ascending=True,
        )
        return [
            {
                "trade_date": row.trade_date,
                "board_type": row.board_type,
                "board_code": row.board_code,
                "name": row.board_name,
                "captured_at": row.captured_at,
                "provider_updated_at": row.provider_updated_at,
                "source": row.source,
                "data_quality": row.data_quality,
                "instantaneous_state": row.instantaneous_distribution_state,
                "resolved_state": row.distribution_state,
                "risk_level": row.distribution_risk_level,
                "risk_score": row.distribution_risk_score,
                "change_pct": row.change_pct,
                "net_inflow": row.net_inflow,
                "flow_speed": row.flow_speed,
                "flow_acceleration": row.flow_acceleration,
                "flow_turning": row.flow_turning,
                "margin_as_of": row.margin_as_of,
            }
            for row in samples
        ]
    if normalized_scope != "daily":
        raise HTTPException(status_code=422, detail="scope 仅支持 daily、intraday 或 evolution")

    rows = load_sector_history(
        db,
        board_type=board_type,
        board_code=board_code,
        board_name=board_name,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        ascending=True,
    )
    def array(raw: str) -> list[object]:
        try:
            value = json.loads(raw or "[]")
        except (TypeError, ValueError):
            value = []
        return value if isinstance(value, list) else []

    output: list[dict[str, object]] = []
    for row in rows:
        output.append({
            "trade_date": row.trade_date,
            "board_type": row.board_type,
            "board_code": row.board_code,
            "name": row.board_name,
            "captured_at": row.captured_at,
            "source": row.source,
            "data_quality": row.data_quality,
            "change_pct": row.change_pct,
            "change_pct_5d": row.change_pct_5d,
            "change_pct_10d": row.change_pct_10d,
            "net_inflow": row.net_inflow,
            "net_inflow_5d": row.net_inflow_5d,
            "net_inflow_10d": row.net_inflow_10d,
            "financing_balance": row.financing_balance,
            "financing_net_buy": row.financing_net_buy,
            "margin_as_of": row.margin_as_of,
            "distribution_state": row.distribution_state,
            "distribution_risk_level": row.distribution_risk_level,
            "distribution_risk_score": row.distribution_risk_score,
            "distribution_confirmation_count": row.distribution_confirmation_count,
            "distribution_evidence": array(row.distribution_evidence_json),
            "distribution_counter_evidence": array(row.distribution_counter_evidence_json),
            "distribution_actions": array(row.distribution_actions_json),
        })
    return output


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
    db: Session = Depends(get_db),
) -> GlobalMarketOut:
    """Return traceable overseas evidence without a provider call on GET.

    A worker restart clears the process cache but not the evidence ledger.  In
    that case the newest immutable DB snapshot is returned with its persisted
    origin and timestamp instead of a misleading empty panel.
    """

    cached = global_market_service.read_cached_snapshot()
    quote_groups = (
        "korea_indices",
        "korea_equities",
        "us_indices",
        "us_sector_rank",
        "strategic_assets",
        "macro_indicators",
    )
    metric_groups = (
        "etf_flows",
        "korea_foreign_flows",
        "korea_leverage_products",
        "official_rates",
    )
    has_cached_evidence = any(
        str(item.get("status") or "").lower() in {"ok", "delayed"}
        for key in quote_groups
        for item in list(cached.get(key) or [])
        if isinstance(item, dict)
    ) or any(
        str(item.get("status") or "").lower() == "ok"
        for key in metric_groups
        for item in list(cached.get(key) or [])
        if isinstance(item, dict)
    )
    persisted = load_latest_global_evidence_snapshot(db)
    persisted_is_newer = (
        persisted is not None
        and (
            not has_cached_evidence
            or global_evidence_recency_key(persisted) > global_evidence_recency_key(cached)
        )
    )
    if persisted_is_newer:
        notes = list(persisted.get("notes") or [])
        notes.append(
            "当前展示数据库中的最新不可变外围证据快照；它比本进程缓存更新，且本次未触发外部刷新。"
            if has_cached_evidence
            else "进程缓存为空，当前展示数据库中的最近一次不可变外围证据快照；未触发外部刷新。"
        )
        persisted["notes"] = list(dict.fromkeys(notes))
        return GlobalMarketOut.model_validate(persisted)

    if has_cached_evidence:
        payload = dict(cached)
        payload["snapshot_origin"] = "process_cache"
        return GlobalMarketOut.model_validate(payload)

    if persisted is not None:
        notes = list(persisted.get("notes") or [])
        notes.append("进程缓存为空，当前展示数据库中最近一次不可变外围证据快照；未触发外部刷新。")
        persisted["notes"] = list(dict.fromkeys(notes))
        return GlobalMarketOut.model_validate(persisted)
    payload = dict(cached)
    payload["snapshot_origin"] = "unavailable"
    return GlobalMarketOut.model_validate(payload)


@router.get("/market/global-cues/history")
@limiter.limit("12/minute")
def global_market_cues_history(
    request: Request,
    scope: str = "snapshots",
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 60,
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    """Read the auditable global evidence ledger or its transition path."""

    normalized_scope = str(scope or "snapshots").strip().lower()
    if normalized_scope == "snapshots":
        return load_global_evidence_history(
            db,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
            ascending=False,
        )
    if normalized_scope == "evolution":
        return build_global_evidence_evolution(
            db,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )
    raise HTTPException(status_code=422, detail="scope 仅支持 snapshots 或 evolution")


@router.post("/market/global-cues/refresh", response_model=GlobalMarketOut)
@limiter.limit("4/minute")
def refresh_global_market_cues(
    request: Request,
    db: Session = Depends(get_db),
) -> GlobalMarketOut:
    refreshed = GlobalMarketOut.model_validate(
        global_market_service.snapshot(force_refresh=True)
    )
    try:
        persist_global_evidence_snapshot(db, refreshed)
    except Exception as exc:
        db.rollback()
        # The provider refresh succeeded, so preserve that useful result while
        # exposing the persistence fault instead of silently discarding it.
        refreshed.notes.append(
            f"外围证据已刷新，但历史快照持久化失败：{exc.__class__.__name__}；当前结果仍可查看。"
        )
    return refreshed

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
