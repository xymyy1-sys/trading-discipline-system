from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.api.helpers.quotes import _normalize_code
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import ActionRecommendation, Holding


def current_holding_identity(db: Session) -> tuple[set[int], set[str]]:
    rows = db.query(Holding.id, Holding.code).filter(Holding.quantity > 0).all()
    return (
        {int(row_id) for row_id, _ in rows if row_id is not None},
        {_normalize_code(str(code)) for _, code in rows if code},
    )


def recommendation_belongs_to_current_holding(
    row: ActionRecommendation,
    *,
    holding_ids: set[int],
    holding_codes: set[str],
) -> bool:
    # A persisted holding id is a lifecycle identity. Never attach an old
    # recommendation to a newly-created holding merely because the code is the
    # same. Code fallback is only for legacy rows that predate holding_id.
    if row.holding_id is not None:
        return int(row.holding_id) in holding_ids
    return _normalize_code(str(row.code or "")) in holding_codes


def expire_orphaned_recommendations(
    db: Session,
    *,
    now: datetime | None = None,
    commit: bool = True,
) -> int:
    now = now or shanghai_now_naive()
    holding_ids, holding_codes = current_holding_identity(db)
    changed = 0
    rows = (
        db.query(ActionRecommendation)
        .filter(ActionRecommendation.acknowledged_at.is_(None))
        .all()
    )
    for row in rows:
        if recommendation_belongs_to_current_holding(
            row,
            holding_ids=holding_ids,
            holding_codes=holding_codes,
        ):
            continue
        if row.expires_at is None or row.expires_at > now:
            row.expires_at = now
            changed += 1
    if changed:
        if commit:
            db.commit()
        else:
            db.flush()
    return changed


def active_current_recommendations(
    db: Session,
    *,
    include_acknowledged: bool = False,
    limit: int = 500,
    now: datetime | None = None,
) -> list[ActionRecommendation]:
    now = now or shanghai_now_naive()
    expire_orphaned_recommendations(db, now=now)
    holding_ids, holding_codes = current_holding_identity(db)
    if not holding_ids and not holding_codes:
        return []

    query = db.query(ActionRecommendation).filter(
        ActionRecommendation.expires_at.is_not(None),
        ActionRecommendation.expires_at > now,
    )
    if not include_acknowledged:
        query = query.filter(ActionRecommendation.acknowledged_at.is_(None))
    rows = (
        query.order_by(
            ActionRecommendation.updated_at.desc(),
            ActionRecommendation.id.desc(),
        )
        .limit(limit)
        .all()
    )
    latest_by_target: dict[str, ActionRecommendation] = {}
    for row in rows:
        if not recommendation_belongs_to_current_holding(
            row,
            holding_ids=holding_ids,
            holding_codes=holding_codes,
        ):
            continue
        key = str(row.holding_id or _normalize_code(str(row.code or "")))
        latest_by_target.setdefault(key, row)
    return list(latest_by_target.values())
