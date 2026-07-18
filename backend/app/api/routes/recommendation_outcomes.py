from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.trading import RecommendationOutcome
from app.schemas.trading import RecommendationOutcomeOut, RecommendationOutcomeSummaryOut
from app.services.recommendation_outcomes import (
    recommendation_outcome_summary,
    refresh_recommendation_outcomes,
)


router = APIRouter()


def _missing_horizons(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _out(row: RecommendationOutcome) -> RecommendationOutcomeOut:
    return RecommendationOutcomeOut(
        id=row.id,
        source_key=row.source_key,
        recommendation_id=row.recommendation_id,
        recommendation_revision_id=row.recommendation_revision_id,
        trade_date=row.trade_date,
        code=row.code,
        name=row.name,
        signal_at=row.signal_at,
        level=row.level,
        state=row.state,
        action=row.action,
        recommended_ratio=row.recommended_ratio,
        reference_snapshot_id=row.reference_snapshot_id,
        reference_at=row.reference_at,
        reference_latency_seconds=row.reference_latency_seconds,
        reference_price=row.reference_price,
        reference_source=row.reference_source,
        reference_quality=row.reference_quality,
        price_5m=row.price_5m,
        return_5m_pct=row.return_5m_pct,
        price_15m=row.price_15m,
        return_15m_pct=row.return_15m_pct,
        price_30m=row.price_30m,
        return_30m_pct=row.return_30m_pct,
        close_price=row.close_price,
        return_close_pct=row.return_close_pct,
        next_trade_date=row.next_trade_date,
        next_open_price=row.next_open_price,
        return_next_open_pct=row.return_next_open_pct,
        next_close_price=row.next_close_price,
        return_next_close_pct=row.return_next_close_pct,
        mfe_pct=row.mfe_pct,
        mae_pct=row.mae_pct,
        status=row.status,
        data_quality=row.data_quality,
        invalid_reason=row.invalid_reason,
        missing_horizons=_missing_horizons(row.missing_horizons_json),
        evaluated_through_at=row.evaluated_through_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/reviews/recommendation-outcomes", response_model=list[RecommendationOutcomeOut])
def list_recommendation_outcomes(
    status: str | None = Query(default=None, pattern="^(pending|partial|complete|invalid)$"),
    code: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[RecommendationOutcomeOut]:
    query = db.query(RecommendationOutcome)
    if status:
        query = query.filter(RecommendationOutcome.status == status)
    if code:
        normalized = "".join(char for char in code if char.isdigit()).zfill(6)
        query = query.filter(RecommendationOutcome.code.in_([code.strip(), normalized, normalized.lstrip("0")]))
    rows = query.order_by(
        RecommendationOutcome.signal_at.desc(),
        RecommendationOutcome.id.desc(),
    ).limit(limit).all()
    return [_out(row) for row in rows]


@router.get("/reviews/recommendation-outcomes/summary", response_model=RecommendationOutcomeSummaryOut)
def get_recommendation_outcome_summary(
    db: Session = Depends(get_db),
) -> RecommendationOutcomeSummaryOut:
    return RecommendationOutcomeSummaryOut(**recommendation_outcome_summary(db))


@router.post("/reviews/recommendation-outcomes/refresh")
def refresh_recommendation_outcome_ledger(
    limit: int = Query(default=250, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    return refresh_recommendation_outcomes(db, limit=limit)
