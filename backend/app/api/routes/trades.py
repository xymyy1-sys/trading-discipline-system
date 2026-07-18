from collections import Counter, defaultdict
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import (
    ActionRecommendation,
    ExpectationSnapshot,
    MarketRegimeSnapshot,
    RecommendationFeedback,
    TradeLog,
    TradeReview,
    VolumePriceSnapshot,
)
from app.schemas.trading import (
    TradeLogCreate,
    TradeLogUpdate,
    TradeLogOut,
    TradeReviewOut,
    GrowthProfileOut,
    ReviewCalibrationSummaryOut,
    EffectivenessReportOut,
    CalibrationProposalOut,
    CalibrationApplyIn,
    CalibrationRunOut,
)
from app.api.helpers.quotes import _normalize_code
from app.api.helpers.holdings_calc import _account_total_asset, _rebuild_holdings_from_trades
from app.api.helpers.trade_review import (
    _trade_out,
    _trade_review_out,
    _create_pending_trade_review,
    _complete_trade_review_task,
    _review_calibration_summary,
    _expectation_calibration_proposal,
    _apply_expectation_calibration,
    _rollback_calibration_run,
    _json_list
)

router = APIRouter()

@router.post("/trades", response_model=TradeLogOut)
def create_trade(
    payload: TradeLogCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> TradeLogOut:
    amount = payload.price * payload.quantity
    total_asset = payload.total_asset or _account_total_asset(db)
    trade = TradeLog(
        **payload.model_dump(exclude={"human_tags", "total_asset"}),
        total_asset=total_asset,
        amount=amount,
        position_ratio=amount / total_asset if total_asset else 0.0,
        stop_loss_price=round(payload.cost_price * 0.96, 2),
        human_tags=",".join(payload.human_tags),
    )
    db.add(trade)
    db.flush()
    # Import locally to avoid circular dependency
    from app.api.helpers.holdings_calc import _ensure_holding_sync_baselines, _apply_trade_to_holding
    _ensure_holding_sync_baselines(db, {_normalize_code(trade.code)})
    _apply_trade_to_holding(trade, db)
    db.commit()
    db.refresh(trade)
    review = _create_pending_trade_review(trade, db)
    db.refresh(trade)
    db.refresh(review)
    background_tasks.add_task(_complete_trade_review_task, trade.id)
    return _trade_out(trade, review)

@router.get("/trades", response_model=list[TradeLogOut])
def list_trades(db: Session = Depends(get_db)) -> list[TradeLogOut]:
    trades = db.query(TradeLog).order_by(TradeLog.traded_at.desc()).limit(100).all()
    reviews = {
        item.trade_id: item
        for item in db.query(TradeReview)
        .filter(TradeReview.trade_id.in_([trade.id for trade in trades] or [0]))
        .all()
    }
    return [_trade_out(item, reviews.get(item.id)) for item in trades]

@router.put("/trades/{trade_id}", response_model=TradeLogOut)
def update_trade(
    trade_id: int,
    payload: TradeLogUpdate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> TradeLogOut:
    trade = db.get(TradeLog, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")
    affected_codes = {_normalize_code(trade.code)}
    data = payload.model_dump(exclude_unset=True)
    human_tags = data.pop("human_tags", None)
    for key, value in data.items():
        setattr(trade, key, value)
    if human_tags is not None:
        trade.human_tags = ",".join(human_tags)
    if not trade.total_asset:
        trade.total_asset = _account_total_asset(db)
    
    # Recalculate trade
    trade.amount = trade.price * trade.quantity
    trade.position_ratio = trade.amount / trade.total_asset if trade.total_asset else 0.0
    trade.stop_loss_price = round(trade.cost_price * 0.96, 2)
    
    affected_codes.add(_normalize_code(trade.code))
    db.commit()
    _rebuild_holdings_from_trades(
        db.query(TradeLog).order_by(TradeLog.traded_at.asc(), TradeLog.id.asc()).all(),
        db,
        reset_codes=affected_codes,
    )
    db.refresh(trade)
    review = _create_pending_trade_review(trade, db)
    db.refresh(trade)
    db.refresh(review)
    background_tasks.add_task(_complete_trade_review_task, trade.id)
    return _trade_out(trade, review)

@router.delete("/trades/{trade_id}")
def delete_trade(trade_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    trade = db.get(TradeLog, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail="trade not found")
    affected_codes = {_normalize_code(trade.code)}
    db.query(TradeReview).filter(TradeReview.trade_id == trade.id).delete()
    db.delete(trade)
    db.commit()
    _rebuild_holdings_from_trades(
        db.query(TradeLog).order_by(TradeLog.traded_at.asc(), TradeLog.id.asc()).all(),
        db,
        reset_codes=affected_codes,
    )
    return {"status": "deleted"}

@router.delete("/trades")
def clear_trades(db: Session = Depends(get_db)) -> dict[str, int]:
    review_count = db.query(TradeReview).delete()
    trade_count = db.query(TradeLog).delete()
    db.commit()
    return {"deleted_trades": trade_count, "deleted_reviews": review_count}

@router.get("/trade-reviews", response_model=list[TradeReviewOut])
def list_trade_reviews(db: Session = Depends(get_db)) -> list[TradeReviewOut]:
    reviews = db.query(TradeReview).order_by(TradeReview.created_at.desc()).limit(100).all()
    return [_trade_review_out(item) for item in reviews]

@router.get("/trade-growth-profile", response_model=GrowthProfileOut)
def trade_growth_profile(db: Session = Depends(get_db)) -> GrowthProfileOut:
    trades = db.query(TradeLog).order_by(TradeLog.traded_at.desc()).limit(100).all()
    reviews = db.query(TradeReview).order_by(TradeReview.created_at.desc()).limit(100).all()
    weakness_counter: Counter[str] = Counter()
    mistake_counter: Counter[str] = Counter()
    action_counter: Counter[str] = Counter()
    scores: list[int] = []
    for review in reviews:
        weakness_counter.update(_json_list(review.weakness_tags))
        mistake_counter.update(_json_list(review.mistakes))
        action_counter.update(_json_list(review.avoid_actions))
        scores.append(review.discipline_score)
    dominant = [item for item, _ in weakness_counter.most_common(5)]
    mistakes = [item for item, _ in mistake_counter.most_common(5)]
    actions = [item for item, _ in action_counter.most_common(5)]
    focus = "暂无足够交易样本，先保证每笔交易有理由、仓位、止损和退出计划。"
    if dominant:
        focus = f"当前最需要修正：{dominant[0]}。下一笔交易先检查它是否又出现。"
    elif mistakes:
        focus = f"当前最需要修正：{mistakes[0]}"
    return GrowthProfileOut(
        trade_count=len(trades),
        review_count=len(reviews),
        dominant_weaknesses=dominant,
        frequent_mistakes=mistakes,
        current_focus=focus,
        improvement_actions=actions or ["每笔交易先写明主线、前排地位、买点、止损和退出计划。"],
        recent_scores=scores[:20],
    )

@router.get("/review-calibration/summary", response_model=ReviewCalibrationSummaryOut)
def review_calibration_summary(db: Session = Depends(get_db)) -> ReviewCalibrationSummaryOut:
    return _review_calibration_summary(db)


def _effectiveness_report(db: Session, key: str, minimum_samples: int) -> EffectivenessReportOut:
    summary = _review_calibration_summary(db)
    metric = next((item for item in summary.model_metrics if item.key == key), None)
    if metric is None:
        raise HTTPException(status_code=404, detail="effectiveness metric not found")
    suggestions = [item for item in summary.calibration_suggestions if item.sample_count == 0 or item.sample_count <= metric.sample_count]
    # These legacy metrics describe state distributions/adoption, not forward
    # market outcomes.  Keep the response shape for compatibility but never
    # advertise automatic calibration from them.
    return EffectivenessReportOut(metric=metric, suggestions=suggestions, auto_calibration_allowed=False)


@router.get("/reviews/expectation-effectiveness", response_model=EffectivenessReportOut)
def expectation_effectiveness(db: Session = Depends(get_db)) -> EffectivenessReportOut:
    return _effectiveness_report(db, "expectation_hit", 20)


@router.get("/reviews/volume-price-effectiveness", response_model=EffectivenessReportOut)
def volume_price_effectiveness(db: Session = Depends(get_db)) -> EffectivenessReportOut:
    return _effectiveness_report(db, "volume_price_risk", 20)


@router.get("/reviews/execution-effectiveness", response_model=EffectivenessReportOut)
def execution_effectiveness(db: Session = Depends(get_db)) -> EffectivenessReportOut:
    return _effectiveness_report(db, "execution_adoption", 20)


@router.get("/reviews/environment-effectiveness")
def environment_effectiveness(db: Session = Depends(get_db)) -> list[dict]:
    """按真实市场快照分层统计预期、建议采纳与不利波动。"""
    latest_grade_by_date: dict[str, tuple[object, str, str]] = {}
    for row in (
        db.query(MarketRegimeSnapshot)
        .order_by(
            MarketRegimeSnapshot.trade_date.asc(),
            MarketRegimeSnapshot.captured_at.asc(),
            MarketRegimeSnapshot.id.asc(),
        )
        .all()
    ):
        # The collector persists the authoritative trading date.  Do not infer
        # it from captured_at: historical imports and timezone-naive SQLite
        # timestamps can otherwise be assigned to the wrong session.
        label = row.regime_name or row.regime_code or "未评级"
        quality = row.data_quality or "missing"
        latest_grade_by_date[row.trade_date] = (row.captured_at, label, quality)
    quality_by_grade: dict[str, set[str]] = defaultdict(set)
    for _, label, quality in latest_grade_by_date.values():
        quality_by_grade[label].add(quality)

    buckets: dict[str, dict] = defaultdict(lambda: {
        "expectation_samples": 0, "expectation_hits": 0,
        "recommendation_samples": 0, "executed_feedback": 0,
        "feedback_samples": 0, "adverse_samples": [],
    })
    final_expectation_by_stock_day: dict[tuple[str, str], ExpectationSnapshot] = {}
    for row in (
        db.query(ExpectationSnapshot)
        .order_by(
            ExpectationSnapshot.trade_date.asc(),
            ExpectationSnapshot.code.asc(),
            ExpectationSnapshot.created_at.asc(),
            ExpectationSnapshot.id.asc(),
        )
        .all()
    ):
        final_expectation_by_stock_day[(row.trade_date, _normalize_code(row.code))] = row
    for row in final_expectation_by_stock_day.values():
        if not row.expectation_result or row.expectation_result in {"UNKNOWN", "PENDING"}:
            continue
        grade = latest_grade_by_date.get(row.trade_date, (None, "未评级", "missing"))[1]
        buckets[grade]["expectation_samples"] += 1
        if row.expectation_result in {"MATCHED", "STRONGER"}:
            buckets[grade]["expectation_hits"] += 1
    recommendations = db.query(ActionRecommendation).all()
    recommendation_by_id = {row.id: row for row in recommendations}
    for row in recommendations:
        grade = latest_grade_by_date.get(row.trade_date, (None, "未评级", "missing"))[1]
        buckets[grade]["recommendation_samples"] += 1
    for row in db.query(RecommendationFeedback).all():
        recommendation = recommendation_by_id.get(row.recommendation_id)
        if not recommendation:
            continue
        grade = latest_grade_by_date.get(recommendation.trade_date, (None, "未评级", "missing"))[1]
        buckets[grade]["feedback_samples"] += 1
        if row.status in {"已执行", "部分执行"}:
            buckets[grade]["executed_feedback"] += 1
    # high_drawdown is cumulative within a session.  Counting every minute
    # snapshot heavily overweights stocks with denser collection.  Reduce each
    # stock/session to its maximum observed adverse excursion first.
    adverse_by_stock_day: dict[tuple[str, str], float] = {}
    for row in db.query(VolumePriceSnapshot).all():
        if row.data_quality == "missing":
            continue
        key = (row.trade_date, _normalize_code(row.code))
        adverse_by_stock_day[key] = max(
            adverse_by_stock_day.get(key, 0.0),
            max(0.0, float(row.high_drawdown or 0)),
        )
    for (trade_date, _), adverse_move in adverse_by_stock_day.items():
        grade = latest_grade_by_date.get(trade_date, (None, "未评级", "missing"))[1]
        buckets[grade]["adverse_samples"].append(adverse_move)

    return [{
        "market_grade": grade,
        "expectation_samples": values["expectation_samples"],
        "expectation_hit_rate": round(values["expectation_hits"] / values["expectation_samples"] * 100, 2) if values["expectation_samples"] else 0,
        "recommendation_samples": values["recommendation_samples"],
        "execution_adoption_rate": round(values["executed_feedback"] / values["feedback_samples"] * 100, 2) if values["feedback_samples"] else 0,
        "average_adverse_move": round(sum(values["adverse_samples"]) / len(values["adverse_samples"]), 2) if values["adverse_samples"] else 0,
        "data_quality": (
            "缺少同日市场环境快照"
            if grade == "未评级"
            else "、".join(sorted(quality_by_grade.get(grade) or {"missing"}))
        ),
    } for grade, values in sorted(buckets.items())]


@router.get("/reviews/calibration-proposal", response_model=CalibrationProposalOut)
def calibration_proposal(db: Session = Depends(get_db)) -> CalibrationProposalOut:
    return _expectation_calibration_proposal(db)


@router.post("/reviews/calibration-apply", response_model=CalibrationRunOut)
def calibration_apply(payload: CalibrationApplyIn, db: Session = Depends(get_db)) -> CalibrationRunOut:
    try:
        return _apply_expectation_calibration(db, payload.confirmation)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/reviews/calibration-runs/{run_id}/rollback", response_model=CalibrationRunOut)
def calibration_rollback(run_id: int, db: Session = Depends(get_db)) -> CalibrationRunOut:
    try:
        return _rollback_calibration_run(db, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
