from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    ActionRecommendation,
    ActionRecommendationRevision,
    RecommendationFeedback,
    TradeLog,
)


EXECUTED_FEEDBACK_CODES = {"executed", "partially_executed"}
EXECUTED_FEEDBACK_LABELS = {"已执行", "部分执行"}


def normalized_trade_code(value: str) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits.zfill(6) if digits and len(digits) <= 6 else (digits or str(value or "").strip())


def trade_code_aliases(value: str) -> set[str]:
    raw = str(value or "").strip()
    code = normalized_trade_code(raw)
    aliases = {raw, code, code.lstrip("0")}
    if len(code) == 6:
        aliases.update({f"sh{code}", f"sz{code}", f"bj{code}", f"SH{code}", f"SZ{code}", f"BJ{code}"})
    return {item for item in aliases if item}


def recommendation_trade_side(revision: ActionRecommendationRevision) -> str | None:
    action = str(revision.action or "")
    if any(token in action for token in ("禁止", "观察", "继续持有", "不操作")) and not float(
        revision.recommended_ratio or 0
    ):
        return None
    # Explicit action wording is more authoritative than the unsigned ratio:
    # both "加仓25%" and "减仓25%" legitimately carry a positive ratio.
    if any(token in action for token in ("买入", "加仓", "补仓", "买回")):
        return "BUY"
    if float(revision.recommended_ratio or 0) > 0 or any(
        token in action for token in ("减仓", "退出", "卖出", "清仓", "只留")
    ):
        return "SELL"
    return None


def trade_side_code(value: str) -> str:
    text = str(value or "").strip().upper()
    if text in {"卖出", "减仓", "SELL", "S"} or "卖" in text or "减仓" in text:
        return "SELL"
    if text in {"买入", "加仓", "BUY", "B"} or "买" in text or "加仓" in text:
        return "BUY"
    return ""


def revision_context(revision: ActionRecommendationRevision | None) -> dict:
    if revision is None:
        return {}
    try:
        value = json.loads(revision.decision_context_json or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def matching_trades_for_revision(
    db: Session,
    recommendation: ActionRecommendation,
    revision: ActionRecommendationRevision,
    expected_side: str,
) -> list[tuple[TradeLog, datetime]]:
    """Return all direction-correct trades in the immutable revision window.

    Recommendation revisions use Shanghai wall time.  Historic ``TradeLog``
    rows are a mixture of UTC-naive model defaults and Shanghai-naive imports,
    so both interpretations are evaluated explicitly.
    """

    local_start = revision.created_at
    local_end = revision.effective_until or recommendation.expires_at or (local_start + timedelta(minutes=15))
    if local_end < local_start:
        return []
    utc_offset = timedelta(hours=8)
    broad_start = local_start - utc_offset
    broad_end = local_end
    aliases = trade_code_aliases(recommendation.code)
    rows = (
        db.query(TradeLog)
        .filter(
            TradeLog.code.in_(aliases),
            TradeLog.traded_at >= broad_start,
            TradeLog.traded_at <= broad_end,
        )
        .order_by(TradeLog.traded_at.asc(), TradeLog.id.asc())
        .all()
    )
    matched: list[tuple[TradeLog, datetime]] = []
    for row in rows:
        if trade_side_code(row.side) != expected_side:
            continue
        raw = row.traded_at
        interpretations = [raw, raw + utc_offset]
        valid = [value for value in interpretations if local_start <= value <= local_end]
        if not valid:
            continue
        local_time = min(valid, key=lambda value: abs((value - local_start).total_seconds()))
        if local_time.date() != local_start.date():
            continue
        matched.append((row, local_time))
    return sorted(matched, key=lambda item: (item[1], item[0].id))


@dataclass(frozen=True)
class FeedbackExecutionResolution:
    trade_id: int | None
    result: str
    executed_quantity: int
    executed_ratio: float
    executed_price: float
    matched_trade_count: int = 0


def resolve_feedback_execution(
    db: Session,
    recommendation: ActionRecommendation,
    revision: ActionRecommendationRevision | None,
    status_code: str,
    *,
    executed_quantity: int | None = None,
    executed_ratio: float | None = None,
    executed_price: float | None = None,
) -> FeedbackExecutionResolution:
    """Resolve persisted execution fields without conflating zero and missing.

    Explicit zero is a real user value.  Only ``None`` asks the matcher to
    derive the quantity, ratio or price from the revision's aggregate trades.
    """

    expected_side = recommendation_trade_side(revision) if revision is not None else None
    matched_trades = (
        matching_trades_for_revision(db, recommendation, revision, expected_side)
        if status_code in EXECUTED_FEEDBACK_CODES and revision is not None and expected_side
        else []
    )
    matched_quantity = sum(max(0, int(item.quantity or 0)) for item, _ in matched_trades)
    matched_amount = sum(
        max(0, int(item.quantity or 0)) * max(0, float(item.price or 0))
        for item, _ in matched_trades
    )
    matched_price = matched_amount / matched_quantity if matched_quantity else 0.0
    actual_quantity = int(matched_quantity if executed_quantity is None else executed_quantity)
    actual_price = float(matched_price if executed_price is None else executed_price)
    context = revision_context(revision)
    expected_quantity = max(0, int(context.get("recommended_sell_quantity") or 0))
    quantity_base = max(0, int(context.get("sellable_quantity") or context.get("current_quantity") or 0))
    derived_ratio = actual_quantity / quantity_base if actual_quantity and quantity_base else 0.0
    actual_ratio = float(derived_ratio if executed_ratio is None else executed_ratio)

    if status_code in EXECUTED_FEEDBACK_CODES:
        if expected_side is None:
            result = "该建议无需成交确认"
        elif not matched_trades and not actual_quantity:
            result = "待匹配成交"
        elif expected_quantity and actual_quantity < expected_quantity:
            result = f"已匹配部分执行 {actual_quantity}/{expected_quantity} 股"
        elif len(matched_trades) > 1:
            result = f"已汇总匹配 {len(matched_trades)} 笔成交"
        elif len(matched_trades) == 1:
            result = "已匹配单笔成交"
        else:
            result = "已按用户确认记录执行数量"
    elif status_code == "not_filled":
        result = "明确未成交"
    else:
        result = "无需成交匹配"

    return FeedbackExecutionResolution(
        trade_id=matched_trades[0][0].id if matched_trades else None,
        result=result,
        executed_quantity=max(0, actual_quantity),
        executed_ratio=min(1.0, max(0.0, actual_ratio)),
        executed_price=max(0.0, actual_price),
        matched_trade_count=len(matched_trades),
    )


def _apply_resolution(feedback: RecommendationFeedback, resolution: FeedbackExecutionResolution) -> bool:
    values = {
        "trade_id": resolution.trade_id,
        "result": resolution.result,
        "executed_quantity": resolution.executed_quantity,
        "executed_ratio": resolution.executed_ratio,
        "executed_price": resolution.executed_price,
    }
    changed = any(getattr(feedback, key) != value for key, value in values.items())
    if not changed:
        return False
    for key, value in values.items():
        setattr(feedback, key, value)
    feedback.updated_at = shanghai_now_naive()
    return True


def rematch_execution_feedback_for_codes(db: Session, codes: Iterable[str]) -> int:
    """Reconcile executed feedback after trades are inserted or edited.

    The function never commits.  Callers keep trade, holdings and feedback in
    one transaction.  Non-executed statuses are deliberately excluded.
    """

    normalized_codes = {normalized_trade_code(value) for value in codes if value}
    if not normalized_codes:
        return 0
    aliases: set[str] = set()
    for value in normalized_codes:
        aliases.update(trade_code_aliases(value))
    recommendations = db.query(ActionRecommendation).filter(ActionRecommendation.code.in_(aliases)).all()
    recommendations = [
        row for row in recommendations if normalized_trade_code(row.code) in normalized_codes
    ]
    if not recommendations:
        return 0
    recommendation_by_id = {row.id: row for row in recommendations}
    feedback_rows = (
        db.query(RecommendationFeedback)
        .filter(
            RecommendationFeedback.recommendation_id.in_(recommendation_by_id),
            RecommendationFeedback.recommendation_revision_id.is_not(None),
            or_(
                RecommendationFeedback.status_code.in_(EXECUTED_FEEDBACK_CODES),
                RecommendationFeedback.status.in_(EXECUTED_FEEDBACK_LABELS),
            ),
        )
        .all()
    )
    changed = 0
    for feedback in feedback_rows:
        recommendation = recommendation_by_id.get(feedback.recommendation_id)
        revision = db.get(ActionRecommendationRevision, feedback.recommendation_revision_id)
        if recommendation is None or revision is None or revision.recommendation_id != recommendation.id:
            continue
        status_code = str(feedback.status_code or "")
        if status_code not in EXECUTED_FEEDBACK_CODES:
            status_code = "partially_executed" if feedback.status == "部分执行" else "executed"
        resolution = resolve_feedback_execution(db, recommendation, revision, status_code)
        # Keep a manual confirmation intact while there has never been a DB
        # match.  Once a linked trade existed, editing it out of the immutable
        # window must clear the stale link and return the feedback to pending.
        if resolution.trade_id is None and feedback.trade_id is None:
            continue
        changed += int(_apply_resolution(feedback, resolution))
    if changed:
        db.flush()
    return changed
