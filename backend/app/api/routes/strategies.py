import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.trading import StrategyTemplate
from app.schemas.trading import StrategyTemplateIn, StrategyTemplateOut

router = APIRouter()

DEFAULT_STRATEGIES = [
    ("FIRST_LIMIT", "首板打板", "limit"), ("ONE_TO_TWO", "一进二", "limit"),
    ("TWO_TO_THREE", "二进三", "limit"), ("HIGH_LEADER", "高标接力", "limit"),
    ("WEAK_TO_STRONG", "弱转强", "reversal"), ("DIVERGENCE_RESEAL", "分歧回封", "limit"),
    ("TREND_BREAKOUT", "趋势突破", "trend"), ("TREND_PULLBACK", "趋势低吸", "trend"),
    ("CAPACITY_CORE", "容量核心", "trend"), ("LEADER_REVERSAL", "龙头断板反包", "reversal"),
    ("SECTOR_REPAIR", "板块修复", "reversal"), ("HOLDING_T", "持仓做T", "holding"),
]

def _loads(raw: str) -> list[str]:
    try:
        value = json.loads(raw or "[]")
        return [str(item) for item in value] if isinstance(value, list) else []
    except Exception:
        return []

def _out(row: StrategyTemplate) -> StrategyTemplateOut:
    return StrategyTemplateOut(
        id=row.id, code=row.code, name=row.name, category=row.category,
        market_environment=_loads(row.market_environment_json), prerequisites=_loads(row.prerequisites_json),
        premarket_expectation=_loads(row.premarket_expectation_json), auction_conditions=_loads(row.auction_conditions_json),
        volume_price_conditions=_loads(row.volume_price_conditions_json), buy_confirmation=_loads(row.buy_confirmation_json),
        position_limit=row.position_limit, structure_stop=_loads(row.structure_stop_json),
        invalid_conditions=_loads(row.invalid_conditions_json), holding_management=_loads(row.holding_management_json),
        forbidden_actions=_loads(row.forbidden_actions_json), enabled=row.enabled, version=row.version,
        created_at=row.created_at, updated_at=row.updated_at,
    )

def _ensure_defaults(db: Session) -> None:
    existing = {row.code for row in db.query(StrategyTemplate).all()}
    for code, name, category in DEFAULT_STRATEGIES:
        if code in existing:
            continue
        db.add(StrategyTemplate(
            code=code, name=name, category=category,
            market_environment_json=json.dumps(["市场环境允许该模式"], ensure_ascii=False),
            prerequisites_json=json.dumps(["数据质量合格", "属于主线或前排"], ensure_ascii=False),
            premarket_expectation_json=json.dumps(["先定义合理预期区间"], ensure_ascii=False),
            auction_conditions_json=json.dumps(["竞价不得严重低于预期"], ensure_ascii=False),
            volume_price_conditions_json=json.dumps(["真实分钟VWAP与成交额确认"], ensure_ascii=False),
            buy_confirmation_json=json.dumps(["风险收益比达标后确认"], ensure_ascii=False),
            position_limit=0.2,
            structure_stop_json=json.dumps(["采用与剧本一致的结构失效位"], ensure_ascii=False),
            invalid_conditions_json=json.dumps(["预期证伪", "板块订单流方向估算持续转弱"], ensure_ascii=False),
            holding_management_json=json.dumps(["按证据状态机持有或减仓"], ensure_ascii=False),
            forbidden_actions_json=json.dumps(["禁止亏损补仓", "禁止数据不足时执行"], ensure_ascii=False),
        ))
    db.commit()

@router.get("/strategies/templates", response_model=list[StrategyTemplateOut])
def list_strategy_templates(db: Session = Depends(get_db)) -> list[StrategyTemplateOut]:
    _ensure_defaults(db)
    return [_out(row) for row in db.query(StrategyTemplate).order_by(StrategyTemplate.category, StrategyTemplate.id).all()]

@router.post("/strategies/templates", response_model=StrategyTemplateOut)
def create_strategy_template(payload: StrategyTemplateIn, db: Session = Depends(get_db)) -> StrategyTemplateOut:
    if db.query(StrategyTemplate).filter(StrategyTemplate.code == payload.code).first():
        raise HTTPException(status_code=409, detail="strategy code already exists")
    row = StrategyTemplate(code=payload.code, name=payload.name)
    db.add(row)
    return _save(row, payload, db, increment=False)

@router.put("/strategies/templates/{template_id}", response_model=StrategyTemplateOut)
def update_strategy_template(template_id: int, payload: StrategyTemplateIn, db: Session = Depends(get_db)) -> StrategyTemplateOut:
    row = db.get(StrategyTemplate, template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="strategy template not found")
    return _save(row, payload, db, increment=True)

def _save(row: StrategyTemplate, payload: StrategyTemplateIn, db: Session, increment: bool) -> StrategyTemplateOut:
    row.code, row.name, row.category = payload.code, payload.name, payload.category
    for field in ("market_environment", "prerequisites", "premarket_expectation", "auction_conditions", "volume_price_conditions", "buy_confirmation", "structure_stop", "invalid_conditions", "holding_management", "forbidden_actions"):
        setattr(row, f"{field}_json", json.dumps(getattr(payload, field), ensure_ascii=False))
    row.position_limit, row.enabled = payload.position_limit, payload.enabled
    if increment:
        row.version += 1
    db.commit(); db.refresh(row)
    return _out(row)
