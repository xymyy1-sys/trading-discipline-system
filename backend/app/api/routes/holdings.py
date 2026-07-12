import asyncio
import json
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.core.database import SessionLocal, get_db
from app.models.trading import Holding, TradeLog
from app.schemas.trading import (
    HoldingCreate,
    HoldingUpdate,
    HoldingOut,
    HoldingRefreshOut,
    HoldingSyncOut,
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
from app.api.helpers.quotes import _latest_a_share_quotes, _quote_lookup_code
from app.api.helpers.decision import build_t_eligibility, create_t_plan, update_t_plan
from app.models.trading import (
    ActionRecommendation,
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
        grouped.setdefault(row.source, []).append(row)
    providers = []
    for source, samples in grouped.items():
        providers.append(DataProviderHealthOut(
            source=source,
            sample_count=len(samples),
            success_count=sum(item.status == "ok" and not item.is_degraded for item in samples),
            degraded_count=sum(item.is_degraded for item in samples),
            stale_count=sum(item.is_stale for item in samples),
            average_latency_ms=round(sum(item.latency_ms for item in samples) / len(samples), 1),
            latest_status=samples[0].status,
            latest_at=samples[0].captured_at,
        ))
    return DataQualityHealthOut(generated_at=datetime.now(), providers=providers)

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
        **_holding_account_summary(outputs, account_total_asset),
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
async def stream_intraday_events(replay: bool = False) -> StreamingResponse:
    async def event_generator():
        last_id: int | None = 0 if replay else None
        while True:
            db = SessionLocal()
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
            finally:
                db.close()
            await asyncio.sleep(5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.post("/recommendations/{recommendation_id}/execution-feedback", response_model=RecommendationFeedbackOut)
def create_recommendation_feedback(
    recommendation_id: int,
    payload: RecommendationFeedbackIn,
    db: Session = Depends(get_db),
) -> RecommendationFeedbackOut:
    recommendation = db.get(ActionRecommendation, recommendation_id)
    if recommendation is None:
        raise HTTPException(status_code=404, detail="recommendation not found")
    feedback = RecommendationFeedback(
        recommendation_id=recommendation_id,
        status=payload.status,
        reason=payload.reason,
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback

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
        **_holding_account_summary(outputs, account_total_asset),
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
