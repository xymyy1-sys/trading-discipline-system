from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import LimitUpPromotionSample
from app.services.trading_calendar import next_a_share_trading_day


MODEL_VERSION = "promotion-v1"


def _wilson(successes: int, total: int, z: float = 1.645) -> tuple[float, float]:
    """Return a deliberately conservative 90% Wilson interval in percent."""
    if total <= 0:
        return 5.0, 55.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, (centre - spread) * 100), min(100.0, (centre + spread) * 100)


def _stocks(ladder: Any) -> dict[str, Any]:
    return {
        str(stock.code): stock
        for group in getattr(ladder, "groups", [])
        for stock in getattr(group, "stocks", [])
        if str(getattr(stock, "code", "") or "")
    }


def _role_context(atmosphere: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for theme in getattr(atmosphere, "theme_ladders", []):
        for rank, role in enumerate(getattr(theme, "identity_roles", []), start=1):
            code = str(getattr(role, "code", "") or "")
            if not code:
                continue
            candidate = {
                "theme": str(getattr(theme, "name", "") or ""),
                "theme_stage": str(getattr(theme, "stage", "") or ""),
                "is_mainline": getattr(theme, "is_mainline", None),
                "mainline_rank": getattr(theme, "mainline_rank", None),
                "theme_promotion_rate": getattr(theme, "promotion_rate", None),
                "theme_completeness_score": int(getattr(theme, "completeness_score", 0) or 0),
                "roles": list(getattr(role, "roles", []) or []),
                "role_score": int(getattr(role, "role_score", 0) or 0),
                "identity_rank": rank,
                "max_position_ratio": float(getattr(role, "max_position_ratio", 0) or 0),
            }
            previous = result.get(code)
            if previous is None or candidate["role_score"] > previous["role_score"]:
                result[code] = candidate
    return result


def resolve_pending_promotion_samples(db: Session, *, trade_date: str, ladder: Any) -> int:
    current = _stocks(ladder)
    rows = db.query(LimitUpPromotionSample).filter(
        LimitUpPromotionSample.evaluation_date == trade_date,
        LimitUpPromotionSample.status == "PENDING",
    ).all()
    evaluated_at = shanghai_now_naive()
    for row in rows:
        stock = current.get(row.code)
        actual_level = int(getattr(stock, "consecutive_limit_days", 0) or 0) if stock else 0
        promoted = actual_level >= row.target_level
        row.actual_level = actual_level
        row.status = "PROMOTED" if promoted else "FAILED"
        row.outcome_json = json.dumps({
            "promoted": promoted,
            "actual_level": actual_level,
            "source_trade_date": trade_date,
            "still_limit_up": stock is not None,
        }, ensure_ascii=False, sort_keys=True)
        row.evaluated_at = evaluated_at
        db.add(row)
    db.flush()
    return len(rows)


def _cohort_statistics(db: Session) -> dict[int, dict[str, float | int]]:
    rows = db.query(LimitUpPromotionSample).filter(
        LimitUpPromotionSample.status.in_(["PROMOTED", "FAILED"]),
    ).all()
    grouped: dict[int, list[LimitUpPromotionSample]] = defaultdict(list)
    for row in rows:
        grouped[int(row.from_level)].append(row)
    result: dict[int, dict[str, float | int]] = {}
    for level, items in grouped.items():
        total = len(items)
        successes = sum(item.status == "PROMOTED" for item in items)
        low, high = _wilson(successes, total)
        # Beta(2, 4) shrinkage starts at 33.3% and yields to actual samples.
        posterior = (successes + 2) / (total + 6) * 100
        result[level] = {
            "sample_count": total,
            "promoted_count": successes,
            "posterior": round(posterior, 1),
            "confidence_low": round(low, 1),
            "confidence_high": round(high, 1),
        }
    return result


def _candidate_probability(stock: Any, context: dict[str, Any], cohort: dict[str, float | int]) -> tuple[float, float, float, list[str]]:
    base = float(cohort.get("posterior") or 33.3)
    adjustment = 0.0
    evidence: list[str] = []
    if context.get("is_mainline") is True:
        adjustment += 6
        evidence.append("属于已确认主线，晋级先验上调")
    elif context.get("is_mainline") is False:
        adjustment -= 8
        evidence.append("非主线题材，持续性先验下调")
    stage = str(context.get("theme_stage") or "")
    if stage in {"启动", "发酵", "修复"}:
        adjustment += 4
        evidence.append(f"题材处于{stage}阶段")
    elif stage in {"高潮", "退潮"}:
        adjustment -= 6
        evidence.append(f"题材处于{stage}阶段，防止一致性兑现")
    role_score = float(context.get("role_score") or 0)
    if role_score >= 75:
        adjustment += 5
        evidence.append("同身位身份竞争评分靠前")
    elif role_score and role_score < 45:
        adjustment -= 4
    break_count = int(getattr(stock, "break_count", 0) or 0)
    if break_count == 0:
        adjustment += 2
        evidence.append("当日未炸板")
    elif break_count >= 2:
        adjustment -= min(8, break_count * 2)
        evidence.append(f"当日炸板{break_count}次，封板稳定性下降")
    amount = float(getattr(stock, "amount", 0) or 0)
    sealed = float(getattr(stock, "sealed_amount", 0) or 0)
    seal_ratio = sealed / amount if amount > 0 else 0
    if seal_ratio >= 0.15:
        adjustment += 3
        evidence.append("封单额相对成交额较强")
    probability = max(5.0, min(75.0, base + adjustment))
    low = max(0.0, float(cohort.get("confidence_low") or 5) + adjustment)
    high = min(95.0, float(cohort.get("confidence_high") or 55) + adjustment)
    if low > probability:
        low = max(0.0, probability - 5)
    if high < probability:
        high = min(95.0, probability + 5)
    return round(probability, 1), round(low, 1), round(high, 1), evidence


def record_closing_promotion_cohort(
    db: Session,
    *,
    completed_trade_date: str,
    ladder: Any,
    atmosphere: Any,
) -> dict[str, dict[str, Any]]:
    """Resolve yesterday and freeze today's k -> k+1 candidate cohort."""
    resolve_pending_promotion_samples(db, trade_date=completed_trade_date, ladder=ladder)
    cohorts = _cohort_statistics(db)
    contexts = _role_context(atmosphere)
    stocks = _stocks(ladder)
    evaluation_date = next_a_share_trading_day(date.fromisoformat(completed_trade_date)).isoformat()
    ranked_by_level: dict[int, list[tuple[str, float]]] = defaultdict(list)
    prepared: dict[str, dict[str, Any]] = {}
    for code, stock in stocks.items():
        level = max(1, int(getattr(stock, "consecutive_limit_days", 1) or 1))
        context = contexts.get(code, {})
        cohort = cohorts.get(level, {
            "sample_count": 0,
            "promoted_count": 0,
            "posterior": 33.3,
            "confidence_low": 5.0,
            "confidence_high": 55.0,
        })
        probability, low, high, probability_evidence = _candidate_probability(stock, context, cohort)
        ranked_by_level[level].append((code, probability))
        prepared[code] = {
            "from_level": level,
            "target_level": level + 1,
            "transition": f"{level}进{level + 1}",
            "probability": probability,
            "confidence_low": low,
            "confidence_high": high,
            "historical_sample_count": int(cohort.get("sample_count") or 0),
            "historical_promoted_count": int(cohort.get("promoted_count") or 0),
            "theme": str(context.get("theme") or getattr(stock, "industry", "") or ""),
            "roles": list(context.get("roles") or []),
            "probability_evidence": probability_evidence,
            "context": context,
        }
    for level, rows in ranked_by_level.items():
        rows.sort(key=lambda item: item[1], reverse=True)
        peer_count = len(rows)
        for rank, (code, _probability) in enumerate(rows, start=1):
            prepared[code]["same_level_rank"] = rank
            prepared[code]["same_level_count"] = peer_count

    now = shanghai_now_naive()
    for code, data in prepared.items():
        stock = stocks[code]
        existing = db.query(LimitUpPromotionSample).filter(
            LimitUpPromotionSample.signal_date == completed_trade_date,
            LimitUpPromotionSample.code == code,
            LimitUpPromotionSample.from_level == data["from_level"],
        ).first()
        if existing is not None:
            continue
        features = {
            **data["context"],
            "same_level_rank": data["same_level_rank"],
            "same_level_count": data["same_level_count"],
            "break_count": int(getattr(stock, "break_count", 0) or 0),
            "sealed_amount": float(getattr(stock, "sealed_amount", 0) or 0),
            "amount": float(getattr(stock, "amount", 0) or 0),
            "turnover": float(getattr(stock, "turnover", 0) or 0),
            "first_limit_time": str(getattr(stock, "first_limit_time", "") or ""),
            "last_limit_time": str(getattr(stock, "last_limit_time", "") or ""),
            "probability_evidence": data["probability_evidence"],
        }
        db.add(LimitUpPromotionSample(
            signal_date=completed_trade_date,
            evaluation_date=evaluation_date,
            code=code,
            name=str(getattr(stock, "name", "") or ""),
            from_level=data["from_level"],
            target_level=data["target_level"],
            theme=data["theme"],
            roles_json=json.dumps(data["roles"], ensure_ascii=False),
            features_json=json.dumps(features, ensure_ascii=False, sort_keys=True),
            model_version=MODEL_VERSION,
            prior_probability=data["probability"],
            confidence_low=data["confidence_low"],
            confidence_high=data["confidence_high"],
            historical_sample_count=data["historical_sample_count"],
            status="PENDING",
            created_at=now,
        ))
    db.flush()
    return prepared


def promotion_dashboard(db: Session, *, signal_date: str | None = None) -> dict[str, Any]:
    query = db.query(LimitUpPromotionSample)
    if signal_date:
        query = query.filter(LimitUpPromotionSample.signal_date == signal_date)
    rows = query.order_by(
        LimitUpPromotionSample.signal_date.desc(),
        LimitUpPromotionSample.from_level.asc(),
        LimitUpPromotionSample.prior_probability.desc(),
    ).limit(300).all()
    latest_date = signal_date or (rows[0].signal_date if rows else "")
    current = [row for row in rows if row.signal_date == latest_date]
    history = _cohort_statistics(db)
    # The first deployed cohort has not had a following session in which it
    # can be resolved yet.  Still expose the shrinkage prior for every level
    # represented in today's cohort so the UI does not look like an empty
    # model.  These rows are explicitly marked as priors rather than results.
    visible_levels = {int(row.from_level) for row in current}
    history_rows = []
    for level in sorted(set(history) | visible_levels):
        metrics = history.get(level, {
            "sample_count": 0,
            "promoted_count": 0,
            "posterior": 33.3,
            "confidence_low": 5.0,
            "confidence_high": 55.0,
        })
        history_rows.append({
            "from_level": level,
            "transition": f"{level}进{level + 1}",
            "basis": "历史结果" if int(metrics["sample_count"]) > 0 else "收缩先验",
            **metrics,
        })

    items = []
    for row in current:
        features = json.loads(row.features_json or "{}")
        stage_cap = 0.15 if row.from_level == 1 else 0.12 if row.from_level == 2 else 0.08
        role_cap = float(features.get("max_position_ratio") or 0)
        trial_cap = min(role_cap, stage_cap) if role_cap > 0 else 0.0
        items.append({
            "id": row.id,
            "code": row.code,
            "name": row.name,
            "theme": row.theme,
            "from_level": row.from_level,
            "target_level": row.target_level,
            "transition": f"{row.from_level}进{row.target_level}",
            "probability": row.prior_probability,
            "confidence_low": row.confidence_low,
            "confidence_high": row.confidence_high,
            "historical_sample_count": row.historical_sample_count,
            "status": row.status,
            "actual_level": row.actual_level,
            "same_level_rank": features.get("same_level_rank"),
            "same_level_count": features.get("same_level_count"),
            "trial_position_ratio": trial_cap,
            "roles": json.loads(row.roles_json or "[]"),
            "features": features,
        })
    return {
        "model_version": MODEL_VERSION,
        "signal_date": latest_date,
        "history": history_rows,
        "items": items,
        "note": "概率按每一级独立统计；样本不足时使用收缩先验并展示宽区间，不代表买入指令。",
    }
