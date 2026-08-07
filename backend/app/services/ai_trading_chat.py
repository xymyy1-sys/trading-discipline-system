from __future__ import annotations

import hashlib
import json
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.trading import (
    AiAnalysisCache,
    DataCaptureSnapshot,
    Holding,
    SimulationAccount,
    SimulationDailyEquity,
    SimulationFill,
    SimulationOrder,
    SimulationPosition,
    SimulationShadowDecision,
)
from app.services.simulation_shadow import AI_TRADER_AUTOMATION_KEY


SCOPE = "dingtalk-free-question"
PROMPT_VERSION = "free-evidence-qa-v1"
SYSTEM = """你是知行交易驾驶舱的群内证据问答助手。回答用户实际提出的问题，不要求固定命令。
只使用输入的系统数据；不知道就明确说缺什么，禁止虚构行情、成交、新闻或资金。
区分真实持仓与AI模拟账户。真实持仓金额、数量、成本和盈亏属于隐私，不得在群内披露；模拟成交可以说明。
优先直接回答，再列最多3条依据和1条后续验证条件。中文、不超过700字、不承诺收益、不声称自动实盘下单。"""


def _context(db: Session) -> dict:
    holdings = db.query(Holding).filter(Holding.quantity > 0).order_by(Holding.updated_at.desc()).all()
    account = db.query(SimulationAccount).filter(
        SimulationAccount.automation_key == AI_TRADER_AUTOMATION_KEY,
    ).first()
    if account is None:
        return {"real_holding_names": [row.name for row in holdings], "simulation": None}
    positions = db.query(SimulationPosition).filter(
        SimulationPosition.account_id == account.id,
        SimulationPosition.quantity > 0,
    ).order_by(SimulationPosition.updated_at.desc()).all()
    equities = db.query(SimulationDailyEquity).filter(
        SimulationDailyEquity.account_id == account.id,
    ).order_by(SimulationDailyEquity.trade_date.desc()).limit(5).all()
    fills = db.query(SimulationFill).filter(
        SimulationFill.account_id == account.id,
    ).order_by(SimulationFill.filled_at.desc(), SimulationFill.id.desc()).limit(30).all()
    fill_rows = []
    for fill in fills:
        order = db.get(SimulationOrder, fill.order_id)
        decision = db.query(SimulationShadowDecision).filter(
            SimulationShadowDecision.order_id == fill.order_id,
        ).first()
        fill_rows.append({
            "time": fill.filled_at,
            "code": fill.code,
            "name": fill.name,
            "side": fill.side,
            "price": fill.price,
            "quantity": fill.quantity,
            "strategy": fill.strategy_source,
            "reason": getattr(decision, "reason", "") or getattr(order, "client_note", ""),
        })
    learning = db.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.data_type == "ai_daily_learning",
        DataCaptureSnapshot.target_code == f"account:{account.id}",
    ).order_by(DataCaptureSnapshot.trade_date.desc(), DataCaptureSnapshot.id.desc()).limit(3).all()
    return {
        "real_holding_names_only": [row.name for row in holdings],
        "privacy_note": "真实持仓仅提供名称，金额/数量/成本/盈亏禁止群内披露",
        "simulation_account": {
            "initial_cash": account.initial_cash,
            "cash": account.cash,
            "positions": [
                {
                    "code": row.code,
                    "name": row.name,
                    "quantity": row.quantity,
                    "average_cost": row.average_cost,
                    "market_price": row.market_price,
                    "unrealized_pnl": row.unrealized_pnl,
                }
                for row in positions
            ],
            "recent_equity": [
                {
                    "date": row.trade_date,
                    "equity": row.total_equity,
                    "daily_pnl": row.daily_pnl,
                    "return_pct": row.return_pct,
                    "drawdown_pct": row.drawdown_pct,
                }
                for row in equities
            ],
            "recent_fills": fill_rows,
            "recent_learning": [json.loads(row.normalized_value_json or "{}") for row in learning],
        },
    }


def answer_trading_question(db: Session, question: str) -> str:
    cleaned = " ".join(str(question or "").split())
    if not cleaned:
        raise ValueError("问题不能为空")
    if len(cleaned) > 500:
        raise ValueError("问题不能超过500个字符")
    settings = get_settings()
    context = _context(db)
    material = json.dumps(
        {"question": cleaned, "context": context, "model": settings.ai_model, "prompt": PROMPT_VERSION},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    input_hash = hashlib.sha256(material.encode("utf-8")).hexdigest()
    target = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:24]
    cached = db.query(AiAnalysisCache).filter(
        AiAnalysisCache.scope == SCOPE,
        AiAnalysisCache.target == target,
        AiAnalysisCache.input_hash == input_hash,
        AiAnalysisCache.status == "completed",
    ).order_by(AiAnalysisCache.updated_at.desc()).first()
    if cached:
        return cached.content
    if not settings.ai_api_key:
        raise RuntimeError("尚未配置AI模型")
    response = httpx.post(
        f"{settings.ai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.ai_model,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"用户问题：{cleaned}\n\n系统证据：{json.dumps(context, ensure_ascii=False, default=str)}"},
            ],
            "stream": False,
        },
        timeout=150,
    )
    response.raise_for_status()
    choices = response.json().get("choices") or []
    content = str(((choices[0].get("message") or {}).get("content") if choices else "") or "").strip()
    if not content:
        raise RuntimeError("AI返回为空")
    row = AiAnalysisCache(
        scope=SCOPE,
        target=target,
        model=settings.ai_model,
        input_hash=input_hash,
        content=content,
        status="completed",
        error_message="",
        updated_at=datetime.now(),
    )
    db.add(row)
    db.commit()
    return content
