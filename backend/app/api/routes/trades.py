from collections import Counter
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import TradeLog, TradeReview
from app.schemas.trading import (
    TradeLogCreate,
    TradeLogUpdate,
    TradeLogOut,
    TradeReviewOut,
    GrowthProfileOut,
    ReviewCalibrationSummaryOut,
)
from app.api.helpers.quotes import _normalize_code
from app.api.helpers.holdings_calc import _account_total_asset, _rebuild_holdings_from_trades
from app.api.helpers.trade_review import (
    _trade_out,
    _trade_review_out,
    _create_pending_trade_review,
    _complete_trade_review_task,
    _review_calibration_summary,
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
