from __future__ import annotations

import hashlib
import json
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.api.helpers.decision import decision_card
from app.core.config import get_settings
from app.models.trading import ActionRecommendation, AiAnalysisCache, ExpectationSnapshot, Holding


SYSTEM_INSTRUCTIONS = """你是A股交易决策系统中的证据审查助手。只依据输入数据分析，不虚构行情、新闻或资金数据。
必须使用中文，明确区分事实、推断、缺失数据和失效条件。输出以下小节：核心结论、预期差判断、支持证据、反向证据、关键价位与触发条件、执行纪律、数据缺口。
不要承诺收益，不要把推断写成事实，不替用户自动下单。"""


def _context(db: Session, scope: str, target: str) -> dict:
    if scope == "stock":
        return {"scope": "个股", "decision_card": decision_card(db, target).model_dump(mode="json")}
    if scope == "market":
        holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
        expectations = {}
        for row in db.query(ExpectationSnapshot).order_by(ExpectationSnapshot.created_at.desc()).all():
            expectations.setdefault(row.code, {
                "name": row.name, "stage": row.stage, "base": row.base_expectation,
                "result": row.expectation_result, "gap": row.expectation_gap_score,
                "transition": row.state_transition, "suggestion": row.suggestion,
            })
        alerts = db.query(ActionRecommendation).filter(ActionRecommendation.acknowledged_at.is_(None)).order_by(ActionRecommendation.created_at.desc()).limit(20).all()
        return {
            "scope": "全市场与持仓",
            "holdings": [{"code": h.code, "name": h.name, "quantity": h.quantity, "cost": h.cost_price, "price": h.current_price, "type": h.position_type} for h in holdings],
            "expectations": expectations,
            "active_alerts": [{"code": a.code, "name": a.name, "level": a.level, "state": a.state, "action": a.action} for a in alerts],
        }
    raise ValueError("不支持的AI分析范围")


def _output_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if choices:
        return str((choices[0].get("message") or {}).get("content") or "").strip()
    return ""


def latest_analysis(db: Session, scope: str, target: str) -> AiAnalysisCache | None:
    return db.query(AiAnalysisCache).filter(AiAnalysisCache.scope == scope, AiAnalysisCache.target == target).order_by(AiAnalysisCache.updated_at.desc()).first()


def generate_analysis(db: Session, scope: str, target: str, force: bool = False) -> AiAnalysisCache:
    settings = get_settings()
    context = _context(db, scope, target)
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    input_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    cached = latest_analysis(db, scope, target)
    if cached and cached.input_hash == input_hash and not force and cached.status == "completed":
        return cached
    if not settings.ai_api_key:
        raise RuntimeError("尚未配置 AI_API_KEY")
    response = httpx.post(
        f"{settings.ai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.ai_api_key}", "Content-Type": "application/json"},
        json={
            "model": settings.ai_model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": f"请审查以下交易证据并形成可执行但审慎的分析：\n{serialized}"},
            ],
            "stream": False,
        },
        timeout=150,
    )
    response.raise_for_status()
    content = _output_text(response.json())
    if not content:
        raise RuntimeError("OpenAI 返回为空")
    row = cached or AiAnalysisCache(scope=scope, target=target)
    row.model = settings.ai_model
    row.input_hash = input_hash
    row.content = content
    row.status = "completed"
    row.error_message = ""
    row.updated_at = datetime.now()
    db.add(row); db.commit(); db.refresh(row)
    return row
