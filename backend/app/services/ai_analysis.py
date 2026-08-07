from __future__ import annotations

import hashlib
import json
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from app.api.helpers.decision import decision_card
from app.core.config import get_settings
from app.models.trading import AiAnalysisCache, ExpectationSnapshot, Holding
from app.services.action_recommendations import active_current_recommendations


DETAILED_PROMPT_VERSION = "evidence-review-v2"
COMPACT_PROMPT_VERSION = "dingtalk-compact-v1"
SYSTEM_INSTRUCTIONS = """你是A股交易决策系统中的证据审查助手。只依据输入数据分析，不虚构行情、新闻或资金数据。
必须使用中文，明确区分事实、推断、缺失数据和失效条件。输出：核心结论、预期差判断、支持证据、反向证据、关键价位与触发条件、执行纪律、数据缺口。
不要承诺收益，不要把推断写成事实，不替用户自动下单。"""
COMPACT_SYSTEM_INSTRUCTIONS = """你是A股交易决策系统的简报助手。只依据输入数据，不虚构事实。
请输出适合钉钉阅读的极简中文摘要，总字数不超过500字、最多6条：
1. 一句核心结论；2. 当前持仓必须处理的风险（没有则写“暂无”）；3. 最多三条立即动作；4. 一条关键数据缺口。
不得复述原始字段名，不得罗列历史警报，不得重复同一标的，不承诺收益，不自动下单。"""


def _context(db: Session, scope: str, target: str) -> dict:
    if scope == "stock":
        return {
            "scope": "个股",
            "decision_card": decision_card(db, target).model_dump(mode="json"),
        }
    if scope == "market":
        holdings = (
            db.query(Holding)
            .filter(Holding.quantity > 0)
            .order_by(Holding.updated_at.desc())
            .all()
        )
        holding_codes = {str(row.code).zfill(6) for row in holdings}
        expectations: dict[str, dict] = {}
        if holding_codes:
            rows = (
                db.query(ExpectationSnapshot)
                .filter(ExpectationSnapshot.code.in_(holding_codes))
                .order_by(ExpectationSnapshot.created_at.desc())
                .all()
            )
            for row in rows:
                code = str(row.code).zfill(6)
                expectations.setdefault(
                    code,
                    {
                        "name": row.name,
                        "stage": row.stage,
                        "base": row.base_expectation,
                        "result": row.expectation_result,
                        "gap": row.expectation_gap_score,
                        "transition": row.state_transition,
                        "suggestion": row.suggestion,
                    },
                )
        alerts = active_current_recommendations(db, limit=20)
        return {
            "scope": "全市场与当前持仓",
            "holdings": [
                {
                    "code": str(row.code).zfill(6),
                    "name": row.name,
                    "quantity": row.quantity,
                    "cost": row.cost_price,
                    "price": row.current_price,
                    "type": row.position_type,
                }
                for row in holdings
            ],
            "expectations": expectations,
            "active_alerts": [
                {
                    "code": str(row.code).zfill(6),
                    "name": row.name,
                    "level": row.level,
                    "state": row.state,
                    "action": row.action,
                }
                for row in alerts
            ],
        }
    raise ValueError("不支持的AI分析范围")


def _output_text(payload: dict) -> str:
    choices = payload.get("choices") or []
    if choices:
        return str((choices[0].get("message") or {}).get("content") or "").strip()
    return ""


def latest_analysis(db: Session, scope: str, target: str) -> AiAnalysisCache | None:
    return (
        db.query(AiAnalysisCache)
        .filter(AiAnalysisCache.scope == scope, AiAnalysisCache.target == target)
        .order_by(AiAnalysisCache.updated_at.desc())
        .first()
    )


def generate_analysis(
    db: Session,
    scope: str,
    target: str,
    force: bool = False,
) -> AiAnalysisCache:
    settings = get_settings()
    compact = scope == "market" and target.endswith("-dingtalk")
    prompt_version = COMPACT_PROMPT_VERSION if compact else DETAILED_PROMPT_VERSION
    system_prompt = COMPACT_SYSTEM_INSTRUCTIONS if compact else SYSTEM_INSTRUCTIONS
    context = _context(db, scope, target)
    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, default=str)
    hash_material = json.dumps(
        {
            "context": context,
            "model": settings.ai_model,
            "prompt_version": prompt_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    input_hash = hashlib.sha256(hash_material.encode("utf-8")).hexdigest()
    cached = latest_analysis(db, scope, target)
    if cached and cached.input_hash == input_hash and not force and cached.status == "completed":
        return cached
    if not settings.ai_api_key:
        raise RuntimeError("尚未配置 AI_API_KEY")
    user_prompt = (
        f"请根据以下系统证据生成今日极简简报：\n{serialized}"
        if compact
        else f"请审查以下交易证据并形成可执行但审慎的分析：\n{serialized}"
    )
    response = httpx.post(
        f"{settings.ai_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings.ai_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        },
        timeout=150,
    )
    response.raise_for_status()
    content = _output_text(response.json())
    if not content:
        raise RuntimeError("AI 返回为空")
    row = cached or AiAnalysisCache(scope=scope, target=target)
    row.model = settings.ai_model
    row.input_hash = input_hash
    row.content = content
    row.status = "completed"
    row.error_message = ""
    row.updated_at = datetime.now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
