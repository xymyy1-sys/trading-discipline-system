from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.limiter import limiter
from app.services.ai_analysis import generate_analysis, latest_analysis
from app.services.ai_position_qa import generate_position_answer
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


class PositionQaIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    force: bool = False


class PositionQaOut(BaseModel):
    id: int
    code: str
    question: str
    model: str
    content: str
    status: str
    cached: bool
    context_as_of: str
    missing_fields: list[str]
    updated_at: datetime


@router.get("/ai/status")
def ai_status() -> dict[str, object]:
    settings = get_settings()
    return {"configured": bool(settings.ai_api_key), "model": settings.ai_model, "provider": settings.ai_provider}


@router.get("/ai/analysis/{scope}/{target}", response_model=AiAnalysisOut | None)
def get_ai_analysis(scope: str, target: str, db: Session = Depends(get_db)):
    return latest_analysis(db, scope, target)


@router.post("/ai/analysis/{scope}/{target}", response_model=AiAnalysisOut)
def post_ai_analysis(scope: str, target: str, force: bool = Query(False), db: Session = Depends(get_db)):
    try:
        return generate_analysis(db, scope, target, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"大模型调用失败：{exc.__class__.__name__}") from exc


@router.post("/ai/position-qa/{code}", response_model=PositionQaOut)
@limiter.limit("10/minute")
def post_position_qa(request: Request, code: str, payload: PositionQaIn, db: Session = Depends(get_db)) -> PositionQaOut:
    """Answer one holding question from a timestamped, source-labelled context pack."""
    try:
        result = generate_position_answer(
            db,
            code,
            payload.question,
            force=payload.force,
        )
        row = result.row
        return PositionQaOut(
            id=row.id,
            code=str(code).strip().zfill(6),
            question=result.question,
            model=row.model,
            content=row.content,
            status=row.status,
            cached=result.cached,
            context_as_of=result.context_as_of,
            missing_fields=result.missing_fields,
            updated_at=row.updated_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"持仓AI问答失败：{exc.__class__.__name__}") from exc


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
