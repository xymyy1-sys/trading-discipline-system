import asyncio
import csv
import io
import json
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.core.database import DemoSessionLocal, SessionLocal, get_db
from app.core.config import get_settings
from app.models.trading import Holding, TradeLog
from app.schemas.trading import (
    HoldingCreate,
    HoldingUpdate,
    HoldingOut,
    HoldingRefreshOut,
    HoldingSyncOut,
    HoldingAccountSummaryOut,
    PortfolioExposureItemOut,
    PortfolioExposureOut,
    AccountAssetIn,
    AccountAssetOut,
    AccountRiskIn,
    AccountRiskOut,
    PositionExecutionStateOut,
    ActionRecommendationOut,
    PositionStateHistoryOut,
    RecommendationFeedbackIn,
    RecommendationFeedbackOut,
    IntradayCollectorStatusOut,
    CollectionRunOut,
    ProfitProtectionSnapshotOut,
    StopLevelsOut,
    TimeStopRuleOut,
    TimeStopRuleUpdate,
    TEligibilityOut,
    TTradePlanIn,
    TTradePlanOut,
    TTradePlanUpdate,
    DataQualityHealthOut,
    DataProviderHealthOut,
)
from app.services.account_risk import account_risk
from app.api.helpers.holdings_calc import (
    _account_state,
    _account_total_asset,
    _holding_out,
    _refresh_holding_prices,
    _rebuild_holdings_from_trades,
    _holding_account_summary
)
from app.api.helpers.execution import build_execution_states, build_position_execution_state
from app.api.helpers.seesaw import _holding_theme_profile, _sector_family
from app.api.helpers.quotes import _latest_a_share_quotes, _quote_lookup_code
from app.api.helpers.decision import build_t_eligibility, build_volume_price_snapshot, create_t_plan, update_t_plan
from app.models.trading import (
    ActionRecommendation,
    ActionRecommendationRevision,
    IntradayCollectionRun,
    IntradayEvidenceEvent,
    PositionExecutionState,
    PositionStateHistory,
    ProfitProtectionSnapshot,
    RecommendationFeedback,
    TTradePlan,
    TimeStopRule,
    DataCaptureSnapshot,
)
from app.services.intraday_collector import (
    collection_notes,
    collector_status,
    run_intraday_collection_once,
)

router = APIRouter()


@router.get("/data-quality/health", response_model=DataQualityHealthOut)
def data_quality_health(db: Session = Depends(get_db)) -> DataQualityHealthOut:
    rows = db.query(DataCaptureSnapshot).order_by(DataCaptureSnapshot.captured_at.desc()).limit(1000).all()
    grouped: dict[str, list[DataCaptureSnapshot]] = {}
    for row in rows:
        grouped.setdefault(f"{row.source}:{row.data_type}", []).append(row)
    providers = []
    latest_trade_date = max((item.trade_date for item in rows if item.trade_date), default="")
    for samples in grouped.values():
        source = samples[0].source
        missing_count = sum(item.status not in {"ok", "success"} or item.quality == "missing" for item in samples)
        sample_trade_dates = {item.trade_date for item in samples if item.trade_date}
        providers.append(DataProviderHealthOut(
            source=source,
            data_type=samples[0].data_type,
            sample_count=len(samples),
            success_count=sum(item.status == "ok" and not item.is_degraded for item in samples),
            degraded_count=sum(item.is_degraded for item in samples),
            stale_count=sum(item.is_stale for item in samples),
            missing_count=missing_count,
            missing_rate=round(missing_count / len(samples) * 100, 1),
            average_latency_ms=round(sum(item.latency_ms for item in samples) / len(samples), 1),
            latest_status=samples[0].status,
            latest_at=samples[0].captured_at,
            latest_trade_date=samples[0].trade_date,
            trade_date_consistent=not latest_trade_date or sample_trade_dates == {latest_trade_date},
            degraded_source=(source if samples[0].is_degraded else ""),
        ))
    return DataQualityHealthOut(generated_at=datetime.now(), providers=providers)


@router.post("/data-quality/captures/{capture_id}/recompute")
def recompute_capture(capture_id: int, db: Session = Depends(get_db)) -> dict:
    """从留存的原始响应重算量价快照；原始记录保持不可变。"""
    capture = db.get(DataCaptureSnapshot, capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    if capture.data_type not in {"stock_minute", "tracked_stock_minute"}:
        raise HTTPException(status_code=409, detail="this capture type has no registered recompute adapter")
    try:
        raw_value = json.loads(capture.raw_value_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="raw capture is invalid JSON") from exc
    snapshot = build_volume_price_snapshot(
        db,
        capture.target_code,
        name=capture.target_name,
        stage="原始响应重算",
        quote=raw_value,
    )
    return {
        "capture_id": capture.id,
        "raw_payload_hash": capture.raw_payload_hash,
        "trade_date": capture.trade_date,
        "code": capture.target_code,
        "price": snapshot.price,
        "vwap": snapshot.vwap,
        "pattern": snapshot.pattern,
        "data_quality": snapshot.data_quality,
        "recomputed_at": datetime.now().isoformat(),
    }

DEFAULT_TIME_STOP_RULE_ROWS = [
    ("default", "默认剧本", "10:00", 5, 5, 15, 0.985),
    ("breakout", "打板/冲板", "09:45", 3, 3, 10, 0.99),
    ("trend", "趋势/容量", "10:30", 8, 6, 20, 0.985),
]


def _collection_run_out(row: IntradayCollectionRun | None) -> CollectionRunOut | None:
    if row is None:
        return None
    return CollectionRunOut(
        id=row.id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        trigger=row.trigger,
        holding_count=row.holding_count,
        snapshot_count=row.snapshot_count,
        event_count=row.event_count,
        notes=collection_notes(row),
        error_message=row.error_message,
    )


def _recommendation_out(row: ActionRecommendation, db: Session) -> ActionRecommendationOut:
    feedback = db.query(RecommendationFeedback).filter(RecommendationFeedback.recommendation_id == row.id).order_by(RecommendationFeedback.created_at.desc()).first()
    return ActionRecommendationOut(
        id=row.id,
        trade_date=row.trade_date,
        holding_id=row.holding_id,
        code=row.code,
        name=row.name,
        level=row.level,
        state=row.state,
        action=row.action,
        recommended_ratio=row.recommended_ratio,
        evidence=json.loads(row.evidence_json or "[]"),
        counter_evidence=json.loads(row.counter_evidence_json or "[]"),
        invalid_conditions=json.loads(row.invalid_conditions_json or "[]"),
        recovery_conditions=json.loads(row.recovery_conditions_json or "[]"),
        created_at=row.created_at,
        expires_at=row.expires_at,
        acknowledged_at=row.acknowledged_at,
        feedback_status=feedback.status if feedback else "",
    )


@router.get("/alerts/active", response_model=list[ActionRecommendationOut])
def list_active_alerts(include_acknowledged: bool = False, db: Session = Depends(get_db)) -> list[ActionRecommendationOut]:
    now = datetime.now()
    rows = db.query(ActionRecommendation).filter(ActionRecommendation.expires_at.is_not(None), ActionRecommendation.expires_at >= now).order_by(ActionRecommendation.created_at.desc()).limit(500).all()
    latest_by_target: dict[str, ActionRecommendation] = {}
    for row in rows:
        if not include_acknowledged and row.acknowledged_at is not None:
            continue
        key = str(row.holding_id or row.code)
        latest_by_target.setdefault(key, row)
    return [_recommendation_out(row, db) for row in latest_by_target.values()]


@router.post("/alerts/{recommendation_id}/acknowledge", response_model=ActionRecommendationOut)
def acknowledge_alert(recommendation_id: int, db: Session = Depends(get_db)) -> ActionRecommendationOut:
    row = db.get(ActionRecommendation, recommendation_id)
    if row is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    row.acknowledged_at = datetime.now()
    db.commit()
    db.refresh(row)
    return _recommendation_out(row, db)


def _ensure_time_stop_rules(db: Session) -> list[TimeStopRule]:
    existing = {row.script_type: row for row in db.query(TimeStopRule).all()}
    changed = False
    for script_type, display_name, deadline, minutes, bars, window, reseal_pct in DEFAULT_TIME_STOP_RULE_ROWS:
        if script_type in existing:
            continue
        row = TimeStopRule(
            script_type=script_type,
            display_name=display_name,
            confirmation_deadline=deadline,
            below_vwap_minutes=minutes,
            below_vwap_min_bars=bars,
            recent_window_minutes=window,
            failed_limit_reseal_pct=reseal_pct,
            enabled=True,
        )
        db.add(row)
        changed = True
    if changed:
        db.commit()
    return db.query(TimeStopRule).order_by(TimeStopRule.id.asc()).all()

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


@router.get("/account/risk", response_model=AccountRiskOut)
def get_account_risk(db: Session = Depends(get_db)) -> AccountRiskOut:
    return account_risk(db)


@router.put("/account/risk", response_model=AccountRiskOut)
def update_account_risk(payload: AccountRiskIn, db: Session = Depends(get_db)) -> AccountRiskOut:
    return account_risk(db, payload)

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


@router.get("/holdings/summary", response_model=HoldingAccountSummaryOut)
def holding_account_summary(db: Session = Depends(get_db)) -> HoldingAccountSummaryOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    account_total_asset = _account_total_asset(db)
    outputs = [_holding_out(item, account_total_asset=account_total_asset) for item in holdings]
    return HoldingAccountSummaryOut(
        **_holding_account_summary(outputs, account_total_asset, db),
        calculated_at=datetime.now(),
    )


@router.get("/holdings/portfolio-exposure", response_model=PortfolioExposureOut)
def holding_portfolio_exposure(db: Session = Depends(get_db)) -> PortfolioExposureOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    total = sum(float(item.current_price or 0) * int(item.quantity or 0) for item in holdings)

    def aggregate(kind: str) -> list[PortfolioExposureItemOut]:
        buckets: dict[str, dict[str, object]] = {}
        for holding in holdings:
            profile = _holding_theme_profile(holding)
            if kind == "industry":
                names = [str(profile.get("industry") or profile.get("primary") or "行业待确认")]
            elif kind == "theme":
                names = [str(value) for value in (profile.get("concepts") or profile.get("tags") or [])[:3]] or ["题材待确认"]
            elif kind == "style":
                names = [holding.position_type or "风格待确认"]
            else:
                names = [_sector_family(str(profile.get("primary") or "")) or "其他风险因子"]
            value = float(holding.current_price or 0) * int(holding.quantity or 0)
            for name in dict.fromkeys(names):
                bucket = buckets.setdefault(name, {"value": 0.0, "codes": []})
                bucket["value"] = float(bucket["value"]) + value
                cast_codes = bucket["codes"]
                if isinstance(cast_codes, list):
                    cast_codes.append(holding.code)
        return sorted([
            PortfolioExposureItemOut(
                name=name, market_value=round(float(bucket["value"]), 2),
                ratio=round(float(bucket["value"]) / total, 4) if total else 0,
                holding_count=len(set(bucket["codes"] if isinstance(bucket["codes"], list) else [])),
                codes=list(dict.fromkeys(bucket["codes"] if isinstance(bucket["codes"], list) else [])),
            ) for name, bucket in buckets.items()
        ], key=lambda item: item.ratio, reverse=True)

    industries, themes, styles, factors = aggregate("industry"), aggregate("theme"), aggregate("style"), aggregate("risk")
    warnings = []
    for label, rows in (("行业", industries), ("题材", themes), ("单一风险因子", factors)):
        if rows and rows[0].ratio >= 0.5:
            warnings.append(f"{label}“{rows[0].name}”暴露 {rows[0].ratio:.1%}，超过50%集中度警戒线。")
    return PortfolioExposureOut(
        generated_at=datetime.now(), total_market_value=round(total, 2), industries=industries,
        themes=themes, styles=styles, risk_factors=factors, warnings=warnings,
    )


def _csv_download(filename: str, headers: list[str], rows: list[list[object]]) -> StreamingResponse:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    content = "\ufeff" + buffer.getvalue()
    return StreamingResponse(iter([content]), media_type="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


@router.get("/exports/holdings.csv")
def export_holdings(db: Session = Depends(get_db)) -> StreamingResponse:
    holdings = db.query(Holding).order_by(Holding.code.asc()).all()
    total_asset = _account_total_asset(db)
    outputs = [_holding_out(item, account_total_asset=total_asset) for item in holdings]
    return _csv_download(
        f"holdings-{datetime.now().date().isoformat()}.csv",
        ["代码", "名称", "数量", "成本价", "现价", "市值", "浮动盈亏", "仓位类型", "执行纪律", "更新时间"],
        [[item.code, item.name, item.quantity, item.cost_price, item.current_price, item.market_value, item.profit_amount, item.position_type, item.next_discipline, item.updated_at.isoformat()] for item in outputs],
    )


@router.get("/exports/trades.csv")
def export_trades(db: Session = Depends(get_db)) -> StreamingResponse:
    trades = db.query(TradeLog).order_by(TradeLog.traded_at.asc(), TradeLog.id.asc()).all()
    return _csv_download(
        f"trades-{datetime.now().date().isoformat()}.csv",
        ["时间", "代码", "名称", "方向", "价格", "数量", "金额", "成本价", "原因", "模式", "是否合规"],
        [[item.traded_at.isoformat(), item.code, item.name, item.side, item.price, item.quantity, item.amount, item.cost_price, item.reason, item.mode, "是" if item.compliant else "否"] for item in trades],
    )

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
        **_holding_account_summary(outputs, account_total_asset, db),
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


@router.get("/holdings/{holding_id}/state-history", response_model=list[PositionStateHistoryOut])
def get_holding_state_history(
    holding_id: int,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> list[PositionStateHistoryOut]:
    rows = (
        db.query(PositionStateHistory)
        .filter(PositionStateHistory.holding_id == holding_id)
        .order_by(PositionStateHistory.captured_at.desc(), PositionStateHistory.id.desc())
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return [
        PositionStateHistoryOut(
            id=row.id,
            holding_id=row.holding_id,
            code=row.code,
            name=row.name,
            trade_date=row.trade_date,
            old_state=row.old_state,
            new_state=row.new_state,
            captured_at=row.captured_at,
            reason=row.reason,
            evidence=json.loads(row.evidence_json or "[]"),
        )
        for row in rows
    ]


@router.get("/holdings/{holding_id}/profit-protection", response_model=list[ProfitProtectionSnapshotOut])
def get_holding_profit_protection(
    holding_id: int,
    db: Session = Depends(get_db),
) -> list[ProfitProtectionSnapshotOut]:
    rows = (
        db.query(ProfitProtectionSnapshot)
        .filter(ProfitProtectionSnapshot.holding_id == holding_id)
        .order_by(ProfitProtectionSnapshot.captured_at.desc(), ProfitProtectionSnapshot.id.desc())
        .limit(50)
        .all()
    )
    return [
        ProfitProtectionSnapshotOut(
            id=row.id,
            holding_id=row.holding_id,
            code=row.code,
            captured_at=row.captured_at,
            current_profit_pct=row.current_profit_pct,
            maximum_profit_pct=row.maximum_profit_pct,
            profit_drawdown_pct=row.profit_drawdown_pct,
            maximum_price=row.maximum_price,
            maximum_profit_at=row.maximum_profit_at,
            day_max_profit_pct=row.day_max_profit_pct,
            day_max_profit_at=row.day_max_profit_at,
            protection_level=row.protection_level,
            protection_floor=row.protection_floor,
            triggered=row.triggered,
            recommended_action=row.recommended_action,
        )
        for row in rows
    ]


@router.get("/holdings/{holding_id}/stop-levels", response_model=StopLevelsOut)
def get_holding_stop_levels(
    holding_id: int,
    db: Session = Depends(get_db),
) -> StopLevelsOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    latest = (
        db.query(PositionExecutionState)
        .filter(PositionExecutionState.holding_id == holding_id)
        .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
        .first()
    )
    if latest is None:
        state = get_holding_execution_state(holding_id, db)
        return StopLevelsOut(
            holding_id=holding_id,
            code=holding.code,
            name=holding.name,
            structure_stop_price=state.structure_stop_price,
            hard_stop_price=state.hard_stop_price,
            stop_source=state.stop_source,
            stop_source_detail=state.stop_source_detail,
            trailing_stop_price=state.trailing_stop_price,
            profit_protection_price=state.profit_protection_price,
            data_quality=state.data_quality,
            evidence=state.evidence,
            invalid_conditions=state.invalid_conditions,
        )
    return StopLevelsOut(
        holding_id=holding_id,
        code=latest.code,
        name=latest.name,
        structure_stop_price=latest.structure_stop_price,
        hard_stop_price=latest.hard_stop_price,
        stop_source=getattr(latest, "stop_source", "fallback_candidate") or "fallback_candidate",
        stop_source_detail=getattr(latest, "stop_source_detail", "") or "",
        trailing_stop_price=latest.trailing_stop_price,
        profit_protection_price=latest.profit_protection_price,
        data_quality=latest.data_quality,
        evidence=json.loads(latest.evidence_json or "[]"),
        invalid_conditions=json.loads(latest.invalid_conditions_json or "[]"),
    )


@router.get("/intraday-collector/status", response_model=IntradayCollectorStatusOut)
def get_intraday_collector_status() -> IntradayCollectorStatusOut:
    status = collector_status()
    return IntradayCollectorStatusOut(
        enabled=bool(status["enabled"]),
        interval_seconds=int(status["interval_seconds"]),
        running=bool(status["running"]),
        queue_depth=int(status.get("queue_depth") or 0),
        open_circuits=list(status.get("open_circuits") or []),
        failure_counts=dict(status.get("failure_counts") or {}),
        last_run=_collection_run_out(status["last_run"]),
    )


@router.post("/intraday-collector/run", response_model=CollectionRunOut)
def trigger_intraday_collector() -> CollectionRunOut:
    row = run_intraday_collection_once("manual")
    return _collection_run_out(row)  # type: ignore[return-value]


@router.get("/time-stop-rules", response_model=list[TimeStopRuleOut])
def list_time_stop_rules(db: Session = Depends(get_db)) -> list[TimeStopRuleOut]:
    return _ensure_time_stop_rules(db)


@router.put("/time-stop-rules/{script_type}", response_model=TimeStopRuleOut)
def update_time_stop_rule(
    script_type: str,
    payload: TimeStopRuleUpdate,
    db: Session = Depends(get_db),
) -> TimeStopRuleOut:
    _ensure_time_stop_rules(db)
    row = db.query(TimeStopRule).filter(TimeStopRule.script_type == script_type).first()
    if row is None:
        raise HTTPException(status_code=404, detail="time stop rule not found")
    if payload.confirmation_deadline is not None:
        if not re.match(r"^\d{1,2}[:：]\d{2}$", payload.confirmation_deadline):
            raise HTTPException(status_code=400, detail="confirmation_deadline must be HH:MM")
        row.confirmation_deadline = payload.confirmation_deadline.replace("：", ":")
    if payload.below_vwap_minutes is not None:
        row.below_vwap_minutes = min(60, max(1, int(payload.below_vwap_minutes)))
    if payload.below_vwap_min_bars is not None:
        row.below_vwap_min_bars = min(30, max(1, int(payload.below_vwap_min_bars)))
    if payload.recent_window_minutes is not None:
        row.recent_window_minutes = min(90, max(1, int(payload.recent_window_minutes)))
    if payload.failed_limit_reseal_pct is not None:
        row.failed_limit_reseal_pct = min(1.0, max(0.9, float(payload.failed_limit_reseal_pct)))
    if payload.enabled is not None:
        row.enabled = bool(payload.enabled)
    row.updated_at = datetime.now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/intraday-events/stream")
async def stream_intraday_events(request: Request, replay: bool = False) -> StreamingResponse:
    settings = get_settings()
    session_factory = DemoSessionLocal if getattr(request.state, "auth_user", "") == settings.demo_username and settings.demo_password else SessionLocal
    async def event_generator():
        last_id: int | None = 0 if replay else None
        heartbeat = 0
        while True:
            db = session_factory()
            try:
                if last_id is None:
                    latest = db.query(IntradayEvidenceEvent).order_by(IntradayEvidenceEvent.id.desc()).first()
                    last_id = int(latest.id or 0) if latest else 0
                    yield "event: stream-ready\ndata: {}\n\n"
                rows = (
                    db.query(IntradayEvidenceEvent)
                    .filter(IntradayEvidenceEvent.id > last_id)
                    .order_by(IntradayEvidenceEvent.id.asc())
                    .limit(20)
                    .all()
                )
                for row in rows:
                    last_id = max(last_id, int(row.id or 0))
                    payload = {
                        "id": row.id,
                        "captured_at": row.captured_at.isoformat(),
                        "target_code": row.target_code,
                        "target_name": row.target_name,
                        "event_type": row.event_type,
                        "severity": row.severity,
                        "priority": row.priority,
                        "confirmed": row.confirmed,
                        "occurrence_count": row.occurrence_count,
                        "evidence": json.loads(row.evidence_json or "[]"),
                    }
                    yield f"event: intraday-risk\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                heartbeat += 1
                if heartbeat % 3 == 0:
                    yield f": heartbeat {datetime.now().isoformat()}\n\n"
            finally:
                db.close()
            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })

@router.post("/recommendations/{recommendation_id}/execution-feedback", response_model=RecommendationFeedbackOut)
def create_recommendation_feedback(
    recommendation_id: int,
    payload: RecommendationFeedbackIn,
    db: Session = Depends(get_db),
) -> RecommendationFeedbackOut:
    recommendation = db.get(ActionRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    allowed_statuses = {"已执行", "部分执行", "不同意", "未成交", "没看到", "纪律违背"}
    if payload.status not in allowed_statuses:
        raise HTTPException(status_code=422, detail="unsupported feedback status")
    matched_trade = None
    if payload.status in {"已执行", "部分执行", "未成交"}:
        matched_trade = (
            db.query(TradeLog)
            .filter(TradeLog.code == recommendation.code, TradeLog.traded_at >= recommendation.created_at)
            .order_by(TradeLog.traded_at.desc(), TradeLog.id.desc())
            .first()
        )
    feedback = RecommendationFeedback(
        recommendation_id=recommendation_id,
        status=payload.status,
        reason=payload.reason,
        trade_id=matched_trade.id if matched_trade else None,
        result="已匹配成交" if matched_trade else ("明确未成交" if payload.status == "未成交" else "待匹配成交"),
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


@router.get("/recommendations/{recommendation_id}/history")
def recommendation_history(recommendation_id: int, db: Session = Depends(get_db)) -> list[dict]:
    recommendation = db.get(ActionRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    rows = (
        db.query(ActionRecommendationRevision)
        .filter(ActionRecommendationRevision.recommendation_id == recommendation_id)
        .order_by(ActionRecommendationRevision.version.asc(), ActionRecommendationRevision.id.asc())
        .all()
    )
    return [{
        "version": row.version,
        "level": row.level,
        "state": row.state,
        "action": row.action,
        "recommended_ratio": row.recommended_ratio,
        "evidence": json.loads(row.evidence_json or "[]"),
        "counter_evidence": json.loads(row.counter_evidence_json or "[]"),
        "invalid_conditions": json.loads(row.invalid_conditions_json or "[]"),
        "recovery_conditions": json.loads(row.recovery_conditions_json or "[]"),
        "created_at": row.created_at.isoformat(),
    } for row in rows]

@router.get("/holdings/{holding_id}/t-eligibility", response_model=TEligibilityOut)
def get_holding_t_eligibility(
    holding_id: int,
    db: Session = Depends(get_db),
) -> TEligibilityOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    return build_t_eligibility(db, holding)


@router.get("/t-plans", response_model=list[TTradePlanOut])
def list_t_plans(active_only: bool = False, db: Session = Depends(get_db)) -> list[TTradePlanOut]:
    query = db.query(TTradePlan)
    if active_only:
        query = query.filter(TTradePlan.status.in_(("planned", "sold_wait_buyback", "partially_bought_back")))
    rows = query.order_by(TTradePlan.updated_at.desc(), TTradePlan.id.desc()).limit(200).all()
    from app.services.t_trading_engine import _t_plan_out
    return [_t_plan_out(row) for row in rows]

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
    try:
        return update_t_plan(db, plan, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

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
        **_holding_account_summary(outputs, account_total_asset, db),
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
