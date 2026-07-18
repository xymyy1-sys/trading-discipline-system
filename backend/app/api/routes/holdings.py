import asyncio
import csv
import io
import json
import re
from datetime import datetime
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.database import DemoSessionLocal, SessionLocal, get_db
from app.core.config import get_settings
from app.models.trading import AccountState, Holding, TradeLog
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
    IntradayEvidenceEventOut,
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
    _read_account_total_asset,
    _holding_out,
    _refresh_holding_prices,
    _rebuild_holdings_from_trades,
    _holding_account_summary
)
from app.api.helpers.execution import read_persisted_execution_state, read_persisted_execution_states
from app.api.helpers.seesaw import _holding_theme_profile, _sector_family
from app.api.helpers.decision import (
    _event_in_trade_session,
    build_t_eligibility,
    build_volume_price_snapshot,
    create_t_plan,
    update_t_plan,
)
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
from app.services.recommendation_feedback import (
    rematch_execution_feedback_for_codes,
    resolve_feedback_execution,
)
from app.core.trading_clock import shanghai_now_naive, shanghai_today

router = APIRouter()


FEEDBACK_STATUS_CODES = {
    "已执行": "executed",
    "部分执行": "partially_executed",
    "不同意": "rejected",
    "忽略": "rejected",
    "暂不执行": "rejected",
    "未成交": "not_filled",
    "没看到": "not_seen",
    "纪律违背": "discipline_breach",
}


def _feedback_event_matches(
    row: RecommendationFeedback,
    *,
    recommendation_id: int,
    revision_id: int | None,
    status_code: str,
    payload: RecommendationFeedbackIn,
) -> bool:
    return (
        row.recommendation_id == recommendation_id
        and row.recommendation_revision_id == revision_id
        and _feedback_status_code(row) == status_code
        and str(row.reason or "") == str(payload.reason or "")
        and (
            payload.executed_quantity is None
            or int(row.executed_quantity or 0) == int(payload.executed_quantity)
        )
        and (
            payload.executed_ratio is None
            or abs(float(row.executed_ratio or 0) - float(payload.executed_ratio)) < 1e-9
        )
        and (
            payload.executed_price is None
            or abs(float(row.executed_price or 0) - float(payload.executed_price)) < 1e-9
        )
    )


def _feedback_status_code(row: RecommendationFeedback) -> str:
    return str(row.status_code or FEEDBACK_STATUS_CODES.get(str(row.status or ""), "legacy_unknown"))


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
    revision = db.get(ActionRecommendationRevision, row.current_revision_id) if row.current_revision_id else None
    if revision is None:
        revision = (
            db.query(ActionRecommendationRevision)
            .filter(ActionRecommendationRevision.recommendation_id == row.id)
            .order_by(ActionRecommendationRevision.version.desc(), ActionRecommendationRevision.id.desc())
            .first()
        )
    feedback_query = db.query(RecommendationFeedback).filter(
        RecommendationFeedback.recommendation_id == row.id,
    )
    if revision is not None:
        feedback_query = feedback_query.filter(
            RecommendationFeedback.recommendation_revision_id == revision.id,
        )
    feedback = feedback_query.order_by(
        RecommendationFeedback.created_at.desc(),
        RecommendationFeedback.id.desc(),
    ).first()
    return ActionRecommendationOut(
        id=row.id,
        revision_id=revision.id if revision else None,
        revision_version=int(revision.version or 0) if revision else 0,
        decision_hash=str(revision.decision_hash or row.current_decision_hash or "") if revision else str(row.current_decision_hash or ""),
        trade_date=row.trade_date,
        target_key=row.target_key or "",
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
        updated_at=row.updated_at,
        expires_at=row.expires_at,
        acknowledged_at=row.acknowledged_at,
        feedback_status=feedback.status if feedback else "",
    )


@router.get("/alerts/active", response_model=list[ActionRecommendationOut])
def list_active_alerts(include_acknowledged: bool = False, db: Session = Depends(get_db)) -> list[ActionRecommendationOut]:
    now = shanghai_now_naive()
    rows = (
        db.query(ActionRecommendation)
        .filter(
            ActionRecommendation.expires_at.is_not(None),
            ActionRecommendation.expires_at >= now,
        )
        .order_by(ActionRecommendation.updated_at.desc(), ActionRecommendation.id.desc())
        .limit(500)
        .all()
    )
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
    row.acknowledged_at = shanghai_now_naive()
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


def _read_time_stop_rules(db: Session) -> list[TimeStopRule | TimeStopRuleOut]:
    """Return persisted rules plus transient built-in defaults.

    Seeding belongs to the explicit update path.  This keeps a first visit to
    the execution page from inserting three rows and advancing database
    timestamps before the user has changed any setting.
    """
    persisted = db.query(TimeStopRule).order_by(TimeStopRule.id.asc()).all()
    by_type = {row.script_type: row for row in persisted}
    outputs: list[TimeStopRule | TimeStopRuleOut] = []
    default_types: set[str] = set()
    for script_type, display_name, deadline, minutes, bars, window, reseal_pct in DEFAULT_TIME_STOP_RULE_ROWS:
        default_types.add(script_type)
        row = by_type.get(script_type)
        if row is not None:
            outputs.append(row)
            continue
        outputs.append(TimeStopRuleOut(
            id=None,
            script_type=script_type,
            display_name=display_name,
            confirmation_deadline=deadline,
            below_vwap_minutes=minutes,
            below_vwap_min_bars=bars,
            recent_window_minutes=window,
            failed_limit_reseal_pct=reseal_pct,
            enabled=True,
            updated_at=datetime(1970, 1, 1),
        ))
    outputs.extend(row for row in persisted if row.script_type not in default_types)
    return outputs

@router.get("/account/asset", response_model=AccountAssetOut)
def get_account_asset(db: Session = Depends(get_db)) -> AccountAssetOut:
    state = db.get(AccountState, 1)
    return AccountAssetOut(
        total_asset=_read_account_total_asset(db),
        updated_at=state.updated_at if state is not None else None,
    )

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
    account_total_asset = _read_account_total_asset(db)
    return [
        _holding_out(
            item,
            account_total_asset=account_total_asset,
            price_note="持久化行情；点击“采集并刷新行情”获取最新价格。",
        )
        for item in holdings
    ]


@router.get("/holdings/summary", response_model=HoldingAccountSummaryOut)
def holding_account_summary(db: Session = Depends(get_db)) -> HoldingAccountSummaryOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    account_total_asset = _read_account_total_asset(db)
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
    total_asset = _read_account_total_asset(db)
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
    # Kept only for old clients.  GET is deliberately side-effect free even
    # when ``force_refresh=true``; callers that need a fresh persisted sample
    # must POST /api/intraday-collector/run first.
    _ = force_refresh
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return read_persisted_execution_states(db, holdings)

@router.get("/holdings/{holding_id}/execution-state", response_model=PositionExecutionStateOut)
def get_holding_execution_state(
    holding_id: int,
    db: Session = Depends(get_db),
) -> PositionExecutionStateOut:
    holding = db.get(Holding, holding_id)
    if holding is None:
        raise HTTPException(status_code=404, detail="holding not found")
    return read_persisted_execution_state(db, holding)


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
        last_success_at=status.get("last_success_at"),
        last_error=str(status.get("last_error") or ""),
        queue_depth=int(status.get("queue_depth") or 0),
        open_circuits=list(status.get("open_circuits") or []),
        failure_counts=dict(status.get("failure_counts") or {}),
        opportunity_radar_running=bool(status.get("opportunity_radar_running")),
        opportunity_radar_last_success_at=status.get("opportunity_radar_last_success_at"),
        opportunity_radar_last_error=str(status.get("opportunity_radar_last_error") or ""),
        simulation_shadow_running=bool(status.get("simulation_shadow_running")),
        simulation_shadow_last_success_at=status.get("simulation_shadow_last_success_at"),
        simulation_shadow_last_error=str(status.get("simulation_shadow_last_error") or ""),
        simulation_shadow_equity_last_success_at=status.get("simulation_shadow_equity_last_success_at"),
        simulation_shadow_equity_last_error=str(status.get("simulation_shadow_equity_last_error") or ""),
        last_run=_collection_run_out(status["last_run"]),
    )


@router.post("/intraday-collector/run", response_model=CollectionRunOut)
def trigger_intraday_collector() -> CollectionRunOut:
    row = run_intraday_collection_once("manual")
    return _collection_run_out(row)  # type: ignore[return-value]


@router.get("/time-stop-rules", response_model=list[TimeStopRuleOut])
def list_time_stop_rules(db: Session = Depends(get_db)) -> list[TimeStopRule | TimeStopRuleOut]:
    return _read_time_stop_rules(db)


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


def _safe_json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _safe_json_dict(raw: str | None) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _intraday_event_out(row: IntradayEvidenceEvent) -> IntradayEvidenceEventOut:
    return IntradayEvidenceEventOut(
        id=row.id,
        captured_at=row.captured_at,
        scope=row.scope,
        target_code=row.target_code,
        target_name=row.target_name,
        event_type=row.event_type,
        severity=row.severity,
        value=row.value,
        previous_value=row.previous_value,
        priority=row.priority,
        group_key=row.group_key,
        state_key=getattr(row, "state_key", None),
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        occurrence_count=row.occurrence_count,
        confirmed=row.confirmed,
        evidence=_safe_json_list(row.evidence_json),
        counter_evidence=_safe_json_list(getattr(row, "counter_evidence_json", "[]")),
        source=getattr(row, "source", "") or "",
        source_url=getattr(row, "source_url", None),
        source_published_at=getattr(row, "source_published_at", None),
        metadata=_safe_json_dict(getattr(row, "metadata_json", "{}")),
    )


@router.get("/intraday-events/recent", response_model=list[IntradayEvidenceEventOut])
def recent_intraday_events(limit: int = 40, db: Session = Depends(get_db)) -> list[IntradayEvidenceEventOut]:
    """Hydrate the cockpit without leaking watchlist-only stock events."""

    holding_codes = [str(row[0]) for row in db.query(Holding.code).all()]
    stock_scope = and_(
        IntradayEvidenceEvent.scope == "stock",
        IntradayEvidenceEvent.target_code.in_(holding_codes),
    ) if holding_codes else False
    rows = (
        db.query(IntradayEvidenceEvent)
        .filter(
            IntradayEvidenceEvent.trade_date == shanghai_today().isoformat(),
            or_(IntradayEvidenceEvent.scope.in_(("sector", "market")), stock_scope),
        )
        .order_by(
            IntradayEvidenceEvent.priority.desc(),
            IntradayEvidenceEvent.captured_at.desc(),
            IntradayEvidenceEvent.id.desc(),
        )
        .limit(min(100, max(1, int(limit))))
        .all()
    )
    today = shanghai_today().isoformat()
    rows = [
        row for row in rows
        if "NEWS_" not in str(row.event_type or "") or _event_in_trade_session(row, today)
    ]
    return [_intraday_event_out(row) for row in rows]


def _stream_cursor(request: Request, *, replay: bool, last_event_id: int | None) -> int | None:
    """Resolve an SSE cursor, giving an explicit query parameter precedence."""

    if last_event_id is not None:
        return max(0, int(last_event_id))
    header = str(request.headers.get("last-event-id") or "").strip()
    if header:
        try:
            return max(0, int(header))
        except ValueError:
            pass
    return 0 if replay else None


def _sse_event_frame(row: IntradayEvidenceEvent) -> str:
    payload = _intraday_event_out(row).model_dump(mode="json")
    return (
        f"id: {int(row.id or 0)}\n"
        "event: intraday-risk\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    )


@router.get("/intraday-events/stream")
async def stream_intraday_events(
    request: Request,
    replay: bool = False,
    last_event_id: int | None = None,
) -> StreamingResponse:
    settings = get_settings()
    session_factory = DemoSessionLocal if getattr(request.state, "auth_user", "") == settings.demo_username and settings.demo_password else SessionLocal
    async def event_generator():
        last_id = _stream_cursor(request, replay=replay, last_event_id=last_event_id)
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
                    if (
                        "NEWS_" in str(row.event_type or "")
                        and not _event_in_trade_session(row, shanghai_today().isoformat())
                    ):
                        continue
                    yield _sse_event_frame(row)
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
    status_code = FEEDBACK_STATUS_CODES.get(payload.status)
    if status_code is None:
        raise HTTPException(status_code=422, detail="unsupported feedback status")

    revision = None
    if payload.revision_id is not None:
        revision = db.get(ActionRecommendationRevision, payload.revision_id)
        if revision is None or revision.recommendation_id != recommendation_id:
            raise HTTPException(status_code=422, detail="revision does not belong to recommendation")
    else:
        compatible_revisions = (
            db.query(ActionRecommendationRevision)
            .filter(ActionRecommendationRevision.recommendation_id == recommendation_id)
            .order_by(ActionRecommendationRevision.version.desc(), ActionRecommendationRevision.id.desc())
            .limit(2)
            .all()
        )
        if len(compatible_revisions) > 1:
            raise HTTPException(
                status_code=422,
                detail="recommendation has multiple revisions; revision_id is required",
            )
        revision = compatible_revisions[0] if compatible_revisions else None

    client_event_id = (payload.client_event_id or "").strip() or str(uuid4())
    existing = (
        db.query(RecommendationFeedback)
        .filter(RecommendationFeedback.client_event_id == client_event_id)
        .first()
    )
    revision_id = revision.id if revision else None
    if existing is not None:
        if not _feedback_event_matches(
            existing,
            recommendation_id=recommendation_id,
            revision_id=revision_id,
            status_code=status_code,
            payload=payload,
        ):
            raise HTTPException(status_code=409, detail="client_event_id already used for different feedback")
        return existing

    now = shanghai_now_naive()
    resolution = resolve_feedback_execution(
        db,
        recommendation,
        revision,
        status_code,
        executed_quantity=payload.executed_quantity,
        executed_ratio=payload.executed_ratio,
        executed_price=payload.executed_price,
    )
    feedback = RecommendationFeedback(
        recommendation_id=recommendation_id,
        recommendation_revision_id=revision_id,
        status=payload.status,
        status_code=status_code,
        reason=payload.reason,
        client_event_id=client_event_id,
        trade_id=resolution.trade_id,
        result=resolution.result,
        executed_quantity=resolution.executed_quantity,
        executed_ratio=resolution.executed_ratio,
        executed_price=resolution.executed_price,
        created_at=now,
        updated_at=now,
    )
    db.add(feedback)
    try:
        db.commit()
    except IntegrityError:
        # Concurrent retries with the same event id race between the initial
        # lookup and INSERT.  The unique key is authoritative: return the row
        # created by the winner when the payload is identical.
        db.rollback()
        concurrent = (
            db.query(RecommendationFeedback)
            .filter(RecommendationFeedback.client_event_id == client_event_id)
            .first()
        )
        if concurrent is not None and _feedback_event_matches(
            concurrent,
            recommendation_id=recommendation_id,
            revision_id=revision_id,
            status_code=status_code,
            payload=payload,
        ):
            return concurrent
        raise HTTPException(status_code=409, detail="client_event_id already used for different feedback")
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
        "id": row.id,
        "version": row.version,
        "previous_revision_id": row.previous_revision_id,
        "decision_hash": row.decision_hash,
        "level": row.level,
        "state": row.state,
        "action": row.action,
        "recommended_ratio": row.recommended_ratio,
        "trigger_events": json.loads(row.trigger_events_json or "[]"),
        "evidence": json.loads(row.evidence_json or "[]"),
        "counter_evidence": json.loads(row.counter_evidence_json or "[]"),
        "invalid_conditions": json.loads(row.invalid_conditions_json or "[]"),
        "recovery_conditions": json.loads(row.recovery_conditions_json or "[]"),
        "decision_context": json.loads(row.decision_context_json or "{}"),
        "rule_version": row.rule_version,
        "created_at": row.created_at.isoformat(),
        "effective_until": row.effective_until.isoformat() if row.effective_until else None,
        "is_current": row.id == recommendation.current_revision_id,
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
    rematched = rematch_execution_feedback_for_codes(db, {trade.code for trade in trades})
    if rematched:
        db.commit()
        notes.append(f"已同步重匹配 {rematched} 条执行反馈。")
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
