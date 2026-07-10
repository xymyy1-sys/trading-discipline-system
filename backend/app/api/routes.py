import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from types import SimpleNamespace
from typing import Any

import requests
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, get_db
from app.models.trading import AccountState, ExitCard, Holding, HoldingSyncBaseline, NextDayPlan, TradeLog, TradeReview
from app.schemas.trading import (
    AccountAssetIn,
    AccountAssetOut,
    ClassificationBasis,
    ExitCardCreate,
    ExitCardOut,
    GrowthProfileOut,
    HoldingCreate,
    HoldingOut,
    HoldingRefreshOut,
    HoldingSyncOut,
    HoldingUpdate,
    InformationDifferentialOut,
    LimitUpLadderOut,
    LimitUpPlanCreate,
    MarketGradeOut,
    MarketSeesawOut,
    NextDayPlanCreate,
    NextDayPlanOut,
    NextDayPlanReview,
    NextDayPlanUpdate,
    PreTradeCheckIn,
    PreTradeCheckOut,
    SectorDetailOut,
    SectorFlowOut,
    SectorRotationItem,
    SellPlanOut,
    HoldingSeesawItem,
    TradeLogCreate,
    TradeLogOut,
    TradeLogUpdate,
    TradeReviewOut,
    ThemeRadarOut,
)
from app.services.market_data import MarketDataProvider, _get_response_cache, _last_trading_day, _set_response_cache
from app.services.rules import grade_market, profit_guard_price, run_pre_trade_check

router = APIRouter(prefix="/api")
root_router = APIRouter()
market_provider = MarketDataProvider()
_QUOTE_META_CACHE: dict[str, dict[str, Any]] = {}


@root_router.get("/", response_class=HTMLResponse)
def root() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>交易纪律系统 API</title>
<style>
  body { font-family: system-ui,sans-serif; max-width:640px; margin:60px auto; padding:0 20px; color:#17231f; background:#f7f7f1; }
  h1 { font-size:24px; }
  a { color:#4472ca; }
  code { background:#e5e8df; padding:2px 6px; border-radius:4px; font-size:13px; }
  ul { line-height:2; }
</style>
</head>
<body>
<h1>📊 交易纪律系统 API</h1>
<p>后端运行中。前端请访问 <a href="http://1.12.222.27:5173">http://1.12.222.27:5173</a></p>
<h2>接口列表</h2>
<ul>
  <li><code>GET /api/health</code></li>
  <li><code>GET /api/market/sector-flow</code></li>
  <li><code>GET /api/market/sector-detail</code></li>
  <li><code>GET /api/market/limit-up-ladder</code></li>
  <li><code>GET /api/market/theme-radar</code></li>
  <li><code>GET /api/market/grade</code></li>
  <li><code>GET /api/intel/daily</code></li>
  <li><code>POST /api/checks/pre-trade</code></li>
  <li><code>GET /api/holdings</code> · <code>POST /api/holdings</code></li>
  <li><code>GET /api/next-day-plans</code> · <code>POST /api/next-day-plans/generate</code></li>
  <li><code>GET /api/trades</code> · <code>POST /api/trades</code></li>
  <li><code>GET /api/exit-cards</code> · <code>POST /api/exit-cards</code></li>
  <li><code>GET /api/sell-plans</code></li>
</ul>
</body>
</html>"""


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/market/sector-flow", response_model=SectorFlowOut)
def sector_flow(
    flow_type: str = "行业资金流",
    period: str = "今日",
    force_refresh: bool = False,
) -> SectorFlowOut:
    return market_provider.sector_flow(
        flow_type=flow_type,
        period=period,
        force_refresh=force_refresh,
    )


@router.get("/market/sector-detail", response_model=SectorDetailOut)
def sector_detail(
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
def limit_up_ladder(
    trade_date: str | None = None,
    force_refresh: bool = False,
) -> LimitUpLadderOut:
    return market_provider.limit_up_ladder(
        trade_date=trade_date,
        force_refresh=force_refresh,
    )


@router.get("/market/theme-radar", response_model=ThemeRadarOut)
def theme_radar(force_refresh: bool = False) -> ThemeRadarOut:
    return market_provider.theme_radar(force_refresh=force_refresh)


@router.get("/intel/daily", response_model=InformationDifferentialOut)
def information_differential(date: str | None = None) -> InformationDifferentialOut:
    return market_provider.information_differential(date=date)


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
def market_seesaw_monitor(
    force_refresh: bool = False,
    db: Session = Depends(get_db),
) -> MarketSeesawOut:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return _market_seesaw_monitor(holdings, force_refresh=force_refresh)


@router.post("/checks/pre-trade", response_model=PreTradeCheckOut)
def pre_trade_check(payload: PreTradeCheckIn) -> PreTradeCheckOut:
    return run_pre_trade_check(payload)


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
    state.total_asset = max(0, payload.total_asset)
    db.add(state)
    db.commit()
    db.refresh(state)
    return AccountAssetOut(total_asset=state.total_asset, updated_at=state.updated_at)


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


@router.get("/next-day-plans", response_model=list[NextDayPlanOut])
def list_next_day_plans(
    plan_date: str | None = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
) -> list[NextDayPlanOut]:
    plan_date = plan_date or _next_trade_date()
    query = db.query(NextDayPlan)
    query = query.filter(NextDayPlan.plan_date == plan_date)
    plans = query.order_by(NextDayPlan.risk_priority.asc(), NextDayPlan.updated_at.desc()).all()
    price_notes = _refresh_existing_holding_plans(plans, db) if refresh else {}
    return [_next_day_plan_out(item, price_note=price_notes.get(item.code, "")) for item in plans]


@router.post("/next-day-plans", response_model=NextDayPlanOut)
def create_next_day_plan(
    payload: NextDayPlanCreate,
    db: Session = Depends(get_db),
) -> NextDayPlanOut:
    plan = _plan_from_payload(payload)
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return _next_day_plan_out(plan)


@router.post("/next-day-plans/generate", response_model=list[NextDayPlanOut])
def generate_next_day_plans(db: Session = Depends(get_db)) -> list[NextDayPlanOut]:
    plan_date = _next_trade_date()
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    account_total_asset = _account_total_asset(db)
    price_notes = _refresh_holding_prices(holdings, db)
    plans: list[NextDayPlan] = []
    seen_keys: set[tuple[str, str, str]] = set()
    for holding in holdings:
        key = (plan_date, holding.code, "holding")
        existing_plans = (
            db.query(NextDayPlan)
            .filter(
                NextDayPlan.plan_date == plan_date,
                NextDayPlan.code == holding.code,
                NextDayPlan.plan_type == "holding",
            )
            .order_by(NextDayPlan.updated_at.desc())
            .all()
        )
        existing = existing_plans[0] if existing_plans else None
        for duplicate in existing_plans[1:]:
            db.delete(duplicate)
        quote = _latest_quote_for_holding(holding)
        plan = _default_next_day_plan(holding, plan_date, account_total_asset, quote)
        if existing:
            _sync_holding_plan(existing, plan)
            plans.append(existing)
        elif key not in seen_keys:
            db.add(plan)
            plans.append(plan)
        seen_keys.add(key)
    db.commit()
    for plan in plans:
        db.refresh(plan)
    return [
        _next_day_plan_out(item, price_note=price_notes.get(item.code, ""))
        for item in sorted(plans, key=lambda item: item.risk_priority)
    ]


@router.post("/next-day-plans/from-limit-up", response_model=NextDayPlanOut)
def create_limit_up_plan(
    payload: LimitUpPlanCreate,
    db: Session = Depends(get_db),
) -> NextDayPlanOut:
    plan_date = _next_trade_date()
    existing = (
        db.query(NextDayPlan)
        .filter(
            NextDayPlan.plan_date == plan_date,
            NextDayPlan.code == payload.code,
            NextDayPlan.plan_type == "limit_up_auction",
        )
        .first()
    )
    plan = _limit_up_next_day_plan(payload, plan_date, existing)
    if existing is None:
        db.add(plan)
    db.commit()
    db.refresh(plan)
    return _next_day_plan_out(plan)


@router.put("/next-day-plans/{plan_id}", response_model=NextDayPlanOut)
def update_next_day_plan(
    plan_id: int,
    payload: NextDayPlanUpdate,
    db: Session = Depends(get_db),
) -> NextDayPlanOut:
    plan = db.get(NextDayPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="next day plan not found")
    data = payload.model_dump(exclude_unset=True)
    if "classification_basis" in data and data["classification_basis"] is not None:
        plan.classification_basis = json.dumps(data.pop("classification_basis"), ensure_ascii=False)
    if "auction_plan" in data and data["auction_plan"] is not None:
        plan.auction_plan = json.dumps(data.pop("auction_plan"), ensure_ascii=False)
    if "forbidden_actions" in data and data["forbidden_actions"] is not None:
        plan.forbidden_actions = json.dumps(data.pop("forbidden_actions"), ensure_ascii=False)
    for key, value in data.items():
        setattr(plan, key, value)
    _refresh_plan_risk(plan)
    db.commit()
    db.refresh(plan)
    return _next_day_plan_out(plan)


@router.delete("/next-day-plans/{plan_id}")
def delete_next_day_plan(plan_id: int, db: Session = Depends(get_db)) -> dict[str, str]:
    plan = db.get(NextDayPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="next day plan not found")
    db.delete(plan)
    db.commit()
    return {"status": "deleted"}


@router.post("/next-day-plans/{plan_id}/review", response_model=NextDayPlanOut)
def review_next_day_plan(
    plan_id: int,
    payload: NextDayPlanReview,
    db: Session = Depends(get_db),
) -> NextDayPlanOut:
    plan = db.get(NextDayPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="next day plan not found")
    plan.review_expectation = payload.review_expectation
    plan.review_execution = payload.review_execution
    plan.review_deviation = payload.review_deviation
    _refresh_plan_risk(plan)
    db.commit()
    db.refresh(plan)
    return _next_day_plan_out(plan)


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
        position_ratio=amount / total_asset if total_asset else 0,
        stop_loss_price=round(payload.cost_price * 0.96, 2),
        human_tags=",".join(payload.human_tags),
    )
    db.add(trade)
    db.flush()
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
    _recalculate_trade(trade)
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


@router.post("/exit-cards", response_model=ExitCardOut)
def create_exit_card(payload: ExitCardCreate, db: Session = Depends(get_db)) -> ExitCardOut:
    card = ExitCard(**payload.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@router.get("/exit-cards", response_model=list[ExitCardOut])
def list_exit_cards(db: Session = Depends(get_db)) -> list[ExitCardOut]:
    cards = db.query(ExitCard).order_by(ExitCard.created_at.desc()).limit(50).all()
    return cards


@router.get("/sell-plans", response_model=list[SellPlanOut])
def sell_plans(db: Session = Depends(get_db)) -> list[SellPlanOut]:
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    return [_sell_plan(item) for item in holdings]


def _normalize_code(code: str) -> str:
    raw = str(code or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw.zfill(6)
    if len(digits) <= 6:
        return digits.zfill(6)
    return digits


def _trade_position_direction(side: str) -> int:
    text = str(side or "")
    if text in {"买入", "加仓"} or "买" in text or "加仓" in text:
        return 1
    if text in {"卖出", "减仓"} or "卖" in text or "减仓" in text:
        return -1
    return 0


def _trade_effect_data(trade: TradeLog) -> dict[str, Any]:
    return {
        "code": trade.code,
        "name": trade.name,
        "side": trade.side,
        "price": trade.price,
        "quantity": trade.quantity,
        "amount": trade.amount,
        "total_asset": trade.total_asset,
        "cost_price": trade.cost_price,
    }


def _find_holding_by_code(db: Session, code: str) -> Holding | None:
    normalized = _normalize_code(code)
    candidates = [normalized]
    stripped = normalized.lstrip("0")
    if stripped:
        candidates.append(stripped)
    return db.query(Holding).filter(Holding.code.in_(candidates)).first()


def _find_holding_sync_baseline(db: Session, code: str) -> HoldingSyncBaseline | None:
    normalized = _normalize_code(code)
    candidates = [normalized]
    stripped = normalized.lstrip("0")
    if stripped:
        candidates.append(stripped)
    return db.query(HoldingSyncBaseline).filter(HoldingSyncBaseline.code.in_(candidates)).first()


def _ensure_holding_sync_baselines(db: Session, codes: set[str]) -> None:
    for code in {_normalize_code(item) for item in codes if item}:
        if _find_holding_sync_baseline(db, code):
            continue
        holding = _find_holding_by_code(db, code)
        baseline = HoldingSyncBaseline(
            code=holding.code if holding else code,
            name=holding.name if holding else code,
            quantity=max(0, int(holding.quantity or 0)) if holding else 0,
            cost_price=float(holding.cost_price or 0) if holding else 0,
            current_price=float(holding.current_price or 0) if holding else 0,
            total_asset=float(holding.total_asset or _account_total_asset(db)) if holding else _account_total_asset(db),
            position_type=holding.position_type if holding else "交易同步基线仓",
            next_discipline=holding.next_discipline if holding else "",
        )
        db.add(baseline)
    db.flush()


def _restore_holding_from_baseline(baseline: HoldingSyncBaseline, db: Session) -> None:
    if int(baseline.quantity or 0) <= 0:
        return
    db.add(
        Holding(
            code=baseline.code,
            name=baseline.name,
            quantity=int(baseline.quantity or 0),
            cost_price=float(baseline.cost_price or 0),
            current_price=float(baseline.current_price or baseline.cost_price or 0),
            total_asset=float(baseline.total_asset or _account_total_asset(db)),
            position_type=baseline.position_type or "交易同步基线仓",
            next_discipline=baseline.next_discipline or "以首次同步前持仓为基线，叠加交易记录重算。",
        )
    )


def _apply_trade_to_holding(
    trade: TradeLog,
    db: Session,
    reverse: bool = False,
) -> None:
    _apply_trade_effect_to_holding(_trade_effect_data(trade), db, reverse=reverse)


def _apply_trade_effect_to_holding(
    data: dict[str, Any],
    db: Session,
    reverse: bool = False,
) -> None:
    direction = _trade_position_direction(str(data.get("side") or ""))
    if direction == 0:
        return
    if reverse:
        direction *= -1
    quantity = int(data.get("quantity") or 0)
    price = float(data.get("price") or 0)
    if quantity <= 0 or price <= 0:
        return
    amount = float(data.get("amount") or 0)
    cost_basis_price = amount / quantity if amount > 0 else price

    code = str(data.get("code") or "")
    name = str(data.get("name") or code)
    total_asset = float(data.get("total_asset") or _account_total_asset(db))
    holding = _find_holding_by_code(db, code)
    qty_delta = direction * quantity

    if qty_delta > 0:
        if holding is None:
            holding = Holding(
                code=code,
                name=name,
                quantity=qty_delta,
                cost_price=cost_basis_price,
                current_price=price,
                total_asset=total_asset,
                position_type="交易记录同步仓",
                next_discipline="由交易记录自动同步，刷新行情后重算盈亏。",
            )
            db.add(holding)
            db.flush()
            return
        old_qty = max(0, int(holding.quantity or 0))
        old_cost = float(holding.cost_price or cost_basis_price)
        new_qty = old_qty + qty_delta
        holding.quantity = new_qty
        holding.cost_price = round(((old_qty * old_cost) + (qty_delta * cost_basis_price)) / new_qty, 4)
        holding.current_price = price if not holding.current_price else holding.current_price
        holding.name = holding.name or name
        holding.total_asset = total_asset or holding.total_asset
        return

    if holding is None:
        return
    sell_qty = abs(qty_delta)
    old_qty = int(holding.quantity or 0)
    new_qty = old_qty - sell_qty
    if new_qty <= 0:
        db.delete(holding)
        return

    # Reversing a prior buy can recover the previous weighted cost; normal sells keep cost basis unchanged.
    if reverse and _trade_position_direction(str(data.get("side") or "")) > 0:
        remaining_cost_amount = (old_qty * float(holding.cost_price or 0)) - (sell_qty * cost_basis_price)
        holding.cost_price = round(max(0, remaining_cost_amount) / new_qty, 4) if new_qty else 0
    holding.quantity = new_qty
    holding.current_price = price if not holding.current_price else holding.current_price
    holding.total_asset = total_asset or holding.total_asset


def _rebuild_holdings_from_trades(
    trades: list[TradeLog],
    db: Session,
    reset_codes: set[str] | None = None,
) -> list[str]:
    trade_codes = {_normalize_code(trade.code) for trade in trades if _trade_position_direction(trade.side)}
    if reset_codes:
        trade_codes.update(_normalize_code(code) for code in reset_codes if code)
    if not trade_codes:
        return ["没有可同步的买入/卖出/加仓/减仓交易记录。"]
    _ensure_holding_sync_baselines(db, trade_codes)
    lookup_codes = list(trade_codes | {code.lstrip("0") for code in trade_codes if code.lstrip("0")})
    for holding in db.query(Holding).filter(Holding.code.in_(lookup_codes)).all():
        db.delete(holding)
    db.flush()
    baselines = [
        item
        for item in db.query(HoldingSyncBaseline).filter(HoldingSyncBaseline.code.in_(lookup_codes)).all()
        if _normalize_code(item.code) in trade_codes
    ]
    for baseline in baselines:
        _restore_holding_from_baseline(baseline, db)
    db.flush()
    for trade in trades:
        if _normalize_code(trade.code) in trade_codes:
            _apply_trade_to_holding(trade, db)
    db.commit()
    baseline_count = sum(1 for item in baselines if int(item.quantity or 0) > 0)
    return [
        f"已按 {len(trades)} 条交易记录重算持仓；{baseline_count} 只股票保留首次同步前基线，买入/加仓叠加原仓，卖出/减仓扣减数量。"
    ]


def _account_state(db: Session) -> AccountState:
    state = db.get(AccountState, 1)
    if state is None:
        inferred_total_asset = (
            db.query(Holding.total_asset)
            .filter(Holding.total_asset > 0)
            .order_by(Holding.updated_at.desc())
            .limit(1)
            .scalar()
        )
        state = AccountState(id=1, total_asset=float(inferred_total_asset or 0))
        db.add(state)
        db.commit()
        db.refresh(state)
    return state


def _account_total_asset(db: Session) -> float:
    return _account_state(db).total_asset


def _refresh_holding_prices(holdings: list[Holding], db: Session) -> dict[str, str]:
    codes = [holding.code for holding in holdings if holding.code]
    if not codes:
        return {}
    try:
        quotes = _latest_a_share_quotes(codes)
    except Exception:
        return {code: "实时行情获取失败，暂用手动录入价。" for code in codes}

    notes: dict[str, str] = {}
    updated = False
    for holding in holdings:
        lookup_code = _quote_lookup_code(holding.code, quotes)
        quote = quotes.get(lookup_code)
        price = float(quote.get("price") or 0) if quote else 0
        if price > 0:
            if abs(price - holding.current_price) >= 0.001:
                holding.current_price = round(price, 2)
                updated = True
            normalized_note = str(quote.get("note") or "实时行情")
            if _normalize_code(holding.code) != lookup_code:
                normalized_note = f"{normalized_note}；原代码{holding.code}按{lookup_code}匹配"
            notes[holding.code] = normalized_note
            _QUOTE_META_CACHE[str(holding.code)] = quote
            _QUOTE_META_CACHE[lookup_code] = quote
        else:
            notes[holding.code] = f"未匹配到实时行情，暂用手动录入价。{_code_hint(holding.code)}"
    if updated:
        db.commit()
        for holding in holdings:
            db.refresh(holding)
    return notes


def _latest_a_share_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    try:
        import akshare as ak

        frame = ak.stock_zh_a_spot_em()
        if frame.empty:
            raise ValueError("empty spot quote")
        normalized = set()
        for code in codes:
            normalized.update(_quote_code_candidates(code))
        quotes: dict[str, dict[str, Any]] = {}
        for _, row in frame.iterrows():
            code = str(row.get("代码") or row.get("code") or "").zfill(6)
            if code not in normalized:
                continue
            price = _safe_float(row.get("最新价") or row.get("price"))
            if price <= 0:
                continue
            open_price = _safe_float(row.get("今开") or row.get("open"))
            prev_close = _safe_float(row.get("昨收") or row.get("prev_close"))
            high_price = _safe_float(row.get("最高") or row.get("high"))
            low_price = _safe_float(row.get("最低") or row.get("low"))
            quotes[code] = {
                "price": price,
                "change_pct": _safe_float(row.get("涨跌幅") or row.get("change_pct")),
                "amount": round(_safe_float(row.get("成交额") or row.get("amount")) / 1e8, 2),
                "turnover": _safe_turnover(row.get("换手率") or row.get("turnover")),
                "open": open_price,
                "prev_close": prev_close,
                "high": high_price,
                "low": low_price,
                "note": "AkShare/东方财富实时行情",
            }
        if quotes:
            return quotes
    except Exception:
        pass
    try:
        quotes = _latest_a_share_quotes_sina(codes)
        if quotes:
            return quotes
    except Exception:
        pass
    return _latest_a_share_quotes_eastmoney(codes)


def _latest_a_share_quotes_sina(codes: list[str]) -> dict[str, dict[str, Any]]:
    symbols = []
    code_by_symbol: dict[str, str] = {}
    for code in codes:
        for candidate in _quote_code_candidates(code):
            prefix = "sh" if candidate.startswith(("5", "6", "9")) else "sz"
            symbol = f"{prefix}{candidate}"
            symbols.append(symbol)
            code_by_symbol[symbol] = candidate
    if not symbols:
        return {}
    url = "https://hq.sinajs.cn/list=" + ",".join(dict.fromkeys(symbols))
    resp = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        timeout=8,
    )
    resp.raise_for_status()
    text = resp.content.decode("gbk", errors="ignore")
    quotes: dict[str, dict[str, Any]] = {}
    for symbol, payload in re.findall(r'var hq_str_(s[hz]\d{6})="([^"]*)"', text):
        parts = payload.split(",")
        if len(parts) < 32 or not parts[0]:
            continue
        code = code_by_symbol.get(symbol, symbol[-6:])
        open_price = _safe_float(parts[1])
        prev_close = _safe_float(parts[2])
        price = _safe_float(parts[3])
        high_price = _safe_float(parts[4])
        low_price = _safe_float(parts[5])
        volume = _safe_float(parts[8])
        amount = _safe_float(parts[9])
        if price <= 0:
            continue
        change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0
        quotes[code] = {
            "price": price,
            "change_pct": change_pct,
            "amount": round(amount / 1e8, 2),
            "turnover": 0,
            "open": open_price,
            "prev_close": prev_close,
            "high": high_price,
            "low": low_price,
            "volume": volume,
            "note": "新浪实时行情",
        }
    return quotes


def _latest_a_share_quotes_eastmoney(codes: list[str]) -> dict[str, dict[str, Any]]:
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    secids = ",".join(
        _eastmoney_secid(candidate)
        for code in codes
        for candidate in _quote_code_candidates(code)
        if candidate
    )
    if not secids:
        return {}
    params = urlencode({
        "fltt": "2",
        "invt": "2",
        "fields": "f12,f14,f2,f3,f6,f8,f15,f16,f17,f18",
        "secids": secids,
    })
    url = f"https://push2.eastmoney.com/api/qt/ulist.np/get?{params}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=6) as response:
        payload = json.loads(response.read().decode("utf-8"))
    rows = payload.get("data", {}).get("diff", []) or []
    quotes: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        price = _safe_float(row.get("f2"))
        if not code or price <= 0:
            continue
        quotes[code] = {
            "price": price,
            "change_pct": _safe_float(row.get("f3")),
            "amount": round(_safe_float(row.get("f6")) / 1e8, 2),
            "turnover": _safe_turnover(row.get("f8")),
            "open": _safe_float(row.get("f17")),
            "prev_close": _safe_float(row.get("f18")),
            "high": _safe_float(row.get("f15")),
            "low": _safe_float(row.get("f16")),
            "note": "东方财富实时行情",
        }
    return quotes


def _eastmoney_secid(code: str) -> str:
    normalized = _quote_code_candidates(code)[0] if _quote_code_candidates(code) else _normalize_code(code)
    market = "1" if normalized.startswith(("6", "9")) else "0"
    return f"{market}.{normalized}"


def _quote_code_candidates(code: str) -> list[str]:
    raw = str(code or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    candidates: list[str] = []
    if len(digits) == 6:
        candidates.append(digits)
    elif len(digits) < 6 and digits:
        candidates.append(digits.zfill(6))
    elif len(digits) > 6:
        candidates.extend([digits[:6], digits[-6:]])
        if digits.startswith("5888") and len(digits) == 7:
            candidates.append(digits[:3] + digits[4:])
    return list(dict.fromkeys(item for item in candidates if len(item) == 6))


def _quote_lookup_code(code: str, quotes: dict[str, dict[str, Any]]) -> str:
    for candidate in _quote_code_candidates(code):
        if candidate in quotes:
            return candidate
    candidates = _quote_code_candidates(code)
    return candidates[0] if candidates else _normalize_code(code)


def _code_hint(code: str) -> str:
    normalized = _normalize_code(code)
    if len(normalized) != 6:
        candidates = "、".join(_quote_code_candidates(code)) or "无"
        return f" 代码长度异常，候选匹配：{candidates}。"
    return ""


def _latest_quote_for_holding(holding: Holding) -> dict[str, Any]:
    try:
        quotes = _latest_a_share_quotes([holding.code])
        return quotes.get(_quote_lookup_code(holding.code, quotes), {})
    except Exception:
        return {}


def _refresh_existing_holding_plans(
    plans: list[NextDayPlan],
    db: Session,
) -> dict[str, str]:
    holding_plans = [plan for plan in plans if plan.plan_type == "holding"]
    if not holding_plans:
        return {}
    codes = {str(plan.code).zfill(6) for plan in holding_plans}
    lookup_codes = set(codes | {code.lstrip("0") for code in codes if code.lstrip("0")})
    for code in codes:
        lookup_codes.update(_quote_code_candidates(code))
    holdings = db.query(Holding).filter(Holding.code.in_(lookup_codes)).all()
    holdings_by_code: dict[str, Holding] = {}
    for holding in holdings:
        holdings_by_code[str(holding.code).zfill(6)] = holding
        for candidate in _quote_code_candidates(holding.code):
            holdings_by_code[candidate] = holding
    account_total_asset = _account_total_asset(db)
    price_notes = _refresh_holding_prices(holdings, db)
    changed = False
    for plan in holding_plans:
        holding = holdings_by_code.get(str(plan.code).zfill(6))
        if holding is None:
            for candidate in _quote_code_candidates(plan.code):
                holding = holdings_by_code.get(candidate)
                if holding is not None:
                    break
        if holding is None:
            continue
        fresh = _default_next_day_plan(
            holding,
            plan.plan_date,
            account_total_asset,
            _latest_quote_for_holding(holding),
        )
        _sync_holding_plan(plan, fresh)
        changed = True
    if changed:
        db.commit()
        for plan in plans:
            db.refresh(plan)
    return price_notes


def _holding_out(
    holding: Holding,
    account_total_asset: float | None = None,
    price_note: str = "",
) -> HoldingOut:
    total_asset = account_total_asset if account_total_asset is not None else holding.total_asset
    market_value = holding.quantity * holding.current_price
    profit_amount = (holding.current_price - holding.cost_price) * holding.quantity
    profit_ratio = (
        (holding.current_price - holding.cost_price) / holding.cost_price
        if holding.cost_price
        else 0
    )
    position_ratio = market_value / total_asset if total_asset else 0
    data = holding.__dict__.copy()
    data.pop("_sa_instance_state", None)
    data["total_asset"] = total_asset
    is_realtime = _is_realtime_note(price_note)
    quote_meta = _QUOTE_META_CACHE.get(str(holding.code)) or _QUOTE_META_CACHE.get(_quote_lookup_code(holding.code, _QUOTE_META_CACHE))
    prev_close = _safe_float((quote_meta or {}).get("prev_close"))
    today_profit_amount = (holding.current_price - prev_close) * holding.quantity if prev_close else 0
    today_profit_ratio = (holding.current_price - prev_close) / prev_close if prev_close else 0
    return HoldingOut(
        **data,
        market_value=round(market_value, 2),
        profit_amount=round(profit_amount, 2),
        profit_ratio=round(profit_ratio, 4),
        today_profit_amount=round(today_profit_amount, 2),
        today_profit_ratio=round(today_profit_ratio, 4),
        position_ratio=round(position_ratio, 4),
        stop_loss_price=round(holding.cost_price * 0.96, 2),
        profit_guard_price=profit_guard_price(holding.cost_price, holding.current_price),
        price_source="realtime" if is_realtime else "manual",
        price_note=price_note,
        prev_close=round(prev_close, 2),
        change_pct=round(_safe_float((quote_meta or {}).get("change_pct")), 2),
        amount=round(_safe_float((quote_meta or {}).get("amount")), 2),
        turnover=round(_safe_float((quote_meta or {}).get("turnover")), 2),
        open_price=round(_safe_float((quote_meta or {}).get("open")), 2),
        high_price=round(_safe_float((quote_meta or {}).get("high")), 2),
        low_price=round(_safe_float((quote_meta or {}).get("low")), 2),
    )


def _holding_account_summary(holdings: list[HoldingOut], total_asset: float) -> dict[str, float]:
    total_market_value = round(sum(item.market_value for item in holdings), 2)
    today_profit_amount = round(sum(item.today_profit_amount for item in holdings), 2)
    total_profit_amount = round(sum(item.profit_amount for item in holdings), 2)
    total_position_ratio = total_market_value / total_asset if total_asset else 0
    today_profit_ratio = today_profit_amount / total_asset if total_asset else 0
    total_profit_ratio = total_profit_amount / total_asset if total_asset else 0
    return {
        "total_asset": round(total_asset, 2),
        "cash_available": round(total_asset - total_market_value, 2) if total_asset else 0,
        "total_market_value": total_market_value,
        "total_position_ratio": round(total_position_ratio, 4),
        "today_profit_amount": today_profit_amount,
        "today_profit_ratio": round(today_profit_ratio, 4),
        "total_profit_amount": total_profit_amount,
        "total_profit_ratio": round(total_profit_ratio, 4),
    }


def _is_realtime_note(price_note: str) -> bool:
    note = str(price_note or "")
    if not note:
        return False
    failure_words = ("失败", "未匹配", "暂用", "手动", "缓存", "数据缺口", "异常")
    if any(word in note for word in failure_words):
        return False
    return "实时行情" in note or "东方财富" in note or "AkShare" in note or "新浪" in note or "腾讯" in note


def _trade_out(trade: TradeLog, review: TradeReview | None = None) -> TradeLogOut:
    data = trade.__dict__.copy()
    data.pop("_sa_instance_state", None)
    raw_tags = str(data.pop("human_tags", "") or "")
    return TradeLogOut(
        **data,
        human_tags=[tag for tag in raw_tags.split(",") if tag],
        review=_trade_review_out(review) if review else None,
    )


def _trade_review_out(review: TradeReview) -> TradeReviewOut:
    return TradeReviewOut(
        id=review.id,
        trade_id=review.trade_id,
        code=review.code,
        name=review.name,
        verdict=review.verdict,
        status=review.status,
        discipline_score=review.discipline_score,
        summary=review.summary,
        stock_context=review.stock_context,
        sector_context=review.sector_context,
        market_context=review.market_context,
        error_message=review.error_message,
        mistakes=_json_list(review.mistakes),
        avoid_actions=_json_list(review.avoid_actions),
        weakness_tags=_json_list(review.weakness_tags),
        created_at=review.created_at,
    )


def _recalculate_trade(trade: TradeLog) -> None:
    trade.amount = trade.price * trade.quantity
    trade.position_ratio = trade.amount / trade.total_asset if trade.total_asset else 0
    trade.stop_loss_price = round(trade.cost_price * 0.96, 2)


def _create_pending_trade_review(trade: TradeLog, db: Session) -> TradeReview:
    db.query(TradeReview).filter(TradeReview.trade_id == trade.id).delete()
    review = TradeReview(
        trade_id=trade.id,
        code=trade.code,
        name=trade.name,
        verdict="深度复盘生成中",
        status="pending",
        discipline_score=0,
        summary="已保存交易记录，正在异步结合大盘、板块、个股分时/行情和交易理由生成深度复盘。",
        stock_context="生成中",
        sector_context="生成中",
        market_context="生成中",
        mistakes=json.dumps(["深度复盘尚未完成。"], ensure_ascii=False),
        avoid_actions=json.dumps(["稍后刷新交易日志查看完整复盘。"], ensure_ascii=False),
        weakness_tags=json.dumps([tag for tag in (trade.human_tags or "").split(",") if tag], ensure_ascii=False),
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


def _complete_trade_review_task(trade_id: int) -> None:
    db = SessionLocal()
    try:
        trade = db.get(TradeLog, trade_id)
        if trade is None:
            return
        generated = _generate_trade_review(trade, db)
        review = (
            db.query(TradeReview)
            .filter(TradeReview.trade_id == trade.id)
            .order_by(TradeReview.created_at.desc())
            .first()
        )
        if review is None:
            db.add(generated)
        else:
            _copy_review_fields(review, generated)
        db.commit()
    except Exception as exc:
        db.rollback()
        review = (
            db.query(TradeReview)
            .filter(TradeReview.trade_id == trade_id)
            .order_by(TradeReview.created_at.desc())
            .first()
        )
        if review is not None:
            review.status = "failed"
            review.verdict = "数据缺口"
            review.error_message = str(exc)
            review.summary = "深度复盘生成失败，已保留交易记录；请稍后刷新行情缓存后重新编辑保存触发复盘。"
            db.commit()
    finally:
        db.close()


def _copy_review_fields(target: TradeReview, source: TradeReview) -> None:
    for field in (
        "code",
        "name",
        "verdict",
        "status",
        "discipline_score",
        "summary",
        "stock_context",
        "sector_context",
        "market_context",
        "error_message",
        "mistakes",
        "avoid_actions",
        "weakness_tags",
    ):
        setattr(target, field, getattr(source, field))


def _generate_trade_review(trade: TradeLog, db: Session) -> TradeReview:
    market_context, sector_context, stock_context = _trade_market_context(trade)
    side = trade.side
    reason = trade.reason or ""
    human_tags = [tag for tag in (trade.human_tags or "").split(",") if tag]
    reason_text = f"{reason} {trade.name} {trade.code}"
    mistakes: list[str] = []
    avoid_actions: list[str] = []
    weakness_tags: list[str] = list(dict.fromkeys(human_tags))

    if not trade.compliant:
        mistakes.append("主动标记为违反体系，说明这笔交易已经存在纪律偏离。")
        avoid_actions.append("下一笔交易先写清体系依据，不符合主线/前排/风控时不下单。")
    if not reason.strip():
        mistakes.append("交易理由为空，属于无计划交易。")
        avoid_actions.append("下单前必须写明主线、买点、止损和退出条件。")
        weakness_tags.append("冲动")
    if side in {"买入", "加仓", "做T"}:
        if not _contains_positive_any(reason_text, ("主线", "热点", "板块", "题材", "龙头", "前排", "共振")):
            mistakes.append("买入理由没有说明主线/热点/前排地位。")
            avoid_actions.append("买入前必须回答：它为什么是当前主线里的龙一、龙二或明确前排。")
        if not _contains_positive_any(reason_text, ("止损", "风险", "撤单", "减仓", "退出", "确认位", "失效")):
            mistakes.append("买入理由缺少失效条件或止损/退出计划。")
            avoid_actions.append("每笔买入同时写明失效点、4%止损参考和弱于预期动作。")
        if trade.position_ratio > 0.4 and "集中" not in trade.mode:
            mistakes.append("标准短线模式下单票仓位超过40%上限。")
            avoid_actions.append("标准短线单票不超过40%，非龙一龙二要自动降档。")
            weakness_tags.append("贪婪")
        elif trade.position_ratio > 0.3 and not _contains_any(reason_text, ("龙一", "龙二", "前排", "核心")):
            mistakes.append("仓位偏高，但理由没有证明龙头/前排地位。")
            avoid_actions.append("没有前排证明时，首仓控制在观察仓或补涨仓级别。")
            weakness_tags.append("冲动追高")
    if side in {"卖出", "减仓"}:
        if not _contains_any(reason_text, ("止盈", "止损", "弱于预期", "破位", "退潮", "计划", "纪律")):
            mistakes.append("卖出理由没有绑定计划，容易变成情绪化卖出。")
            avoid_actions.append("卖出前区分：止盈、止损、弱于预期、板块退潮，不能只凭感觉。")
            weakness_tags.append("恐惧")

    if "未在资金流/题材雷达/涨停天梯中找到明确支持" in sector_context and side in {"买入", "加仓", "做T"}:
        mistakes.append("当前系统证据未确认板块共振，买入证据不足。")
        avoid_actions.append("没有板块资金或涨停天梯支撑时，默认降低仓位或只观察。")
    if not mistakes:
        mistakes.append("未发现明显纪律错误，但仍需盘后核对实际走势是否符合预期。")
        avoid_actions.append("保留本次计划模板，盘后复盘是否按计划执行。")

    weakness_tags = _dedupe([tag for tag in weakness_tags if tag] or _infer_weakness_tags(mistakes))
    score = _discipline_score(trade, mistakes)
    verdict = "体系内" if score >= 80 else "存疑" if score >= 60 else "明显偏离"
    summary = _trade_review_summary(trade, verdict, mistakes, avoid_actions)
    return TradeReview(
        trade_id=trade.id,
        code=trade.code,
        name=trade.name,
        verdict=verdict,
        status="done",
        discipline_score=score,
        summary=summary,
        stock_context=stock_context,
        sector_context=sector_context,
        market_context=market_context,
        error_message="",
        mistakes=json.dumps(mistakes, ensure_ascii=False),
        avoid_actions=json.dumps(_dedupe(avoid_actions), ensure_ascii=False),
        weakness_tags=json.dumps(weakness_tags, ensure_ascii=False),
    )


def _trade_market_context(trade: TradeLog) -> tuple[str, str, str]:
    market_context = "行情数据暂不可用，先按交易理由和纪律规则复盘。"
    sector_context = "未在资金流/题材雷达/涨停天梯中找到明确支持。"
    stock_context = f"{trade.name} {trade.code}：价格{trade.price:.2f}，金额{trade.amount:.2f}，仓位{trade.position_ratio * 100:.1f}%。"
    radar = _get_response_cache("theme-radar")
    if radar is not None:
        strongest = radar.strongest_theme.name if radar.strongest_theme else "暂无"
        market_context = f"市场温度：{radar.market_temperature}；最强题材：{strongest}。"
        for theme in radar.themes[:12]:
            text = f"{theme.name}{''.join(theme.leader_names)}{''.join(theme.related_boards)}"
            if trade.name in text or trade.code in text or _contains_any(trade.reason, tuple(theme.related_boards + [theme.name])):
                sector_context = (
                    f"题材匹配：{theme.name}，阶段：{theme.stage}，评分{theme.score}；"
                    f"核心股：{'、'.join(theme.leader_names[:4]) or '待确认'}。"
                )
                break
    else:
        market_context = "题材雷达暂无缓存：本次保存不阻塞外部行情，盘后刷新题材雷达后可重新评估。"
    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for group in ladder.groups:
            for stock in group.stocks:
                if stock.code == trade.code or stock.name == trade.name:
                    stock_context = (
                        f"{trade.name}位于涨停天梯{group.label}；行业/概念："
                        f"{stock.industry or '待确认'} {'、'.join(stock.concepts[:3])}；"
                        f"封单{stock.sealed_amount:.2f}亿，炸板{stock.break_count}次。"
                    )
                    if sector_context.startswith("未在"):
                        sector_context = f"涨停天梯支持：{stock.industry or '待确认'}，{stock.expectation}"
                    break
    elif sector_context.startswith("未在"):
        sector_context = "涨停天梯暂无缓存：本次保存不阻塞外部行情，盘后刷新涨停天梯后可补充验证。"
    return market_context, sector_context, stock_context


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)


def _contains_positive_any(text: str, keywords: tuple[str, ...]) -> bool:
    negative_markers = ("没有", "没写", "未写", "缺少", "不明确", "无")
    for keyword in keywords:
        if not keyword:
            continue
        start = text.find(keyword)
        while start >= 0:
            prefix = text[max(0, start - 8):start]
            if not any(marker in prefix for marker in negative_markers):
                return True
            start = text.find(keyword, start + len(keyword))
    return False


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _infer_weakness_tags(mistakes: list[str]) -> list[str]:
    joined = "".join(mistakes)
    tags: list[str] = []
    if "仓位" in joined or "超过" in joined:
        tags.append("贪婪")
    if "主线" in joined or "前排" in joined:
        tags.append("冲动追高")
    if "理由为空" in joined or "无计划" in joined:
        tags.append("冲动")
    if "卖出" in joined or "感觉" in joined:
        tags.append("恐惧")
    return tags or ["纪律待强化"]


def _discipline_score(trade: TradeLog, mistakes: list[str]) -> int:
    score = 100
    if not trade.compliant:
        score -= 20
    score -= min(45, max(0, len(mistakes) - 1) * 12)
    if trade.position_ratio > 0.4 and "集中" not in trade.mode:
        score -= 15
    if not trade.reason.strip():
        score -= 15
    return max(0, min(100, score))


def _trade_review_summary(
    trade: TradeLog,
    verdict: str,
    mistakes: list[str],
    avoid_actions: list[str],
) -> str:
    mistake = mistakes[0] if mistakes else "未发现明显问题"
    action = avoid_actions[0] if avoid_actions else "继续按计划复盘执行。"
    return f"{trade.side}{trade.name}复盘：{verdict}。核心问题：{mistake} 后续动作：{action}"


def _sell_plan(holding: Holding) -> SellPlanOut:
    guard = profit_guard_price(holding.cost_price, holding.current_price)
    return SellPlanOut(
        code=holding.code,
        name=holding.name,
        first_trim_price=round(max(holding.current_price * 1.04, holding.cost_price * 1.05), 2),
        second_exit_price=round(max(holding.current_price * 1.075, holding.cost_price * 1.1), 2),
        failure_price=round(guard or holding.cost_price * 0.96, 2),
        sell_ratios=["第一层卖 1/3", "第二层再卖 1/3", "尾仓按封板与 VWAP 决定"],
        allow_buyback=False,
        buyback_condition="重新站上确认位且板块仍为前排，买回不超过原仓 1/2",
        condition_orders=[
            "较前收盘涨 4%-5% 或达到压力位：卖 1/3",
            "涨 7%-8% 或接近涨停但封板弱：再卖 1/3",
            "从盘中高点回落 5%：剩余进攻仓至少减半",
        ],
    )


def _market_seesaw_monitor(
    holdings: list[Holding],
    force_refresh: bool = False,
) -> MarketSeesawOut:
    notes: list[str] = []
    industry_flows: list[Any] = []
    concept_flows: list[Any] = []
    sources: list[str] = []
    cached_concept = None if force_refresh else _get_response_cache("sector-flow|概念资金流|今日")
    cached_industry = None if force_refresh else _get_response_cache("sector-flow|行业资金流|今日")
    try:
        concept_flow = cached_concept or market_provider.sector_flow(
            flow_type="概念资金流",
            period="今日",
            force_refresh=force_refresh,
        )
        concept_flows.extend(concept_flow.inflow + concept_flow.outflow)
        sources.append(f"概念资金流/{concept_flow.source}")
    except Exception as exc:
        notes.append(f"概念资金流不可用：{exc}")
    try:
        industry_flow = cached_industry or market_provider.sector_flow(
            flow_type="行业资金流",
            period="今日",
            force_refresh=force_refresh,
        )
        industry_flows.extend(industry_flow.inflow + industry_flow.outflow)
        sources.append(f"行业资金流/{industry_flow.source}")
    except Exception as exc:
        notes.append(f"行业资金流不可用：{exc}")

    unique_industry_flows = _dedupe_sector_flows(industry_flows)
    unique_concept_flows = _dedupe_sector_flows(concept_flows)
    ranked_industry = sorted(unique_industry_flows, key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct), reverse=True)
    ranked_concept = sorted(unique_concept_flows, key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct), reverse=True)
    industry_rank_map = {item.name: idx for idx, item in enumerate(ranked_industry, start=1)}
    concept_rank_map = {item.name: idx for idx, item in enumerate(ranked_concept, start=1)}
    limit_counts = _sector_limit_up_counts()

    inflow_targets = [
        _sector_rotation_item(item, industry_rank_map.get(item.name, 0), limit_counts)
        for item in ranked_industry[:10]
    ]
    outflow_ranked = sorted(unique_industry_flows, key=lambda item: (item.net_inflow, item.main_inflow))
    outflow_targets = [
        _sector_rotation_item(item, industry_rank_map.get(item.name, 0), limit_counts)
        for item in outflow_ranked[:8]
        if item.net_inflow < 0 or item.main_inflow < 0
    ]

    quotes = _latest_quotes_for_holdings(holdings)
    holding_alerts = [
        _holding_seesaw_item(
            holding,
            quotes.get(_quote_lookup_code(holding.code, quotes), {}),
            ranked_industry,
            industry_rank_map,
            ranked_concept,
            concept_rank_map,
            inflow_targets,
        )
        for holding in holdings
    ]
    severe_count = sum(1 for item in holding_alerts if item.risk_level in {"高", "中高"})
    strong_targets = [item for item in inflow_targets if item.net_inflow > 0 and (item.acceleration > 0 or item.limit_up_count >= 2)]
    market_mode = "存量跷跷板明显" if strong_targets and severe_count else "轮动观察"
    if len(strong_targets) >= 2 and severe_count >= 2:
        market_mode = "存量资金快速迁移"
    top_target = strong_targets[0].name if strong_targets else (inflow_targets[0].name if inflow_targets else "暂无")
    summary = (
        f"行业资金流当前最强吸金方向：{top_target}；"
        f"{'已有持仓出现冲高回落/板块失血，需要保护利润。' if severe_count else '暂未触发持仓级强告警，继续观察板块排名和个股VWAP。'}"
    )
    return MarketSeesawOut(
        source="+".join(dict.fromkeys(sources)) or "diagnostic",
        updated_at=datetime.now(),
        market_mode=market_mode,
        summary=summary,
        inflow_targets=inflow_targets[:8],
        outflow_targets=outflow_targets[:6],
        holding_alerts=sorted(
            holding_alerts,
            key=lambda item: ({"高": 4, "中高": 3, "中": 2, "观察": 1}.get(item.risk_level, 0), item.pullback_from_high_pct),
            reverse=True,
        ),
        notes=notes or ["主判定口径为行业资金流；概念资金流仅作为辅助证据。"],
    )


def _dedupe_sector_flows(flows: list[Any]) -> list[Any]:
    best: dict[str, Any] = {}
    for item in flows:
        name = str(getattr(item, "name", "") or getattr(item, "display_name", "") or "")
        if not name:
            continue
        previous = best.get(name)
        if previous is None or abs(float(getattr(item, "net_inflow", 0) or 0)) > abs(float(getattr(previous, "net_inflow", 0) or 0)):
            best[name] = item
    return list(best.values())


def _sector_rotation_item(item: Any, rank: int, limit_counts: dict[str, int]) -> SectorRotationItem:
    acceleration = _sector_acceleration(item)
    names = _sector_aliases(item)
    limit_count = max(limit_counts.get(name, 0) for name in names) if names else 0
    direction = "加速流入" if acceleration > 0 else "流入减速" if acceleration < 0 else "资金平稳"
    evidence = (
        f"排名第{rank}，涨跌{float(item.change_pct):+.2f}%，净流入{float(item.net_inflow):.2f}亿，"
        f"主力净流入{float(item.main_inflow):.2f}亿，盘中变化{acceleration:+.2f}亿，涨停{limit_count}只，{direction}。"
    )
    return SectorRotationItem(
        name=str(item.name),
        rank=rank,
        change_pct=round(float(item.change_pct or 0), 2),
        net_inflow=round(float(item.net_inflow or 0), 2),
        main_inflow=round(float(item.main_inflow or 0), 2),
        acceleration=round(acceleration, 2),
        limit_up_count=limit_count,
        leaders=[str(leader) for leader in getattr(item, "leaders", [])[:4]],
        evidence=evidence,
    )


def _sector_acceleration(item: Any) -> float:
    points = list(getattr(item, "timeline", []) or [])
    values = [float(getattr(point, "value", 0) or 0) for point in points]
    if len(values) >= 2:
        return values[-1] - values[max(0, len(values) - 4)]
    return float(getattr(item, "net_inflow", 0) or 0)


def _sector_aliases(item: Any) -> list[str]:
    raw = [
        getattr(item, "name", ""),
        getattr(item, "display_name", ""),
        getattr(item, "raw_name", ""),
        getattr(item, "theme_line", ""),
        getattr(item, "mainline", ""),
        getattr(item, "subline", ""),
        getattr(item, "category", ""),
    ]
    return [str(value) for value in raw if str(value or "").strip()]


def _sector_limit_up_counts() -> dict[str, int]:
    counts: Counter[str] = Counter()
    try:
        ladder = market_provider.limit_up_ladder(force_refresh=False)
    except Exception:
        ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is None:
        return {}
    for cluster in getattr(ladder, "clusters", []) or []:
        counts[str(cluster.name)] += int(cluster.count or 0)
    for group in getattr(ladder, "groups", []) or []:
        for stock in getattr(group, "stocks", []) or []:
            if stock.industry:
                counts[str(stock.industry)] += 1
            for concept in stock.concepts[:4]:
                counts[str(concept)] += 1
    return dict(counts)


def _latest_quotes_for_holdings(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    try:
        return _latest_a_share_quotes([holding.code for holding in holdings if holding.code])
    except Exception:
        return {}


def _holding_seesaw_item(
    holding: Holding,
    quote: dict[str, Any],
    ranked_industry_flows: list[Any],
    industry_rank_map: dict[str, int],
    ranked_concept_flows: list[Any],
    concept_rank_map: dict[str, int],
    inflow_targets: list[SectorRotationItem],
) -> HoldingSeesawItem:
    current = _safe_float(quote.get("price")) or holding.current_price
    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    change_pct = _safe_float(quote.get("change_pct"))
    high_change_pct = ((high - prev_close) / prev_close * 100) if high and prev_close else max(change_pct, 0)
    pullback = max(0, high_change_pct - change_pct)
    estimated_vwap = _estimated_vwap(quote)
    below_vwap = bool(estimated_vwap and current < estimated_vwap)
    theme_profile = _holding_theme_profile(holding)
    holding_theme = str(theme_profile["primary"])
    theme_tags = list(theme_profile["tags"])
    theme_flow = _holding_theme_flow_profile(
        holding,
        ranked_industry_flows,
        industry_rank_map,
        ranked_concept_flows,
        concept_rank_map,
    )
    matched_flow = theme_flow["primary_flow"]
    theme_flow_sectors = list(theme_flow["sectors"])
    concept_flow_sectors = list(theme_flow["concept_sectors"])
    matched_flow_sector = str(getattr(matched_flow, "name", "") or "") or "、".join(theme_flow_sectors[:3])
    sector = holding_theme
    sector_rank = int(theme_flow["rank"])
    sector_net = float(theme_flow["current"])
    sector_main = float(theme_flow["main"])
    sector_acc = float(theme_flow["acceleration"])
    theme_flow_peak = float(theme_flow["peak"])
    theme_flow_pullback = float(theme_flow["pullback"])
    theme_flow_pullback_pct = float(theme_flow["pullback_pct"])
    theme_flow_summary = str(theme_flow["summary"])
    concept_flow_summary = str(theme_flow["concept_summary"])
    strongest = inflow_targets[0] if inflow_targets else None
    strongest_name = strongest.name if strongest else "暂无强吸金方向"
    strongest_is_other = bool(
        strongest
        and holding_theme
        and strongest.name not in theme_flow_sectors
        and _sector_family(strongest.name) != _sector_family(holding_theme)
    )
    external_inflow_target = strongest_name if strongest_is_other else ""
    evidence: list[str] = [
        f"所属主线：{holding_theme}；标签：{' / '.join(theme_tags) or '待补充'}。",
        (
            f"个股板块画像：行业={theme_profile.get('industry') or '未抓到'}；"
            f"概念={ '、'.join(list(theme_profile.get('concepts') or [])[:8]) or '未抓到'}；"
            f"来源={theme_profile.get('source') or 'fallback'}。"
        ),
        f"{holding.name}最高涨幅{high_change_pct:.2f}%，当前涨幅{change_pct:.2f}%，高点回撤{pullback:.2f}%。",
    ]
    if estimated_vwap:
        evidence.append(f"估算VWAP {estimated_vwap:.2f}，当前{'跌破' if below_vwap else '仍在'}VWAP。")
    if theme_flow_sectors:
        evidence.append(theme_flow_summary)
    else:
        evidence.append("主资金曲线：未在行业/概念资金流中精确匹配；仅展示个股画像，不强行替代。")
    if concept_flow_sectors:
        evidence.append(concept_flow_summary)
    if strongest:
        evidence.append(f"外部吸金方向：{strongest.name}，净流入{strongest.net_inflow:.2f}亿，涨停{strongest.limit_up_count}只。")

    sell_triggers = _intraday_sell_triggers(
        holding=holding,
        current=current,
        high=high,
        high_change_pct=high_change_pct,
        change_pct=change_pct,
        pullback=pullback,
        below_vwap=below_vwap,
        sector=sector,
        sector_rank=sector_rank,
        sector_net=sector_net,
        sector_main=sector_main,
        sector_acc=sector_acc,
        sector_flow_peak=theme_flow_peak,
        sector_flow_current=sector_net,
        sector_flow_pullback=theme_flow_pullback,
        sector_flow_pullback_pct=theme_flow_pullback_pct,
        strongest_name=strongest_name,
        strongest_is_other=strongest_is_other,
    )

    score = 0
    if strongest_is_other and strongest.net_inflow > max(0, sector_net):
        score += 2
    if sector_net < 0 or sector_main < 0 or sector_acc < -1:
        score += 2
    if theme_flow_pullback >= 20 or theme_flow_pullback_pct >= 20:
        score += 2
    if pullback >= 4:
        score += 2
    elif pullback >= 2.5:
        score += 1
    if below_vwap:
        score += 2
    if high_change_pct >= 7 and pullback >= 3:
        score += 1
    if sell_triggers["profit_drawdown_trigger"]:
        score += 2
    if sell_triggers["stock_weakening_trigger"] and sell_triggers["sector_ebb_trigger"]:
        score += 1

    if score >= 6:
        risk_level = "高"
        signal = (
            f"{holding_theme}承压，外部吸金方向为{external_inflow_target}，个股冲高回落弱于预期"
            if external_inflow_target
            else f"{holding_theme}承压，个股冲高回落弱于预期"
        )
        advice = sell_triggers["trigger_action"] or "优先保护利润：跌破/反抽不过VWAP继续减仓；若板块资金仍流出，不再加仓或买回。"
    elif score >= 4:
        risk_level = "中高"
        signal = "资金跷跷板风险升高"
        advice = sell_triggers["trigger_action"] or "持有降为观察：不加仓；若高点回撤扩大或跌破VWAP，先减一部分风险。"
    elif score >= 2:
        risk_level = "中"
        signal = "板块轮动分流"
        advice = sell_triggers["trigger_action"] or "继续观察板块排名和个股承接，只有重新站稳VWAP且板块资金回流才提高预期。"
    else:
        risk_level = "观察"
        signal = "暂未触发跷跷板风险"
        advice = "按原计划持有观察，重点看所属板块是否继续在资金榜前列。"

    return HoldingSeesawItem(
        code=holding.code,
        name=holding.name,
        sector=sector,
        holding_theme=holding_theme,
        theme_tags=theme_tags,
        stock_industry=str(theme_profile.get("industry") or ""),
        stock_concepts=[str(item) for item in theme_profile.get("concepts", [])],
        theme_source=str(theme_profile.get("source") or ""),
        flow_basis=str(theme_flow.get("basis") or "行业资金流"),
        primary_industry_sector=matched_flow_sector,
        concept_flow_sectors=concept_flow_sectors,
        concept_flow_summary=concept_flow_summary,
        matched_flow_sector=matched_flow_sector,
        theme_flow_sectors=theme_flow_sectors,
        theme_flow_summary=theme_flow_summary,
        theme_flow_current=round(sector_net, 2),
        theme_flow_peak=round(theme_flow_peak, 2),
        theme_flow_pullback=round(theme_flow_pullback, 2),
        theme_flow_pullback_pct=round(theme_flow_pullback_pct, 2),
        external_inflow_target=external_inflow_target,
        current_price=round(current, 2),
        change_pct=round(change_pct, 2),
        high_change_pct=round(high_change_pct, 2),
        pullback_from_high_pct=round(pullback, 2),
        estimated_vwap=round(estimated_vwap, 2),
        below_vwap=below_vwap,
        sector_rank=sector_rank,
        sector_net_inflow=round(sector_net, 2),
        sector_main_inflow=round(sector_main, 2),
        sector_acceleration=round(sector_acc, 2),
        risk_level=risk_level,
        signal=signal,
        advice=advice,
        profit_protection_state=sell_triggers["profit_protection_state"],
        trigger_action=sell_triggers["trigger_action"],
        sector_ebb_trigger=sell_triggers["sector_ebb_trigger"],
        stock_weakening_trigger=sell_triggers["stock_weakening_trigger"],
        profit_drawdown_trigger=sell_triggers["profit_drawdown_trigger"],
        buyback_trigger=sell_triggers["buyback_trigger"],
        evidence=evidence,
        theme_flow_timeline=list(theme_flow.get("timeline_points", [])),
    )


def _intraday_sell_triggers(
    holding: Holding,
    current: float,
    high: float,
    high_change_pct: float,
    change_pct: float,
    pullback: float,
    below_vwap: bool,
    sector: str,
    sector_rank: int,
    sector_net: float,
    sector_main: float,
    sector_acc: float,
    sector_flow_peak: float = 0,
    sector_flow_current: float = 0,
    sector_flow_pullback: float = 0,
    sector_flow_pullback_pct: float = 0,
    strongest_name: str = "",
    strongest_is_other: bool = False,
) -> dict[str, Any]:
    current_profit_pct = ((current - holding.cost_price) / holding.cost_price * 100) if holding.cost_price else 0
    high_profit_pct = ((high - holding.cost_price) / holding.cost_price * 100) if high and holding.cost_price else max(current_profit_pct, high_change_pct)
    profit_drawdown = max(0, high_profit_pct - current_profit_pct)
    sector_triggers: list[str] = []
    stock_triggers: list[str] = []
    profit_triggers: list[str] = []

    if sector_net < 0:
        sector_triggers.append(f"{sector or '所属板块'}净流入转负：{sector_net:.2f}亿。")
    if sector_main < 0:
        sector_triggers.append(f"{sector or '所属板块'}主力净流入转负：{sector_main:.2f}亿。")
    if sector_acc < -1:
        sector_triggers.append(f"{sector or '所属板块'}盘中资金变化{sector_acc:+.2f}亿，出现退潮。")
    if sector_flow_pullback >= 20 or sector_flow_pullback_pct >= 20:
        sector_triggers.append(
            f"{sector or '所属板块'}主线资金从高点{sector_flow_peak:.2f}亿回落到{sector_flow_current:.2f}亿，"
            f"回落{sector_flow_pullback:.2f}亿（{sector_flow_pullback_pct:.1f}%），即使当前仍净流入也按退潮处理。"
        )
    if sector_rank and sector_rank > 10:
        sector_triggers.append(f"{sector or '所属板块'}资金排名降至第{sector_rank}，不在前排。")
    if strongest_is_other:
        sector_triggers.append(f"资金排名切向{strongest_name}，形成跷跷板分流。")

    if high_change_pct >= 9 and pullback >= 3:
        stock_triggers.append(f"盘中接近涨停/强冲高后回撤{pullback:.2f}%，冲板失败风险升高。")
    elif high_change_pct >= 5 and pullback >= 3:
        stock_triggers.append(f"强势冲高后回撤{pullback:.2f}%，由强转分歧。")
    if pullback >= 5:
        stock_triggers.append("从日内高点回撤超过5%，利润回吐速度偏快。")
    elif pullback >= 3:
        stock_triggers.append("从日内高点回撤超过3%，触发减仓观察。")
    if below_vwap:
        stock_triggers.append("当前跌破估算分时均价/VWAP，且反抽未确认前不提高预期。")
    if change_pct < 0 and high_change_pct >= 5:
        stock_triggers.append("从高浮盈杀到翻绿/收跌区间，属于强转弱而非普通震荡。")

    if high_profit_pct >= 8:
        protection_state = f"最高浮盈约{high_profit_pct:.2f}%，进入8%-10%分批兑现区。"
    elif high_profit_pct >= 5:
        protection_state = f"最高浮盈约{high_profit_pct:.2f}%，进入5%以上利润保护区。"
    else:
        protection_state = "尚未进入5%利润保护区，按原止损/预期管理。"

    if high_profit_pct >= 5 and profit_drawdown >= 3:
        profit_triggers.append(f"浮盈5%以上后回撤{profit_drawdown:.2f}%，先减一部分观察。")
    if high_profit_pct >= 5 and profit_drawdown >= 5 and below_vwap:
        profit_triggers.append("浮盈保护区内回撤超过5%且跌破VWAP，优先兑现至少一半。")
    if high_profit_pct >= 8 and (sector_triggers or below_vwap or pullback >= 3):
        profit_triggers.append("浮盈8%-10%区间未能封住强势，不能再按亏损票逻辑死等。")

    if profit_triggers and stock_triggers and sector_triggers:
        action = "三类卖出信号共振：板块退潮、个股转弱、利润回撤同时出现，按卖出/减仓信号处理，先保护利润。"
    elif profit_triggers and stock_triggers:
        action = "利润保护与个股弱化同时触发：先减仓观察，跌破/反抽不过VWAP继续降风险。"
    elif sector_triggers and stock_triggers:
        action = "板块退潮叠加个股弱化：持有降级，不加仓，反抽不过VWAP优先减仓。"
    elif profit_triggers:
        action = "进入利润保护状态：回撤达到规则阈值，先兑现一部分，不把盈利票拿成被动票。"
    elif sector_triggers:
        action = "板块资金出现分流：继续观察个股是否跌破VWAP，未转强前不接回。"
    else:
        action = ""

    buyback = [
        "板块止跌或资金重新回流。",
        "个股不再创新低，并重新站回分时均价/VWAP。",
        "下跌缩量、反弹放量；买回后设置失败位，跌破日内低点或VWAP不继续补。",
    ]
    return {
        "profit_protection_state": protection_state,
        "trigger_action": action,
        "sector_ebb_trigger": sector_triggers,
        "stock_weakening_trigger": stock_triggers,
        "profit_drawdown_trigger": profit_triggers,
        "buyback_trigger": buyback,
    }


def _estimated_vwap(quote: dict[str, Any]) -> float:
    amount_yuan = _safe_float(quote.get("amount")) * 1e8
    volume_shares = _safe_float(quote.get("volume"))
    return amount_yuan / volume_shares if amount_yuan and volume_shares else 0


def _match_holding_sector_flow(holding: Holding, ranked_flows: list[Any]) -> Any | None:
    best: tuple[int, Any] | None = None
    for flow in ranked_flows:
        score = _holding_flow_match_score(holding, flow)
        if score and (best is None or score > best[0]):
            best = (score, flow)
    return best[1] if best else None


def _holding_theme_flow_profile(
    holding: Holding,
    ranked_industry_flows: list[Any],
    industry_rank_map: dict[str, int],
    ranked_concept_flows: list[Any] | None = None,
    concept_rank_map: dict[str, int] | None = None,
) -> dict[str, Any]:
    theme_profile = _holding_theme_profile(holding)
    preferred_flow = _preferred_industry_board_flow(holding)
    matched_industry: list[tuple[int, Any]] = []
    for flow in ranked_industry_flows:
        score = _holding_flow_match_score(holding, flow)
        if score > 0:
            matched_industry.append((score, flow))
    matched_industry.sort(
        key=lambda pair: (
            pair[0],
            abs(float(getattr(pair[1], "net_inflow", 0) or 0)),
            abs(_sector_acceleration(pair[1])),
        ),
        reverse=True,
    )
    selected = [preferred_flow] if preferred_flow is not None else [flow for _, flow in matched_industry[:1]]
    primary_flow = selected[0] if selected else None
    sectors = [str(getattr(flow, "name", "") or "") for flow in selected if str(getattr(flow, "name", "") or "")]
    current = sum(float(getattr(flow, "net_inflow", 0) or 0) for flow in selected)
    main = sum(float(getattr(flow, "main_inflow", 0) or 0) for flow in selected)
    acceleration = sum(_sector_acceleration(flow) for flow in selected)
    rank = min((industry_rank_map.get(name, 999) for name in sectors), default=0)
    timeline = _aggregate_flow_timeline(selected)
    peak = max(timeline.values()) if timeline else current
    if current > peak:
        peak = current
    pullback = max(0.0, peak - current)
    pullback_pct = pullback / abs(peak) * 100 if peak else 0.0
    concept_matched: list[tuple[int, Any]] = []
    for flow in ranked_concept_flows or []:
        score = _holding_flow_match_score(holding, flow)
        if score > 0:
            concept_matched.append((score, flow))
    concept_matched.sort(
        key=lambda pair: (
            pair[0],
            abs(float(getattr(pair[1], "net_inflow", 0) or 0)),
            abs(_sector_acceleration(pair[1])),
        ),
        reverse=True,
    )
    priority_aliases = [str(item) for item in theme_profile.get("priority_flow_aliases", []) if str(item).strip()]
    if priority_aliases:
        concept_selected = [
            flow
            for _, flow in concept_matched
            if _flow_matches_aliases(flow, priority_aliases)
        ][:4]
    else:
        concept_selected = [flow for _, flow in concept_matched[:4]]
    best_industry_score = matched_industry[0][0] if matched_industry else 0
    best_concept_score = concept_matched[0][0] if concept_matched else 0
    priority_concept = next(
        (
            flow
            for _, flow in concept_matched
            if _flow_matches_aliases(flow, priority_aliases)
        ),
        None,
    )
    use_concept_primary = bool(
        priority_concept is not None
        and best_concept_score >= best_industry_score
        and _holding_theme_prefers_concept_flow(theme_profile)
    )
    basis = "概念资金流" if use_concept_primary else "行业资金流"
    if (
        _holding_theme_prefers_concept_flow(theme_profile)
        and priority_concept is None
        and selected
        and not any(_flow_matches_aliases(flow, priority_aliases) for flow in selected)
    ):
        selected = []
        primary_flow = None
        sectors = []
        current = 0.0
        main = 0.0
        acceleration = 0.0
        rank = 0
        peak = 0.0
        pullback = 0.0
        pullback_pct = 0.0
        basis = "资金流缺口"
    if use_concept_primary:
        selected = [priority_concept]
        primary_flow = selected[0]
        sectors = [str(getattr(primary_flow, "name", "") or "")]
        rank = concept_rank_map.get(sectors[0], 999) if concept_rank_map and sectors and sectors[0] else 0
        concept_selected = [flow for flow in concept_selected if flow is not primary_flow]
        current = sum(float(getattr(flow, "net_inflow", 0) or 0) for flow in selected)
        main = sum(float(getattr(flow, "main_inflow", 0) or 0) for flow in selected)
        acceleration = sum(_sector_acceleration(flow) for flow in selected)
        timeline = _aggregate_flow_timeline(selected)
        peak = max(timeline.values()) if timeline else current
        if current > peak:
            peak = current
        pullback = max(0.0, peak - current)
        pullback_pct = pullback / abs(peak) * 100 if peak else 0.0
    concept_sectors = [
        str(getattr(flow, "name", "") or "")
        for flow in concept_selected
        if str(getattr(flow, "name", "") or "")
    ]
    concept_current = sum(float(getattr(flow, "net_inflow", 0) or 0) for flow in concept_selected)
    concept_main = sum(float(getattr(flow, "main_inflow", 0) or 0) for flow in concept_selected)
    concept_rank = min((concept_rank_map.get(name, 999) for name in concept_sectors), default=0) if concept_rank_map else 0
    if sectors:
        sector_text = "、".join(sectors[:3])
        rank_name = "概念排名" if basis == "概念资金流" else "行业排名"
        rank_text = f"{rank_name}第{rank}" if rank and rank != 999 else "优先细分板块"
        summary = (
            f"主资金曲线：{sector_text}（{basis}）；{rank_text}，当前净流入{current:.2f}亿，"
            f"主力净流入{main:.2f}亿，盘中变化{acceleration:+.2f}亿。"
        )
        if pullback > 0:
            summary += f" 高点{peak:.2f}亿回落至当前，回落{pullback:.2f}亿（{pullback_pct:.1f}%）。"
    else:
        summary = ""
    if concept_sectors:
        concept_summary = (
            f"概念辅助证据：{'、'.join(concept_sectors[:4])}；"
            f"最佳概念排名第{0 if concept_rank == 999 else concept_rank}，"
            f"合计净流入{concept_current:.2f}亿，主力净流入{concept_main:.2f}亿。"
        )
    else:
        concept_summary = ""
    # Build actual timeline from matched flows (prefer snapshots; fallback to synthetic)
    _tl = _aggregate_flow_timeline(selected)
    if len(_tl) < 3 and sectors:
        # try broader industry flow timeline for the same sector name
        broader_timeline = _broader_industry_timeline(ranked_industry_flows, sectors, theme_profile)
        if broader_timeline and len(broader_timeline) >= len(_tl):
            _tl = broader_timeline
    if not _tl and current != 0:
        _tl = {datetime.now().strftime("%H:%M"): current}
    timeline_points = [
        {"time": t, "value": round(v, 2)}
        for t, v in sorted(_tl.items()) if v or v == 0
    ]
    if not timeline_points and current:
        timeline_points = [{"time": datetime.now().strftime("%H:%M"), "value": round(current, 2)}]

    return {
        "primary_flow": primary_flow,
        "basis": basis,
        "sectors": sectors,
        "rank": 0 if rank == 999 else rank,
        "current": current,
        "main": main,
        "acceleration": acceleration,
        "peak": peak,
        "pullback": pullback,
        "pullback_pct": pullback_pct,
        "summary": summary,
        "concept_sectors": concept_sectors,
        "concept_summary": concept_summary,
        "concept_current": concept_current,
        "concept_main": concept_main,
        "timeline_points": timeline_points,
    }


def _broader_industry_timeline(
    ranked_industry_flows: list[Any],
    holding_sectors: list[str],
    theme_profile: dict[str, Any],
) -> dict[str, float] | None:
    """Try to get a richer timeline from the broad industry flow list for the holding's sector."""
    best: tuple[int, dict[str, float]] | None = None
    # Map holding sector keywords to East Money industry names that appear in sector_flow
    _SECTOR_TO_INDUSTRY_FLOW: dict[str, list[str]] = {
        "半导体": ["电子信息", "电子器件", "半导体设备"],
        "AI算力": ["电子信息", "电子器件", "计算机行业", "通信行业"],
        "商业航天": ["飞机制造", "航天航空", "军工航天"],
        "医药": ["生物制药", "化学制药", "医疗器械"],
        "机器人": ["机械行业", "电器行业"],
        "新能源": ["发电设备", "新能源车"],
        "化工": ["化工行业", "化纤行业"],
    }
    for flow in ranked_industry_flows:
        flow_names = _sector_aliases(flow)
        match_score = 0
        for s in holding_sectors:
            if not s:
                continue
            # direct match
            for name in flow_names:
                if not name:
                    continue
                if s in name or name in s:
                    match_score += 4
                    break
            # mapped match
            mapped = _SECTOR_TO_INDUSTRY_FLOW.get(s, [])
            for m in mapped:
                for name in flow_names:
                    if m in name or name in m:
                        match_score += 3
                        break
        if match_score > 0:
            tl = _aggregate_flow_timeline([flow])
            if tl and (best is None or match_score > best[0] or len(tl) > len(best[1])):
                best = (match_score, tl)
    return best[1] if best else None


def _preferred_industry_board_flow(holding: Holding) -> Any | None:
    theme_profile = _holding_theme_profile(holding)
    for secid, display_name in theme_profile.get("preferred_industry_boards", []) or []:
        try:
            flow = _fetch_eastmoney_h5_board_flow(str(secid), str(display_name))
        except Exception:
            continue
        if flow is not None:
            return flow
    return None


def _fetch_eastmoney_h5_board_flow(secid: str, display_name: str) -> Any | None:
    cache_key = f"em-h5-board-flow|{_last_trading_day()}|{secid}"
    cached = _get_response_cache(cache_key)
    if cached is not None:
        return cached
    resp = requests.get(
        "https://emdatah5.eastmoney.com/dc/ZJLX/getZJLXData",
        params={
            "secid": secid,
            "fields": "f57,f58,f135,f136,f137,f138,f139,f140,f141,f142,f143,f144,f145,f146,f147,f148,f149,f86",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
        },
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://emdatah5.eastmoney.com/"},
        timeout=6,
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data") or {}
    if not data:
        return None
    name = str(data.get("f58") or display_name)
    main_net = round(float(data.get("f137") or 0) / 1e8, 2)
    super_large_net = round(float(data.get("f140") or 0) / 1e8, 2)
    large_net = round(float(data.get("f143") or 0) / 1e8, 2)
    main_inflow = round(super_large_net + large_net, 2) if (super_large_net or large_net) else main_net
    flow = SimpleNamespace(
        name=name,
        display_name=name,
        raw_name=name,
        board_code=secid.split(".")[-1],
        provider="eastmoney-h5",
        theme_line=name,
        mainline=name,
        subline="",
        category="行业资金流",
        change_pct=0.0,
        net_inflow=main_net,
        main_inflow=main_inflow,
        strength=max(0, min(100, int(50 + main_net / 2))),
        leaders=[],
        timeline=_eastmoney_h5_board_timeline(secid, main_net),
    )
    _set_response_cache(cache_key, flow)
    return flow


def _eastmoney_h5_board_timeline(secid: str, current: float) -> list[Any]:
    # Eastmoney H5 exposes reliable current board fund flow; intraday minute flow
    # can be unavailable for boards, so keep a stable synthetic point instead of
    # mixing a broad industry curve into a specific board.
    now_label = datetime.now().strftime("%H:%M")
    return [SimpleNamespace(time=now_label, value=current)]


def _holding_flow_match_score(holding: Holding, flow: Any) -> int:
    theme_profile = _holding_theme_profile(holding)
    target_text = (
        f"{holding.name} {holding.code} {holding.position_type} {holding.next_discipline} "
        f"{theme_profile['primary']} {' '.join(theme_profile['tags'])} "
        f"{theme_profile.get('industry', '')} {' '.join(theme_profile.get('concepts', []))}"
    )
    aliases = list(dict.fromkeys(list(theme_profile["flow_aliases"]) + _holding_sector_keywords(holding)))
    names = _sector_aliases(flow)
    score = 0
    for name in names:
        if name and name in target_text:
            score += 4
    for alias in aliases:
        if any(alias in name or name in alias for name in names if name):
            score += 3
    for alias in theme_profile.get("priority_flow_aliases", []) or []:
        if any(alias in name or name in alias for name in names if name):
            score += 6
    return score


def _flow_matches_aliases(flow: Any, aliases: list[str]) -> bool:
    names = _sector_aliases(flow)
    return any(
        alias and any(alias in name or name in alias for name in names if name)
        for alias in aliases
    )


def _aggregate_flow_timeline(flows: list[Any]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for flow in flows:
        for point in getattr(flow, "timeline", []) or []:
            time_key = str(getattr(point, "time", "") or "")
            if not time_key:
                continue
            totals[time_key] += float(getattr(point, "value", 0) or 0)
    return dict(totals)


def _holding_stock_board_profile(holding: Holding) -> dict[str, Any]:
    code = str(holding.code or "").strip()
    cache_key = f"stock-board-profile|{code}"
    cached = _get_response_cache(cache_key)
    if cached is not None:
        return cached

    profile = {
        "industry": "",
        "concepts": [],
        "source": "",
    }
    if not re.fullmatch(r"\d{6}", code):
        return profile

    # 1) Sina stock profile page
    try:
        resp = requests.get(
            f"https://vip.stock.finance.sina.com.cn/corp/go.php/vCI_CorpOtherInfo/stockid/{code}.phtml",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=8,
        )
        resp.raise_for_status()
        resp.encoding = "gb2312"
        html = resp.text
        industry = _extract_sina_stock_industry(html)
        concepts = _extract_sina_stock_concepts(html)
        if industry or concepts:
            profile = {
                "industry": industry,
                "concepts": concepts,
                "source": "sina-stock-board",
            }
            _set_response_cache(cache_key, profile)
            return profile
    except Exception:
        pass

    # 2) East Money stock detail for board & concept
    try:
        em = _fetch_em_stock_board(code)
        if em.get("industry") or em.get("concepts"):
            profile = {
                "industry": em.get("industry") or "",
                "concepts": em.get("concepts") or [],
                "source": "eastmoney-stock-detail",
            }
            _set_response_cache(cache_key, profile)
            return profile
    except Exception:
        pass

    # 3) Fallback: reuse the local limit-up ladder cache when the stock is present.
    try:
        ladder = market_provider.limit_up_ladder(force_refresh=False)
    except Exception:
        ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for group in getattr(ladder, "groups", []) or []:
            for stock in getattr(group, "stocks", []) or []:
                if str(getattr(stock, "code", "") or "") == code or str(getattr(stock, "name", "") or "") == str(holding.name or ""):
                    profile = {
                        "industry": str(getattr(stock, "industry", "") or ""),
                        "concepts": [str(item) for item in getattr(stock, "concepts", []) if str(item).strip()],
                        "source": "limit-up-ladder-cache",
                    }
                    _set_response_cache(cache_key, profile)
                    return profile

    _set_response_cache(cache_key, profile)
    return profile


def _extract_sina_stock_industry(html: str) -> str:
    match = re.search(r"所属行业板块.*?<tr>\s*<td[^>]*>(.*?)</td>", html, re.S)
    return _strip_html(match.group(1)) if match else ""


def _extract_sina_stock_concepts(html: str) -> list[str]:
    start = html.find("所属概念板块")
    if start < 0:
        return []
    section = html[start:]
    rows = re.findall(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>", section, re.S)
    concepts: list[str] = []
    skip = {"所属行业板块", "所属概念板块", "概念板块", "同概念个股"}
    for raw in rows:
        value = _strip_html(raw)
        if not value or value in skip or "备注" in value or "点击查看" in value or "对不起" in value:
            continue
        if len(value) > 30:
            continue
        if value not in concepts:
            concepts.append(value)
    return concepts


def _strip_html(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw or "")).strip()


def _fetch_em_stock_board(code: str) -> dict[str, Any]:
    """Try East Money stock detail for industry + concepts."""
    try:
        market = "1" if code.startswith("6") else "0"
        resp = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={
                "secid": f"{market}.{code}",
                "fields": "f57,f58,f127,f55,f100,f102,f103",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        data = resp.json().get("data") or {}
        industry = str(data.get("f100") or "")
        concept_str = str(data.get("f103") or "")
        concepts: list[str] = []
        if concept_str and concept_str != "-":
            concepts = [c.strip() for c in concept_str.split(",") if c.strip()]
        return {"industry": industry, "concepts": concepts}
    except Exception:
        return {}


_THEME_RULES: list[dict[str, Any]] = [
    {
        "primary": "AI算力链",
        "tags": ["AI算力", "服务器", "数据中心", "液冷", "东数西算"],
        "keywords": ["算力", "服务器", "东数西算", "液冷", "云计算", "英伟达", "CPO", "光模块", "数据中心", "腾讯云", "人工智能", "AI", "铜连接", "高速连接器", "光通信"],
        "flow_aliases": ["AI算力", "算力概念", "服务器", "东数西算", "液冷概念", "云计算", "英伟达概念", "数据中心", "CPO", "光模块", "人工智能", "铜连接", "算力租赁"],
        "prefer_concept": True,
    },
    {
        "primary": "商业航天链",
        "tags": ["商业航天", "卫星互联网", "北斗导航", "航天军工"],
        "keywords": ["商业航天", "卫星互联网", "卫星导航", "北斗", "航天", "火箭", "空间站", "低空经济", "无人机", "军工航天", "国防军工", "军工信息化", "军民融合"],
        "flow_aliases": ["商业航天", "卫星互联网", "卫星导航", "北斗导航", "航天航空", "航天军工", "军工", "国防军工", "军工信息化", "低空经济", "无人机"],
        "prefer_concept": True,
    },
    {
        "primary": "半导体链",
        "tags": ["半导体", "芯片产业链", "集成电路"],
        "keywords": ["半导体", "芯片", "集成电路", "先进封装", "封测", "半导体设备", "光刻机", "存储芯片"],
        "flow_aliases": ["半导体", "芯片", "集成电路", "先进封装", "封装", "封测", "半导体设备", "光刻机", "存储芯片", "华为海思"],
        "preferred_industry_boards": [("90.BK1036", "半导体")],
    },
    {
        "primary": "机器人链",
        "tags": ["机器人", "人形机器人", "工业自动化"],
        "keywords": ["机器人", "人形机器人", "减速器", "工业母机", "机器视觉"],
        "flow_aliases": ["机器人", "人形机器人", "减速器", "工业母机", "机器视觉"],
        "prefer_concept": True,
    },
    {
        "primary": "医药链 / 创新药",
        "tags": ["医药", "创新药", "生物医药"],
        "keywords": ["创新药", "医药", "生物医药", "CRO", "医疗器械", "减肥药"],
        "flow_aliases": ["创新药", "医药", "生物医药", "CRO", "医疗器械", "减肥药"],
    },
    {
        "primary": "新能源链",
        "tags": ["新能源", "光伏", "锂电", "储能"],
        "keywords": ["新能源", "光伏", "锂电", "固态电池", "储能", "风电"],
        "flow_aliases": ["新能源", "光伏", "锂电池", "固态电池", "储能", "风电"],
    },
    {
        "primary": "化工材料链",
        "tags": ["化工", "新材料"],
        "keywords": ["化工", "塑料", "新材料", "化纤", "染料", "有机硅"],
        "flow_aliases": ["化工", "化工行业", "塑料", "新材料", "化纤", "有机硅"],
    },
    {
        "primary": "消费电子链",
        "tags": ["消费电子", "电子元件", "PCB"],
        "keywords": ["消费电子", "电子元件", "电子零部件", "PCB", "OLED", "华为概念", "小米概念"],
        "flow_aliases": ["消费电子", "电子元件", "电子零部件", "PCB", "OLED", "电子信息", "电子器件"],
    },
]


def _holding_theme_prefers_concept_flow(theme_profile: dict[str, Any]) -> bool:
    return bool(theme_profile.get("prefer_concept"))


def _holding_theme_profile(holding: Holding) -> dict[str, Any]:
    code = str(holding.code or "").strip()
    name = str(holding.name or "")
    board_profile = _holding_stock_board_profile(holding)
    industry = str(board_profile.get("industry") or "")
    concepts = [
        str(item)
        for item in board_profile.get("concepts", [])
        if str(item).strip() and str(item) != industry
    ]
    text = f"{code} {name} {industry} {' '.join(concepts)} {holding.position_type or ''} {holding.next_discipline or ''}"

    scored: list[tuple[int, dict[str, Any]]] = []
    for rule in _THEME_RULES:
        score = 0
        for keyword in rule["keywords"]:
            if keyword and keyword in text:
                score += 3 if keyword in " ".join(concepts) else 2
        if rule["primary"] in text:
            score += 4
        if score:
            scored.append((score, rule))
    scored.sort(key=lambda item: item[0], reverse=True)
    if scored:
        rule = scored[0][1]
        tags = list(dict.fromkeys([*rule["tags"], *[c for c in concepts if any(k in c for k in rule["keywords"])][:4]]))
        aliases = list(dict.fromkeys([*rule["flow_aliases"], industry, *concepts]))
        primary = str(rule["primary"])
        if industry and industry not in primary and any(key in industry for key in ("半导体", "航天", "电子", "医药", "机器人", "新能源", "化工")):
            primary = f"{primary} / {industry}"
        return {
            "primary": primary,
            "tags": tags,
            "flow_aliases": aliases,
            "priority_flow_aliases": list(dict.fromkeys([*rule["flow_aliases"], *rule["tags"]])),
            "preferred_industry_boards": rule.get("preferred_industry_boards", []),
            "prefer_concept": bool(rule.get("prefer_concept")),
            "industry": industry,
            "concepts": concepts,
            "source": board_profile.get("source") or "theme-rules",
        }

    fallback = _holding_sector_keywords(holding)
    aliases = list(dict.fromkeys([*fallback, industry, *concepts]))
    return {
        "primary": fallback[0] if fallback else (industry or "待确认主线"),
        "tags": aliases[:8],
        "flow_aliases": aliases,
        "priority_flow_aliases": fallback,
        "preferred_industry_boards": [],
        "prefer_concept": False,
        "industry": industry,
        "concepts": concepts,
        "source": board_profile.get("source") or "fallback",
    }


def _holding_sector_keywords(holding: Holding) -> list[str]:
    text = f"{holding.name} {holding.position_type} {holding.next_discipline}"
    mapping = {
        "半导体": ("长电", "半导体", "芯片", "封测", "科创半导体"),
        "先进封装": ("长电", "封装", "封测"),
        "电子信息": ("半导体", "芯片", "电子", "PCB", "消费电子"),
        "AI算力": ("浪潮", "算力", "服务器", "AI", "人工智能", "CPO", "利通", "英伟达", "云计算", "液冷", "东数西算"),
        "商业航天": ("航天", "卫星", "火箭", "军工", "军民融合"),
        "创新药": ("海正", "医药", "创新药", "药业", "生物"),
        "电子元件": ("PCB", "消费电子", "OLED"),
        "机器人": ("机器人", "减速器", "人形"),
        "新能源": ("新能源", "光伏", "锂电", "固态电池", "储能"),
        "化工材料": ("化工", "塑料", "材料"),
    }
    hits: list[str] = []
    for sector, keywords in mapping.items():
        if any(keyword in text for keyword in keywords):
            hits.append(sector)
    return hits


def _fallback_holding_sector(holding: Holding) -> str:
    return str(_holding_theme_profile(holding)["primary"])


def _sector_family(name: str) -> str:
    if any(key in name for key in ("半导体", "芯片", "封装", "电子", "AI", "算力", "服务器", "CPO", "光模块")):
        return "科技"
    if any(key in name for key in ("航天", "卫星", "火箭", "军工")):
        return "商业航天"
    if any(key in name for key in ("医药", "创新药", "药")):
        return "医药"
    if any(key in name for key in ("机器人", "减速器", "人形")):
        return "机器人"
    if any(key in name for key in ("新能源", "光伏", "锂电", "固态电池", "储能")):
        return "新能源"
    if any(key in name for key in ("化工", "塑料", "材料")):
        return "化工材料"
    return name


_CATEGORY_RISK_PRIORITY = {
    "弱于预期": 1,
    "分歧转弱": 2,
    "弱转强": 3,
    "符合预期": 4,
    "强预期": 5,
    "超预期": 6,
    "低价情绪股": 1,
    "高位巨量分歧股": 2,
    "弱于预期股": 3,
    "震荡趋势股": 4,
    "主线前排股": 5,
}


_FORBIDDEN_BY_CATEGORY = {
    "弱于预期": ["不补仓", "反抽优先减仓", "不默认接回"],
    "分歧转弱": ["不追高", "不扩大做T风险", "先确认承接"],
    "弱转强": ["不抢第一笔翻红", "不无条件买回"],
    "符合预期": ["不追高", "不脱离计划做T"],
    "强预期": ["不因小波动丢核心仓", "不机械做T"],
    "超预期": ["不追最高点", "不临盘扩大仓位"],
    "低价情绪股": ["不追高", "不补仓", "不接回", "不新增风险"],
    "高位巨量分歧股": ["不追高", "不补仓", "不扩大做T风险"],
    "弱于预期股": ["不补仓", "不默认接回", "反抽优先减仓"],
    "震荡趋势股": ["不追高", "不无条件买回"],
    "主线前排股": ["不机械做T", "不因小波动丢核心仓"],
}


def _next_trade_date() -> str:
    d = datetime.now() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _default_next_day_plan(
    holding: Holding,
    plan_date: str,
    account_total_asset: float | None = None,
    quote: dict[str, Any] | None = None,
) -> NextDayPlan:
    total_asset = account_total_asset if account_total_asset is not None else holding.total_asset
    position_ratio = (
        holding.quantity * holding.current_price / total_asset
        if total_asset
        else 0
    )
    evidence = _holding_market_evidence(holding, quote)
    category = _infer_expectation_category(holding, evidence, quote)
    basis = ClassificationBasis(
        sector=evidence.get("sector") or "待盘后确认",
        mainline_position=evidence.get("mainline_position") or "待识别是否为主线前排",
        fund_flow=evidence.get("fund_flow") or "待结合资金流证据页确认",
        amount=evidence.get("amount") or "待补成交额",
        turnover=evidence.get("turnover") or "待补换手率",
        trend="；".join(
            item
            for item in [
                evidence.get("intraday"),
                evidence.get("trend") or "按确认位、支撑位、压力位复核",
            ]
            if item
        ),
        support=str(round(holding.current_price * 0.97, 2)),
        pressure=str(round(holding.current_price * 1.04, 2)),
        weaker_than_sector=bool(evidence.get("weaker_than_sector")),
    )
    dynamic_plan = _dynamic_holding_auction_plan(holding, category, evidence, quote)
    trim_quantity = max(0, holding.quantity // 3)
    plan = NextDayPlan(
        plan_date=plan_date,
        plan_type="holding",
        holding_id=holding.id,
        code=holding.code,
        name=holding.name,
        quantity=holding.quantity,
        cost_price=holding.cost_price,
        current_price=holding.current_price,
        position_ratio=round(position_ratio, 4),
        holding_category=category,
        classification_basis=basis.model_dump_json(),
        outperform_condition=_outperform_condition(category),
        outperform_action=_outperform_action(category),
        expected_condition=_expected_condition(category),
        expected_action=_expected_action(category),
        underperform_condition=_underperform_condition(category),
        underperform_action=_underperform_action(category),
        confirm_price=round(max(holding.current_price, holding.cost_price), 2),
        trim_price=round(max(holding.current_price * 1.03, holding.cost_price * 1.04), 2),
        trim_condition="冲高到压力位或放量不封板时分批高抛",
        trim_quantity=trim_quantity,
        allow_buyback=category in {"符合预期", "弱转强"},
        buyback_price=round(holding.current_price * 0.97, 2),
        buyback_condition="回落到支撑位缩量企稳，重新站回分时均价/VWAP",
        max_buyback_quantity=trim_quantity if category in {"符合预期", "弱转强"} else 0,
        reduce_price=round(holding.cost_price * 0.98, 2),
        final_risk_price=round(holding.cost_price * 0.96, 2),
        stop_loss_4pct=round(holding.cost_price * 0.96, 2),
        auction_plan=json.dumps(dynamic_plan, ensure_ascii=False),
        forbidden_actions=json.dumps(_FORBIDDEN_BY_CATEGORY.get(category, []), ensure_ascii=False),
    )
    _refresh_plan_risk(plan)
    return plan


def _sync_holding_plan(existing: NextDayPlan, fresh: NextDayPlan) -> None:
    existing.holding_id = fresh.holding_id
    existing.quantity = fresh.quantity
    existing.cost_price = fresh.cost_price
    existing.current_price = fresh.current_price
    existing.position_ratio = fresh.position_ratio
    existing.holding_category = fresh.holding_category
    existing.classification_basis = fresh.classification_basis
    existing.confirm_price = fresh.confirm_price
    existing.trim_price = fresh.trim_price
    existing.trim_quantity = fresh.trim_quantity
    existing.buyback_price = fresh.buyback_price
    existing.max_buyback_quantity = fresh.max_buyback_quantity if existing.allow_buyback else 0
    existing.reduce_price = fresh.reduce_price
    existing.final_risk_price = fresh.final_risk_price
    existing.stop_loss_4pct = fresh.stop_loss_4pct
    existing.auction_plan = fresh.auction_plan
    existing.forbidden_actions = fresh.forbidden_actions
    for field in (
        "outperform_condition",
        "outperform_action",
        "expected_condition",
        "expected_action",
        "underperform_condition",
        "underperform_action",
        "trim_condition",
        "buyback_condition",
    ):
        if not str(getattr(existing, field) or "").strip():
            setattr(existing, field, getattr(fresh, field))
    _refresh_plan_risk(existing)


def _limit_up_next_day_plan(
    payload: LimitUpPlanCreate,
    plan_date: str,
    existing: NextDayPlan | None = None,
) -> NextDayPlan:
    next_limit_price = _next_limit_up_price(payload.price)
    turnover = _safe_turnover(payload.turnover)
    concepts = [item for item in payload.concepts if item]
    concept_text = "、".join(concepts[:4]) or "待补概念"
    board_level = f"{max(payload.level, 1)}板"
    evidence = _limit_up_auction_evidence(payload, concepts)
    risk_notes = evidence["risk_notes"]
    weak_reduce_price = round(payload.price, 2)
    weak_exit_price = round(payload.price * 0.97, 2)
    keep_condition = (
        "9:20后封单不明显撤退，同题材核心股同步强化；"
        "开盘后5-15分钟站稳分时均价/VWAP，不能只看9:15-9:20虚假竞价。"
    )
    cancel_condition = (
        "9:20后封单快速衰减、同题材前排走弱、竞价高开低走或炸板后无承接，立即撤单。"
    )
    auction_plan = {
        "board_level": board_level,
        "industry": payload.industry,
        "concepts": concepts,
        "overnight_order": True,
        "order_price": next_limit_price,
        "limit_up_price": next_limit_price,
        "keep_order_condition": keep_condition,
        "cancel_condition": cancel_condition,
        "opening_confirmation": "集合竞价只是筛选，连续竞价开盘后的承接才是确认。",
        "max_position_ratio": payload.max_position_ratio,
        "break_limit_action": "炸板后不临时加仓；只有强回封、板块仍扩散、成交承接健康时才重新评估。",
        "notes": payload.expectation,
        "board_strength": evidence["board_strength"],
        "leader_support": evidence["leader_support"],
        "limit_quality": evidence["limit_quality"],
        "expectation_level": evidence["expectation_level"],
        "strong_boundary_price": weak_reduce_price,
        "weak_reduce_price": weak_reduce_price,
        "weak_exit_price": weak_exit_price,
        "risk_notes": risk_notes,
    }
    plan = existing or NextDayPlan()
    plan.plan_date = plan_date
    plan.plan_type = "limit_up_auction"
    plan.holding_id = None
    plan.code = payload.code
    plan.name = payload.name
    plan.quantity = 0
    plan.cost_price = payload.price
    plan.current_price = payload.price
    plan.position_ratio = 0
    plan.holding_category = "主线前排股"
    plan.classification_basis = json.dumps(
        {
            "sector": payload.industry or concept_text,
            "mainline_position": evidence["mainline_position"] or f"{board_level}涨停股，需明日竞价确认是否仍是前排",
            "fund_flow": evidence["board_strength"] or f"涨停封单约{payload.sealed_amount:.2f}亿，成交约{payload.amount:.2f}亿",
            "amount": f"{payload.amount:.2f}亿",
            "turnover": f"{turnover:.2f}%" if turnover is not None else f"数据异常：原始换手率 {payload.turnover}",
            "trend": evidence["limit_quality"],
            "support": str(payload.price),
            "pressure": str(next_limit_price),
            "weaker_than_sector": False,
        },
        ensure_ascii=False,
    )
    plan.outperform_condition = (
        f"超预期：{payload.name}直接一字，或高开5%以上快速加速上板；"
        f"{payload.industry or concept_text}继续强于市场，前排助攻不掉队。"
        f"若属于高位天量后的再一致，只按加速末段处理，明日涨停参考 {next_limit_price:.2f}。"
    )
    plan.outperform_action = (
        f"超预期才看晋级：若封单稳定且板块助攻成立，持有为主；"
        f"委托价不高于 {next_limit_price:.2f}，仓位上限 {payload.max_position_ratio * 100:.0f}%。"
        "高位天量或偏离5日线过远时以保护利润为主，不继续扩大仓位。"
    )
    plan.expected_condition = (
        f"符合预期：高开2%-5%，短暂换手后10点前回封；"
        f"回踩不破 {weak_reduce_price:.2f}，分时均价承接强，板块资金仍在前排。"
    )
    plan.expected_action = (
        f"持有观察，不加仓；若迟迟不板但仍站稳 {weak_reduce_price:.2f}，可以保留底仓，"
        "冲高封单转弱时先锁一部分利润。"
    )
    plan.underperform_condition = (
        f"弱于预期：平开/低开，或高开后不能快速上板；跌破强弱分界 {weak_reduce_price:.2f} 后不能迅速收回；"
        f"若继续跌破清仓线 {weak_exit_price:.2f}，说明接力失败；"
        "个股强而板块无助攻时，涨停预期下调一级。"
    )
    plan.underperform_action = (
        f"跌破 {weak_reduce_price:.2f} 且5-15分钟不能收回，先减仓至少1/2；"
        f"跌破分时均价后反抽不过继续减仓；跌破 {weak_exit_price:.2f} 或冲板失败放量回落，清掉剩余仓位。"
    )
    plan.confirm_price = payload.price
    plan.trim_price = 0
    plan.trim_condition = "买入后次日再生成卖出计划；打板当日不做T。"
    plan.trim_quantity = 0
    plan.allow_buyback = False
    plan.buyback_price = 0
    plan.buyback_condition = ""
    plan.max_buyback_quantity = 0
    plan.reduce_price = weak_reduce_price
    plan.final_risk_price = weak_exit_price
    plan.stop_loss_4pct = round(payload.price * 0.96, 2)
    plan.limit_up_price = next_limit_price
    plan.auction_plan = json.dumps(auction_plan, ensure_ascii=False)
    plan.forbidden_actions = json.dumps(
        [
            "不无脑隔夜成交",
            "不看9:15虚假封单",
            "不盘中临时追高",
            "高位天量后不继续扩大仓位",
            "炸板无承接必须放弃",
        ],
        ensure_ascii=False,
    )
    _refresh_plan_risk(plan)
    return plan


def _limit_up_auction_evidence(
    payload: LimitUpPlanCreate,
    concepts: list[str],
) -> dict[str, Any]:
    concept_text = " ".join([payload.industry, payload.name, *concepts])
    board_strength = "板块资金数据缺口：请先刷新题材雷达/资金流，再生成打板预案。"
    mainline_position = ""
    leader_support: list[str] = []
    board_supported = False
    weak_board = False
    support_count = 0

    radar = _get_response_cache("theme-radar")
    if radar is not None:
        for idx, theme in enumerate(radar.themes[:20], start=1):
            theme_mainline = str(getattr(theme, "mainline", "") or getattr(theme, "theme_type", "") or "")
            theme_subline = str(getattr(theme, "subline", "") or "")
            theme_category = str(getattr(theme, "category", "") or "")
            related = " ".join([
                theme.name,
                theme_mainline,
                theme_subline,
                *theme.related_boards,
                *theme.leader_names,
                *(role.name for role in theme.core_stocks),
                *(role.code for role in theme.core_stocks),
            ])
            if _contains_any(related, tuple([payload.industry, *concepts, payload.name, payload.code])) or _contains_any(concept_text, tuple([theme.name, theme_mainline, theme_subline])):
                board_strength = (
                    f"{theme.name}：题材强度{theme.score}分，排名第{idx}；"
                    f"板块净流入{theme.net_inflow:.2f}亿，主力净流入{theme.main_inflow:.2f}亿，"
                    f"涨停{theme.limit_up_count}只，阶段={theme.stage}。"
                )
                board_supported = theme.net_inflow > 0 and theme.main_inflow > 0 and theme.limit_up_count >= 3
                weak_board = theme.net_inflow <= 0 or theme.main_inflow <= 0 or theme.limit_up_count <= 1 or theme.score < 60
                support_count = theme.limit_up_count
                mainline_position = (
                    f"{theme.name} / {theme_mainline or theme_category or '待分类'}，"
                    f"{'主线前排' if theme.score >= 75 else '轮动/分歧题材'}。"
                )
                leader_support = [
                    f"{role.name}({role.code}) {role.role}，涨跌{role.change_pct:+.2f}%，成交{role.amount:.2f}亿：{role.reason}"
                    for role in theme.core_stocks[:6]
                ] or [f"核心股：{name}" for name in theme.leader_names[:6]]
                break

    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    ladder_support: list[str] = []
    break_count = payload.break_count
    if ladder is not None:
        matched_clusters = [
            cluster
            for cluster in ladder.clusters
            if _contains_any(cluster.name, tuple([payload.industry, *concepts]))
            or _contains_any(" ".join(cluster.stocks), tuple([payload.name, payload.code, *concepts]))
        ]
        for cluster in matched_clusters[:3]:
            support_count = max(support_count, cluster.count)
            ladder_support.append(
                f"{cluster.name}：{cluster.count}只涨停，最高{cluster.highest_level}板，前排 {'、'.join(cluster.stocks[:6])}。"
            )
        for group in ladder.groups:
            for stock in group.stocks:
                if stock.code == payload.code or stock.name == payload.name:
                    break_count = stock.break_count
                    quality = _limit_quality_text(stock.amount, stock.turnover, stock.break_count, stock.sealed_amount, group.label)
                    if quality:
                        payload.amount = stock.amount or payload.amount
                        payload.turnover = stock.turnover or payload.turnover
                        payload.sealed_amount = stock.sealed_amount or payload.sealed_amount
                    break

    if ladder_support:
        leader_support = list(dict.fromkeys([*leader_support, *ladder_support]))[:8]
    if not leader_support:
        leader_support = ["前排助攻数据缺口：请先刷新涨停天梯和题材雷达。"]

    limit_quality = _limit_quality_text(
        payload.amount,
        payload.turnover,
        break_count,
        payload.sealed_amount,
        f"{max(payload.level, 1)}板",
    )
    risk_notes = _auction_risk_notes(payload, break_count, board_supported, weak_board, support_count)
    expectation_level = _auction_expectation_level(payload, break_count, board_strength, weak_board, support_count)
    return {
        "board_strength": board_strength,
        "mainline_position": mainline_position,
        "leader_support": leader_support,
        "limit_quality": limit_quality,
        "expectation_level": expectation_level,
        "risk_notes": risk_notes,
    }


def _limit_quality_text(
    amount: float,
    turnover: float,
    break_count: int,
    sealed_amount: float,
    board_level: str,
) -> str:
    quality = "一致强封"
    if break_count > 0:
        quality = "高换手分歧回封"
    if amount >= 80:
        quality = "容量核心放量换手板" if break_count == 0 else "容量核心爆量分歧回封"
    elif turnover >= 18 or break_count >= 1:
        quality = "高换手分歧回封"
    if sealed_amount <= 0.2 and break_count > 0:
        quality = "弱封单分歧回封"
    return (
        f"{board_level}，{quality}；成交{amount:.2f}亿，换手{turnover:.2f}%，"
        f"炸板{break_count}次，封单{sealed_amount:.2f}亿。"
    )


def _auction_expectation_level(
    payload: LimitUpPlanCreate,
    break_count: int,
    board_strength: str,
    weak_board: bool = False,
    support_count: int = 0,
) -> str:
    strong_board = "主力净流入" in board_strength and "数据缺口" not in board_strength
    if weak_board or (support_count and support_count <= 1):
        if break_count > 0 or payload.turnover >= 18 or payload.amount >= 20:
            return "个股强而板块弱，预期下调：次日必须强更强"
    if payload.amount >= 80:
        return "容量核心放量换手，风险升高但可观察"
    if break_count == 0 and payload.turnover < 12 and payload.sealed_amount >= 1:
        return "强预期"
    if break_count > 0 or payload.turnover >= 18:
        return "分歧偏弱，次日必须强更强"
    return "符合预期"


def _auction_risk_notes(
    payload: LimitUpPlanCreate,
    break_count: int,
    board_supported: bool,
    weak_board: bool,
    support_count: int,
) -> list[str]:
    turnover = _safe_turnover(payload.turnover) or 0
    notes: list[str] = []
    high_level = payload.level >= 2
    high_volume = payload.amount >= 20 or turnover >= 18
    if high_level and high_volume:
        if payload.amount >= 80:
            notes.append("容量核心放量换手：风险权重升高，但若主线资金和前排助攻持续，仍有观察价值。")
        elif break_count > 0:
            notes.append("高位天量分歧回封：继续转一致难度上升，次日必须强更强。")
        else:
            notes.append("高位放量：筹码交换剧烈，风险收益比下降，不能按低位启动看待。")
    if high_level and payload.price > 0:
        notes.append("高位天量后若次日再一致，只按加速末段处理，保护利润优先，不宜继续扩大仓位。")
    if high_volume:
        notes.append("偏离5日线风险：未取得均线数据时按高位放量替代提示，禁止追高加仓，等待竞价/开盘强势确认。")
    if weak_board:
        notes.append("板块资金不支持：个股独立行情持续性下降，若次日无板块助攻，涨停预期下调一级。")
    elif not board_supported:
        notes.append("板块共振证据不足：需补充题材雷达/资金流和涨停天梯后再提高预期。")
    if support_count <= 1:
        notes.append("前排/后排助攻不足：同题材梯队或首板扩散不足，次日必须个股强更强。")
    notes.append(f"弱于预期价格触发：跌破{payload.price:.2f}先减仓，跌破分时均价后反抽不过继续减仓，跌破{payload.price * 0.97:.2f}附近清仓。")
    return list(dict.fromkeys(notes))


def _holding_market_evidence(
    holding: Holding,
    quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    theme_profile = _holding_theme_profile(holding)
    evidence: dict[str, Any] = {
        "sector": theme_profile["primary"],
        "theme_tags": list(theme_profile["tags"]),
        "stock_industry": theme_profile.get("industry") or "",
        "stock_concepts": list(theme_profile.get("concepts") or []),
        "theme_source": theme_profile.get("source") or "",
        "mainline_position": (
            f"所属主线：{theme_profile['primary']}；标签：{' / '.join(theme_profile['tags']) or '待确认'}；"
            f"原始行业={theme_profile.get('industry') or '未抓到'}；"
            f"原始概念={ '、'.join(list(theme_profile.get('concepts') or [])[:6]) or '未抓到'}。"
        ),
        "fund_flow": "",
        "amount": "",
        "turnover": "",
        "trend": "",
        "intraday": "",
        "weaker_than_sector": False,
        "is_mainline_front": False,
        "is_high_divergence": False,
        "is_underperforming": False,
    }
    quote = quote or {}
    if quote:
        current_price = _safe_float(quote.get("price")) or holding.current_price
        open_price = _safe_float(quote.get("open"))
        prev_close = _safe_float(quote.get("prev_close"))
        high_price = _safe_float(quote.get("high"))
        low_price = _safe_float(quote.get("low"))
        change_pct = _safe_float(quote.get("change_pct"))
        amount = _safe_float(quote.get("amount"))
        turnover = quote.get("turnover")
        if amount > 0:
            evidence["amount"] = f"{amount:.2f}亿"
        if turnover:
            evidence["turnover"] = f"{turnover:.2f}%"
        open_gap = ((open_price - prev_close) / prev_close * 100) if prev_close else 0
        intraday_repair = bool(
            prev_close
            and open_price
            and low_price
            and open_price < prev_close
            and current_price >= prev_close
        )
        high_reject = bool(high_price and current_price <= high_price * 0.97 and change_pct <= 2)
        if prev_close and open_price:
            evidence["intraday"] = (
                f"今开{open_price:.2f}（开盘{open_gap:+.2f}%），"
                f"现价{current_price:.2f}（涨跌{change_pct:+.2f}%），"
                f"日内高低{high_price:.2f}/{low_price:.2f}。"
            )
        else:
            evidence["intraday"] = "分时字段不足，仅使用最新价和涨跌幅做降级判断。"
        evidence["gap_pct"] = open_gap
        evidence["change_pct"] = change_pct
        evidence["intraday_repair"] = intraday_repair
        evidence["high_reject"] = high_reject
        evidence["strong_open"] = open_gap >= 2 and current_price >= open_price
        evidence["super_expectation"] = open_gap >= 3 and change_pct >= 5 and not high_reject
        evidence["weak_open"] = open_gap <= -1.5
        volume_context = _volume_price_context(holding.code, quote)
        evidence.update(volume_context)
    theme_flow = _cached_holding_theme_flow_profile(holding)
    if theme_flow["sectors"]:
        evidence["flow_basis"] = theme_flow.get("basis") or "行业资金流"
        evidence["primary_industry_sector"] = "、".join(theme_flow["sectors"][:3])
        evidence["matched_flow_sector"] = evidence["primary_industry_sector"]
        evidence["theme_flow_sectors"] = list(theme_flow["sectors"])
        evidence["concept_flow_sectors"] = list(theme_flow.get("concept_sectors") or [])
        evidence["concept_flow_summary"] = theme_flow.get("concept_summary") or ""
        evidence["theme_flow_summary"] = theme_flow["summary"]
        evidence["theme_flow_current"] = theme_flow["current"]
        evidence["theme_flow_peak"] = theme_flow["peak"]
        evidence["theme_flow_pullback"] = theme_flow["pullback"]
        evidence["theme_flow_pullback_pct"] = theme_flow["pullback_pct"]
        evidence["fund_flow"] = theme_flow["summary"]
        evidence["is_underperforming"] = bool(
            evidence.get("is_underperforming")
            or theme_flow["pullback"] >= 20
            or theme_flow["pullback_pct"] >= 20
            or theme_flow["current"] < 0
        )
    radar = _get_response_cache("theme-radar")
    if radar is not None:
        for theme in radar.themes[:20]:
            stock_names = [role.name for role in theme.core_stocks]
            stock_codes = [role.code for role in theme.core_stocks]
            related_text = "".join(theme.related_boards + theme.leader_names + stock_names + stock_codes)
            if holding.code in related_text or holding.name in related_text or _contains_any(
                f"{holding.position_type} {holding.next_discipline}",
                tuple(theme.related_boards + [theme.name]),
            ):
                evidence["radar_auxiliary_sector"] = theme.name
                evidence["mainline_position"] = (
                    f"所属主线：{theme_profile['primary']}；题材雷达辅助：{theme.name}，"
                    f"{theme.stage}，题材评分{theme.score}；核心股："
                    f"{'、'.join(theme.leader_names[:4]) or '待确认'}"
                )
                if not evidence.get("fund_flow"):
                    evidence["fund_flow"] = (
                        f"题材雷达辅助：{theme.name}净流入{theme.net_inflow:.2f}亿，"
                        f"主力净流入{theme.main_inflow:.2f}亿，涨停{theme.limit_up_count}只。"
                    )
                evidence["is_mainline_front"] = (
                    theme.score >= 75
                    and (holding.name in theme.leader_names or holding.code in stock_codes or holding.name in stock_names)
                )
                evidence["is_underperforming"] = theme.score < 55 or theme.net_inflow < 0
                break

    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for group in ladder.groups:
            for stock in group.stocks:
                if stock.code == holding.code or stock.name == holding.name:
                    turnover = _safe_turnover(stock.turnover)
                    if stock.industry:
                        evidence["ladder_auxiliary_sector"] = stock.industry
                    evidence["mainline_position"] = (
                        f"所属主线：{theme_profile['primary']}；涨停天梯{group.label}；"
                        f"概念：{'、'.join(stock.concepts[:4]) or '待确认'}。"
                    )
                    evidence["amount"] = f"{stock.amount:.2f}亿"
                    evidence["turnover"] = (
                        f"{turnover:.2f}%"
                        if turnover is not None
                        else f"数据异常：原始换手率 {stock.turnover}"
                    )
                    if not evidence.get("fund_flow"):
                        evidence["fund_flow"] = (
                            f"涨停天梯辅助：封单{stock.sealed_amount:.2f}亿，"
                            f"炸板{stock.break_count}次；{stock.expectation}"
                        )
                    evidence["is_mainline_front"] = True
                    evidence["is_high_divergence"] = (
                        stock.break_count >= 2
                        or stock.amount >= 30
                        or (turnover is not None and turnover >= 25)
                    )
                    evidence["trend"] = "涨停强势结构，次日必须看封单、开盘承接和板块扩散。"
                    return evidence

    current = holding.current_price
    cost = holding.cost_price
    if cost and current < cost * 0.97:
        evidence["is_underperforming"] = True
        evidence["trend"] = "现价低于成本3%以上，优先按弱修复/退出纪律处理。"
    elif cost and current > cost * 1.12:
        evidence["trend"] = "已有较明显利润垫，关注冲高兑现和回撤保护。"
    return evidence


def _cached_holding_theme_flow_profile(holding: Holding) -> dict[str, Any]:
    industry_flows: list[Any] = []
    concept_flows: list[Any] = []
    cached_industry = _get_response_cache("sector-flow|行业资金流|今日")
    if cached_industry is not None:
        industry_flows.extend(list(getattr(cached_industry, "inflow", []) or []))
        industry_flows.extend(list(getattr(cached_industry, "outflow", []) or []))
    cached_concept = _get_response_cache("sector-flow|概念资金流|今日")
    if cached_concept is not None:
        concept_flows.extend(list(getattr(cached_concept, "inflow", []) or []))
        concept_flows.extend(list(getattr(cached_concept, "outflow", []) or []))
    ranked_industry = sorted(
        _dedupe_sector_flows(industry_flows),
        key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct),
        reverse=True,
    )
    ranked_concept = sorted(
        _dedupe_sector_flows(concept_flows),
        key=lambda item: (item.net_inflow, item.main_inflow, item.change_pct),
        reverse=True,
    )
    industry_rank_map = {item.name: idx for idx, item in enumerate(ranked_industry, start=1)}
    concept_rank_map = {item.name: idx for idx, item in enumerate(ranked_concept, start=1)}
    return _holding_theme_flow_profile(holding, ranked_industry, industry_rank_map, ranked_concept, concept_rank_map)


def _volume_price_context(code: str, quote: dict[str, Any]) -> dict[str, Any]:
    amount_today = _safe_float(quote.get("amount"))
    volume_today = _safe_float(quote.get("volume")) / 100
    change_pct = _safe_float(quote.get("change_pct"))
    high_price = _safe_float(quote.get("high"))
    low_price = _safe_float(quote.get("low"))
    open_price = _safe_float(quote.get("open"))
    price = _safe_float(quote.get("price"))
    hist = _daily_history_metrics(code)
    five_day_avg_volume = _safe_float(hist.get("five_day_avg_volume"))
    ma5 = _safe_float(hist.get("ma5"))
    volume_ratio = volume_today / five_day_avg_volume if volume_today and five_day_avg_volume else 0
    amplitude = (high_price - low_price) / _safe_float(quote.get("prev_close")) * 100 if high_price and low_price and quote.get("prev_close") else 0
    status = "量价数据不足"
    if volume_ratio >= 2.5 and abs(change_pct) >= 3:
        status = "放巨量分歧" if change_pct < 5 else "放巨量拉升"
    elif volume_ratio >= 1.3 and change_pct >= 3:
        status = "放量拉升"
    elif 0 < volume_ratio < 1.0 and change_pct >= 3:
        status = "缩量拉升"
    elif volume_ratio >= 1.2 and change_pct <= -3:
        status = "放量大跌"
    elif 0 < volume_ratio < 0.8 and -1 <= change_pct <= 1:
        status = "缩量震荡"
    elif 0 < volume_ratio < 0.8 and change_pct > 0:
        status = "缩量止跌/修复"
    elif volume_ratio >= 1.1 and abs(change_pct) < 1.5:
        status = "放量滞涨/震荡"
    elif volume_ratio >= 1.1:
        status = "轻微放量"
    elif volume_ratio > 0:
        status = "缩量整理"

    ma5_deviation = (price - ma5) / ma5 * 100 if price and ma5 else 0
    detail = (
        f"今日成交额{amount_today:.2f}亿，今日成交量{volume_today:.0f}手，近5日均量{five_day_avg_volume:.0f}手，"
        f"量比{volume_ratio:.2f}；涨跌{change_pct:+.2f}%，振幅{amplitude:.2f}%"
    )
    if ma5:
        detail += f"，5日均价{ma5:.2f}，偏离{ma5_deviation:+.2f}%"
    return {
        "volume_price_status": status,
        "volume_price_detail": detail,
        "five_day_avg_amount": five_day_avg_volume,
        "today_amount": amount_today,
        "today_volume": volume_today,
        "volume_ratio": volume_ratio,
        "ma5": ma5,
        "ma5_deviation": ma5_deviation,
    }


def _daily_history_metrics(code: str) -> dict[str, float]:
    candidates = _quote_code_candidates(code)
    if not candidates:
        return {}
    try:
        candidate = candidates[0]
        symbol = ("sh" if candidate.startswith(("5", "6", "9")) else "sz") + candidate
        url = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
        resp = requests.get(
            url,
            params={"param": f"{symbol},day,,,8"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
            timeout=4,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = (((payload.get("data") or {}).get(symbol) or {}).get("day") or [])
        if not rows:
            return {}
        volumes = [_safe_float(row[5]) for row in rows if len(row) > 5]
        closes = [_safe_float(row[2]) for row in rows if len(row) > 2]
        prev_volumes = volumes[:-1] if len(volumes) >= 2 else volumes
        return {
            "five_day_avg_volume": sum(prev_volumes[-5:]) / len(prev_volumes[-5:]) if prev_volumes else 0,
            "ma5": sum(closes[-5:]) / len(closes[-5:]) if closes else 0,
        }
    except Exception:
        return {}


def _dynamic_holding_auction_plan(
    holding: Holding,
    category: str,
    evidence: dict[str, Any],
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    quote = quote or {}
    current = _safe_float(quote.get("price")) or holding.current_price
    prev_close = _safe_float(quote.get("prev_close"))
    open_price = _safe_float(quote.get("open"))
    change_pct = _safe_float(quote.get("change_pct"))
    volume_status = str(evidence.get("volume_price_status") or "量价数据不足")
    expected_state = _expected_condition(category)
    expectation_match = _expectation_match_label(evidence, category)
    operation_advice = _dynamic_operation_advice(expectation_match, category, holding, current)
    board_strength = evidence.get("fund_flow") or "板块资金证据缺口：请刷新题材雷达/资金流。"
    leader_support = _leader_support_for_holding(holding, evidence)
    limit_quality = (
        f"{holding.name}盘中状态：{evidence.get('intraday') or '实时分时字段不足'}；"
        f"{evidence.get('volume_price_detail') or volume_status}。"
    )
    strong_boundary = round(max(prev_close or current, open_price or current, holding.cost_price), 2)
    weak_reduce = round(max(holding.cost_price * 0.98, current * 0.97), 2)
    weak_exit = round(max(holding.cost_price * 0.96, current * 0.94), 2)
    next_day_script = [
        f"超预期：{_outperform_condition(category)} 动作：{_outperform_action(category)}",
        f"符合预期：{_expected_condition(category)} 动作：{_expected_action(category)}",
        f"弱于预期：{_underperform_condition(category)} 动作：{_underperform_action(category)}",
    ]
    risk_notes = _dynamic_risk_notes(evidence, holding, current)
    sell_trigger_cards = _dynamic_sell_trigger_cards(holding, evidence, quote, current)
    return {
        "board_level": "持仓动态预期",
        "industry": evidence.get("sector") or "",
        "concepts": [str(item) for item in (evidence.get("theme_tags") or [evidence.get("sector")]) if item],
        "overnight_order": False,
        "order_price": 0,
        "limit_up_price": _next_limit_up_price(current) if current else 0,
        "keep_order_condition": "持仓计划不使用隔夜买入，盘中只按强弱触发处理。",
        "cancel_condition": "不符合预期或板块证据转弱时取消加仓/买回动作。",
        "opening_confirmation": evidence.get("intraday") or "",
        "max_position_ratio": 0,
        "break_limit_action": "冲板失败、炸板无承接或放量回落，按弱于预期降风险。",
        "notes": evidence.get("volume_price_detail") or "",
        "board_strength": board_strength,
        "board_strength_detail": [board_strength, evidence.get("mainline_position") or "主线地位待确认"],
        "leader_support": leader_support,
        "limit_quality": limit_quality,
        "expectation_level": expectation_match,
        "strong_boundary_price": strong_boundary,
        "weak_reduce_price": weak_reduce,
        "weak_exit_price": weak_exit,
        "risk_notes": risk_notes,
        "intraday_status": evidence.get("intraday") or f"现价{current:.2f}，涨跌{change_pct:+.2f}%。",
        "expected_state": expected_state,
        "expectation_match": expectation_match,
        "operation_advice": operation_advice,
        "volume_price_status": volume_status,
        "next_day_script": next_day_script,
        "sell_trigger_cards": sell_trigger_cards,
        "refreshed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _dynamic_sell_trigger_cards(
    holding: Holding,
    evidence: dict[str, Any],
    quote: dict[str, Any],
    current: float,
) -> list[str]:
    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    change_pct = _safe_float(quote.get("change_pct"))
    if prev_close and high:
        high_change_pct = (high - prev_close) / prev_close * 100
        pullback = max(0, high_change_pct - change_pct)
    else:
        high_change_pct = 0
        pullback = 0
    estimated_vwap = _estimated_vwap(quote)
    below_vwap = bool(estimated_vwap and current < estimated_vwap)
    sector = str(evidence.get("sector") or "")
    triggers = _intraday_sell_triggers(
        holding=holding,
        current=current,
        high=high,
        high_change_pct=high_change_pct,
        change_pct=change_pct,
        pullback=pullback,
        below_vwap=below_vwap,
        sector=sector,
        sector_rank=0,
        sector_net=float(evidence.get("theme_flow_current") or 0),
        sector_main=0,
        sector_acc=0,
        sector_flow_peak=float(evidence.get("theme_flow_peak") or 0),
        sector_flow_current=float(evidence.get("theme_flow_current") or 0),
        sector_flow_pullback=float(evidence.get("theme_flow_pullback") or 0),
        sector_flow_pullback_pct=float(evidence.get("theme_flow_pullback_pct") or 0),
        strongest_name="其他强势方向",
        strongest_is_other=bool(evidence.get("is_underperforming")),
    )
    cards = [
        f"利润保护：{triggers['profit_protection_state']}",
        "板块退潮："
        + ("；".join(triggers["sector_ebb_trigger"]) if triggers["sector_ebb_trigger"] else "未触发，继续看板块资金排名和主线前排。"),
        "个股弱化："
        + ("；".join(triggers["stock_weakening_trigger"]) if triggers["stock_weakening_trigger"] else "未触发，继续看是否守住分时均价/VWAP。"),
        "利润回撤："
        + ("；".join(triggers["profit_drawdown_trigger"]) if triggers["profit_drawdown_trigger"] else "未触发，未到规则减仓阈值。"),
        "接回条件：" + "；".join(triggers["buyback_trigger"]),
    ]
    if triggers["trigger_action"]:
        cards.insert(0, f"动作建议：{triggers['trigger_action']}")
    return cards


def _expectation_match_label(evidence: dict[str, Any], category: str) -> str:
    volume_status = str(evidence.get("volume_price_status") or "")
    if evidence.get("super_expectation") or (evidence.get("strong_open") and "拉升" in volume_status):
        return "强更强"
    if evidence.get("intraday_repair"):
        return "弱转强"
    if evidence.get("weak_open") and evidence.get("high_reject"):
        return "弱转强失败"
    if evidence.get("high_reject") or evidence.get("is_underperforming") or "大跌" in volume_status:
        return "弱于预期"
    if category in {"超预期", "强预期"} and "放量" in volume_status:
        return "符合预期偏强"
    return "符合预期"


def _dynamic_operation_advice(label: str, category: str, holding: Holding, current: float) -> str:
    if label in {"强更强", "符合预期偏强"}:
        return "持有核心仓为主，不主动卖飞；高位天量或偏离5日线过远时不扩大仓位。"
    if label == "弱转强":
        return "先观察翻红后能否站稳分时均价/VWAP，确认后只允许按计划买回，不追第一笔。"
    if label == "弱转强失败":
        return f"反抽不过分时均价/VWAP先减仓，跌回{max(holding.cost_price * 0.98, current * 0.97):.2f}附近继续降风险。"
    if label == "弱于预期":
        return f"不幻想，跌破{max(holding.cost_price * 0.98, current * 0.97):.2f}减仓，跌破{max(holding.cost_price * 0.96, current * 0.94):.2f}清仓。"
    return "持有观察，不加仓；迟迟不能走强或板块无助攻时减一部分风险。"


def _leader_support_for_holding(holding: Holding, evidence: dict[str, Any]) -> list[str]:
    sector = str(evidence.get("sector") or "")
    supports: list[str] = []
    radar = _get_response_cache("theme-radar")
    if radar is not None:
        for theme in radar.themes[:20]:
            if sector and sector in theme.name:
                supports.extend(
                    f"{role.name}({role.code}) {role.role} 涨跌{role.change_pct:+.2f}% 成交{role.amount:.2f}亿：{role.reason}"
                    for role in theme.core_stocks[:6]
                )
                break
    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for cluster in ladder.clusters[:10]:
            if sector and (sector in cluster.name or holding.name in "、".join(cluster.stocks)):
                supports.append(f"{cluster.name}：{cluster.count}只涨停，最高{cluster.highest_level}板，前排{'、'.join(cluster.stocks[:6])}。")
    return list(dict.fromkeys(supports))[:8] or ["前后排助攻数据缺口：请刷新题材雷达/涨停天梯。"]


def _dynamic_risk_notes(evidence: dict[str, Any], holding: Holding, current: float) -> list[str]:
    notes: list[str] = []
    volume_ratio = _safe_float(evidence.get("volume_ratio"))
    ma5_deviation = _safe_float(evidence.get("ma5_deviation"))
    if volume_ratio >= 2:
        notes.append("高位/盘中放巨量：巨大分歧信号，继续转一致难度上升，只作为风险权重升高处理。")
    if ma5_deviation >= 8:
        notes.append(f"偏离5日线{ma5_deviation:.2f}%：禁止追高加仓，若开盘/竞价不能强势确认，按回踩均线风险处理。")
    if evidence.get("is_underperforming"):
        notes.append("现状弱于板块或资金流：个股独立行情持续性下降，涨停预期下调一级。")
    if evidence.get("leader_support_missing"):
        notes.append("前排/后排助攻不足：无梯队扩散时，次日必须个股强更强。")
    notes.append(f"弱于预期价格触发：跌破{max(holding.cost_price * 0.98, current * 0.97):.2f}减仓，跌破{max(holding.cost_price * 0.96, current * 0.94):.2f}清仓。")
    return list(dict.fromkeys(notes))


def _infer_expectation_category(
    holding: Holding,
    evidence: dict[str, Any] | None = None,
    quote: dict[str, Any] | None = None,
) -> str:
    evidence = evidence or {}
    base_category = _infer_holding_category(holding, evidence)
    if evidence.get("super_expectation"):
        return "超预期"
    if evidence.get("strong_open"):
        return "强预期"
    if evidence.get("intraday_repair"):
        return "弱转强"
    if evidence.get("high_reject") or base_category == "高位巨量分歧股":
        return "分歧转弱"
    if evidence.get("weak_open") or evidence.get("is_underperforming") or base_category in {"弱于预期股", "低价情绪股"}:
        return "弱于预期"
    return "符合预期"


def _infer_holding_category(holding: Holding, evidence: dict[str, Any] | None = None) -> str:
    evidence = evidence or {}
    text = f"{holding.position_type} {holding.next_discipline} {holding.name}"
    if "低价" in text or holding.current_price <= 5:
        return "低价情绪股"
    if evidence.get("is_high_divergence") or "高位" in text or "分歧" in text or "巨量" in text:
        return "高位巨量分歧股"
    if evidence.get("is_underperforming") or "退出" in text or "风险" in text or "亏损" in text or "弱于预期" in text:
        return "弱于预期股"
    if evidence.get("is_mainline_front") or "主线" in text or "龙头" in text or "前排" in text:
        return "主线前排股"
    return "震荡趋势股"


def _outperform_condition(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "高开后继续放量走强，回踩分时均价/VWAP不破，且强于板块。"
    if category == "弱转强":
        return "低开后快速翻红，重新站上分时均价/VWAP，板块同步修复。"
    if category == "主线前排股":
        return "板块继续强化，个股高开或快速站上确认位，放量突破且守住分时均价/VWAP"
    return "板块不退潮，个股站上确认位并放量突破，回踩分时均价/VWAP不破"


def _outperform_action(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "保留核心仓，冲高只按计划止盈，不因小波动做无意义高抛。"
    if category == "弱转强":
        return "先确认翻红后的承接，允许按计划买回已高抛部分，不能超过已卖股数。"
    if category == "主线前排股":
        return "继续持有核心仓，冲高只做分批止盈，不机械做T"
    if category in {"弱于预期", "分歧转弱", "弱于预期股", "高位巨量分歧股", "低价情绪股"}:
        return "冲高优先降风险，只处理卖出计划，不盲目加仓"
    return "按压力位分批高抛，买回必须等支撑承接确认"


def _expected_condition(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "高开或红盘震荡，有承接但未继续主动突破，仍站在关键确认位上方。"
    if category == "弱转强":
        return "翻红后围绕分时均价震荡，回落不破昨收或确认位。"
    return "板块未退潮，个股围绕确认位震荡，有承接但未主动突破"


def _expected_action(category: str) -> str:
    if category in {"符合预期", "弱转强", "震荡趋势股"}:
        return "允许按计划高抛低吸，买回必须等支撑缩量企稳"
    if category in {"强预期", "超预期", "主线前排股"}:
        return "保持核心仓，非关键压力位不做无意义高抛"
    return "以观察和减风险为主，不主动扩大仓位"


def _underperform_condition(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "高开后快速回落跌破分时均价/VWAP，无法重新收回确认位，明显弱于板块。"
    if category == "弱转强":
        return "翻红失败后再次跌回昨收/确认位下方，低点继续下移。"
    return "低开不修复、跌破确认位/减仓线、弱于板块、放量下跌或资金明显流出"


def _underperform_action(category: str) -> str:
    if category in {"强预期", "超预期", "主线前排股"}:
        return "跌破确认位先降仓，若板块同步退潮则退出非核心仓"
    return "先减仓或退出；反抽是卖出窗口，不默认接回"


def _plan_from_payload(payload: NextDayPlanCreate) -> NextDayPlan:
    plan = NextDayPlan(
        **payload.model_dump(
            exclude={
                "classification_basis",
                "forbidden_actions",
                "auction_plan",
                "market_value",
                "profit_amount",
                "profit_ratio",
                "price_source",
                "price_note",
            }
        ),
        classification_basis=payload.classification_basis.model_dump_json(),
        auction_plan=payload.auction_plan.model_dump_json(),
        forbidden_actions=json.dumps(payload.forbidden_actions, ensure_ascii=False),
    )
    if not plan.plan_date:
        plan.plan_date = _next_trade_date()
    _refresh_plan_risk(plan)
    return plan


def _refresh_plan_risk(plan: NextDayPlan) -> None:
    plan.risk_priority = _CATEGORY_RISK_PRIORITY.get(plan.holding_category, 9)
    if not plan.stop_loss_4pct and plan.cost_price:
        plan.stop_loss_4pct = round(plan.cost_price * 0.96, 2)
    warnings = _plan_warnings(plan)
    plan.risk_warnings = json.dumps(warnings, ensure_ascii=False)


def _plan_warnings(plan: NextDayPlan) -> list[str]:
    warnings: list[str] = []
    try:
        auction_plan = json.loads(plan.auction_plan or "{}")
    except json.JSONDecodeError:
        auction_plan = {}
    if plan.plan_type == "limit_up_auction":
        warnings.append("打板预案不是买入指令：9:20后封单和开盘承接不符合条件就撤单。")
        warnings.append("T+1风险：一旦炸板或高开低走，当日无法通过卖出纠错。")
    for item in auction_plan.get("risk_notes") or []:
        if item:
            warnings.append(str(item))
    if plan.trim_quantity > 0 and not plan.buyback_condition and plan.allow_buyback:
        warnings.append("没有买回条件却标记做T：本次高抛默认为减仓。")
    if plan.allow_buyback and plan.max_buyback_quantity > plan.trim_quantity:
        warnings.append("做T买回不能超过已卖出股数。")
    if plan.holding_category in {"弱于预期", "弱于预期股"} and plan.allow_buyback:
        warnings.append("弱于预期反抽优先减仓。")
    if plan.holding_category in {"分歧转弱", "高位巨量分歧股"} and plan.allow_buyback:
        warnings.append("分歧转弱需先缩量企稳，不做T扩大风险。")
    if plan.holding_category == "低价情绪股" and plan.allow_buyback:
        warnings.append("低价情绪股以退出为主。")
    if plan.trim_price and plan.buyback_price and plan.buyback_price > 0:
        spread = (plan.trim_price - plan.buyback_price) / plan.buyback_price
        if spread < 0.02:
            warnings.append("差价不足2%-3%：不值得做T。")
    return warnings


def _next_limit_up_price(price: float, ratio: str = "1.10") -> float:
    value = Decimal(str(price)) * Decimal(ratio)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _next_day_plan_out(plan: NextDayPlan, price_note: str = "") -> NextDayPlanOut:
    market_value = plan.quantity * plan.current_price
    profit_amount = (plan.current_price - plan.cost_price) * plan.quantity
    profit_ratio = (
        (plan.current_price - plan.cost_price) / plan.cost_price
        if plan.cost_price
        else 0
    )
    is_realtime = _is_realtime_note(price_note)
    return NextDayPlanOut(
        id=plan.id,
        plan_date=plan.plan_date,
        plan_type=plan.plan_type,
        holding_id=plan.holding_id,
        code=plan.code,
        name=plan.name,
        quantity=plan.quantity,
        cost_price=plan.cost_price,
        current_price=plan.current_price,
        market_value=round(market_value, 2),
        profit_amount=round(profit_amount, 2),
        profit_ratio=round(profit_ratio, 4),
        price_source="realtime" if is_realtime else "manual",
        price_note=price_note,
        position_ratio=plan.position_ratio,
        holding_category=plan.holding_category,
        risk_priority=plan.risk_priority,
        classification_basis=ClassificationBasis(**_json_obj(plan.classification_basis)),
        outperform_condition=plan.outperform_condition,
        outperform_action=plan.outperform_action,
        expected_condition=plan.expected_condition,
        expected_action=plan.expected_action,
        underperform_condition=plan.underperform_condition,
        underperform_action=plan.underperform_action,
        confirm_price=plan.confirm_price,
        trim_price=plan.trim_price,
        trim_condition=plan.trim_condition,
        trim_quantity=plan.trim_quantity,
        allow_buyback=plan.allow_buyback,
        buyback_price=plan.buyback_price,
        buyback_condition=plan.buyback_condition,
        max_buyback_quantity=plan.max_buyback_quantity,
        reduce_price=plan.reduce_price,
        final_risk_price=plan.final_risk_price,
        stop_loss_4pct=plan.stop_loss_4pct,
        limit_up_price=plan.limit_up_price,
        auction_plan=_json_obj(plan.auction_plan),
        forbidden_actions=_json_list(plan.forbidden_actions),
        risk_warnings=_json_list(plan.risk_warnings),
        review_expectation=plan.review_expectation,
        review_execution=plan.review_execution,
        review_deviation=plan.review_deviation,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _json_obj(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _json_list(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except Exception:
        return []


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "-":
            return 0
        return float(value)
    except Exception:
        return 0


def _safe_turnover(value: Any) -> float | None:
    raw = _safe_float(value)
    if raw <= 0:
        return None
    turnover = raw * 100 if 0 < raw < 1 else raw
    if turnover > 120:
        return None
    return round(turnover, 2)
