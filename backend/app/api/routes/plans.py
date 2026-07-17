import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.models.trading import Holding, NextDayPlan, ExitCard
from app.schemas.trading import (
    NextDayPlanOut,
    NextDayPlanCreate,
    LimitUpPlanCreate,
    NextDayPlanUpdate,
    NextDayPlanReview,
    ExitCardOut,
    ExitCardCreate,
    SellPlanOut
)
from app.api.helpers.quotes import _latest_quote_for_holding, _json_obj
from app.api.helpers.holdings_calc import _account_total_asset, _refresh_holding_prices
from app.api.helpers.plan_calc import (
    _next_trade_date,
    _refresh_existing_holding_plans,
    _next_day_plan_out,
    _plan_from_payload,
    _default_next_day_plan,
    _sync_holding_plan,
    _limit_up_next_day_plan,
    _refresh_plan_risk,
    refresh_limit_expectation_stage,
)
from app.api.helpers.seesaw import _sell_plan

router = APIRouter()


_LIMIT_UP_SYSTEM_AUCTION_FIELDS = {
    "mainline_name",
    "mainline_rank",
    "mainline_score",
    "mainline_level",
    "is_mainline",
    "theme_stage",
    "theme_stage_reason",
    "identity_roles",
    "identity_action",
    "position_rule",
    "theme_evidence",
}


def _guard_limit_up_auction_update(existing_raw: str, incoming: dict) -> dict:
    """Keep the system's theme judgement and position ceiling authoritative."""
    existing = _json_obj(existing_raw)
    merged = {**existing, **incoming}

    for field in _LIMIT_UP_SYSTEM_AUCTION_FIELDS:
        if field in existing:
            merged[field] = existing[field]
        else:
            # Old plans without system evidence must remain fail-closed; clients
            # cannot manufacture mainline/stage/identity evidence themselves.
            merged.pop(field, None)

    try:
        system_cap = max(0.0, float(existing.get("max_position_ratio") or 0))
    except (TypeError, ValueError):
        system_cap = 0.0
    try:
        requested_cap = max(0.0, float(incoming.get("max_position_ratio", system_cap) or 0))
    except (TypeError, ValueError):
        requested_cap = 0.0
    effective_cap = min(system_cap, requested_cap)
    stage = str(existing.get("theme_stage") or "数据不足")
    has_identity = bool(existing.get("identity_roles"))
    system_eligible = (
        existing.get("is_mainline") is True
        and stage not in {"高潮", "退潮", "数据不足"}
        and has_identity
        and effective_cap > 0
    )
    merged["max_position_ratio"] = effective_cap if system_eligible else 0.0
    if not system_eligible:
        merged["overnight_order"] = False
    return merged

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
        quote = _latest_quote_for_holding(holding)
        change_pct = float(quote.get("change_pct") or 0)
        if change_pct >= 9.5:
            for stale in db.query(NextDayPlan).filter(
                NextDayPlan.plan_date == plan_date, NextDayPlan.code == holding.code, NextDayPlan.plan_type == "holding",
            ).all():
                db.delete(stale)
            existing_limit = db.query(NextDayPlan).filter(
                NextDayPlan.plan_date == plan_date, NextDayPlan.code == holding.code, NextDayPlan.plan_type == "limit_up_auction",
            ).first()
            payload = LimitUpPlanCreate(
                code=holding.code, name=holding.name, price=float(quote.get("price") or holding.current_price or 0),
                level=max(1, int(quote.get("consecutive_limit_days") or 1)), industry=str(quote.get("industry") or ""),
                concepts=list(quote.get("concepts") or []), sealed_amount=float(quote.get("sealed_amount") or 0),
                amount=float(quote.get("amount") or 0), turnover=float(quote.get("turnover") or 0),
                break_count=int(quote.get("break_count") or 0), expectation="持仓股当日涨停，自动进入打板预期验证",
            )
            plan = _limit_up_next_day_plan(payload, plan_date, existing_limit)
            if existing_limit is None:
                db.add(plan)
            plans.append(plan)
            continue
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
        auction_plan = data.pop("auction_plan")
        if plan.plan_type == "limit_up_auction":
            auction_plan = _guard_limit_up_auction_update(plan.auction_plan, auction_plan)
        plan.auction_plan = json.dumps(auction_plan, ensure_ascii=False)
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

@router.post("/next-day-plans/{plan_id}/stage-refresh", response_model=NextDayPlanOut)
def refresh_next_day_plan_stage(plan_id: int, db: Session = Depends(get_db)) -> NextDayPlanOut:
    plan = db.get(NextDayPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="next day plan not found")
    return refresh_limit_expectation_stage(plan, db)

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
