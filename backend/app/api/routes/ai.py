from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.services.ai_analysis import generate_analysis, latest_analysis
from app.services.dingtalk import dingtalk_status, send_dingtalk_markdown

router = APIRouter()


class AiAnalysisOut(BaseModel):
    id: int
    scope: str
    target: str
    model: str
    content: str
    status: str
    updated_at: datetime


@router.get("/ai/status")
def ai_status() -> dict[str, object]:
    settings = get_settings()
    return {"configured": bool(settings.openai_api_key), "model": settings.openai_model}


@router.get("/ai/analysis/{scope}/{target}", response_model=AiAnalysisOut | None)
def get_ai_analysis(scope: str, target: str, db: Session = Depends(get_db)):
    return latest_analysis(db, scope, target)


@router.post("/ai/analysis/{scope}/{target}", response_model=AiAnalysisOut)
def post_ai_analysis(scope: str, target: str, force: bool = Query(False), db: Session = Depends(get_db)):
    try:
        return generate_analysis(db, scope, target, force=force)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"大模型调用失败：{exc.__class__.__name__}") from exc


@router.get("/notifications/dingtalk/status")
def get_dingtalk_status() -> dict[str, object]:
    return dingtalk_status()


@router.post("/notifications/dingtalk/test")
def test_dingtalk() -> dict[str, object]:
    try:
        send_dingtalk_markdown("知行交易驾驶舱测试", "### 钉钉通知关联成功\n\n系统已可以向本群推送风险事件和决策提醒。")
        return {"ok": True}
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
