import json
from datetime import datetime, timezone

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

def _default_payload(code: str, name: str, category: str) -> StrategyTemplateIn:
    return StrategyTemplateIn(
        code=code,
        name=name,
        category=category,
        market_environment=["市场环境允许该模式"],
        prerequisites=["数据质量合格", "属于主线或前排"],
        premarket_expectation=["先定义合理预期区间"],
        auction_conditions=["竞价不得严重低于预期"],
        volume_price_conditions=["真实分钟VWAP与成交额确认"],
        buy_confirmation=["风险收益比达标后确认"],
        position_limit=0.2,
        structure_stop=["采用与剧本一致的结构失效位"],
        invalid_conditions=["预期证伪", "板块订单流方向估算持续转弱"],
        holding_management=["按证据状态机持有或减仓"],
        forbidden_actions=["禁止亏损补仓", "禁止数据不足时执行"],
    )


def _transient_default(index: int, payload: StrategyTemplateIn) -> StrategyTemplateOut:
    """Expose a usable built-in draft without inserting it during a GET.

    Negative ids are stable UI handles.  Saving one through the explicit PUT
    endpoint materialises the corresponding row and returns its real id.
    """
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return StrategyTemplateOut(
        **payload.model_dump(),
        id=-(index + 1),
        version=0,
        created_at=epoch,
        updated_at=epoch,
    )

@router.get("/strategies/templates", response_model=list[StrategyTemplateOut])
def list_strategy_templates(db: Session = Depends(get_db)) -> list[StrategyTemplateOut]:
    persisted = db.query(StrategyTemplate).order_by(StrategyTemplate.category, StrategyTemplate.id).all()
    persisted_codes = {row.code for row in persisted}
    defaults = [
        _transient_default(index, _default_payload(code, name, category))
        for index, (code, name, category) in enumerate(DEFAULT_STRATEGIES)
        if code not in persisted_codes
    ]
    return [*[_out(row) for row in persisted], *defaults]

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
    if row is None and template_id < 0:
        index = -template_id - 1
        if index < 0 or index >= len(DEFAULT_STRATEGIES):
            raise HTTPException(status_code=404, detail="strategy template not found")
        expected_code, _, _ = DEFAULT_STRATEGIES[index]
        if payload.code != expected_code:
            raise HTTPException(status_code=409, detail="transient strategy handle does not match payload")
        existing = db.query(StrategyTemplate).filter(StrategyTemplate.code == payload.code).first()
        if existing is not None:
            return _save(existing, payload, db, increment=True)
        row = StrategyTemplate(code=payload.code, name=payload.name)
        db.add(row)
        return _save(row, payload, db, increment=False)
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
