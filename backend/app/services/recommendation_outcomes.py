from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    ActionRecommendation,
    ActionRecommendationRevision,
    RecommendationOutcome,
    VolumePriceSnapshot,
)


HORIZONS: tuple[tuple[str, int], ...] = (("5m", 5), ("15m", 15), ("30m", 30))
REFERENCE_TOLERANCE = timedelta(minutes=5)
HORIZON_TOLERANCE = timedelta(minutes=5)
UNUSABLE_QUALITIES = {"", "missing", "manual", "invalid", "unavailable"}
RELIABLE_QUALITIES = {"realtime", "complete", "ok", "reliable"}
NEXT_SESSION_EXPIRY_DAYS = 15


def _aliases(code: str) -> set[str]:
    raw = str(code or "").strip()
    digits = "".join(char for char in raw if char.isdigit())
    normalized = digits.zfill(6) if digits and len(digits) <= 6 else (digits or raw)
    return {item for item in {raw, normalized, normalized.lstrip("0")} if item}


def _usable(row: VolumePriceSnapshot) -> bool:
    return float(row.price or 0) > 0 and str(row.data_quality or "").lower() not in UNUSABLE_QUALITIES


def _reliable(row: VolumePriceSnapshot) -> bool:
    return float(row.price or 0) > 0 and str(row.data_quality or "").lower() in RELIABLE_QUALITIES


def _return_pct(price: float | None, reference: float | None) -> float | None:
    if price is None or reference is None or reference <= 0:
        return None
    return round((price / reference - 1) * 100, 4)


def _session_target(value: datetime, minutes: int) -> datetime | None:
    """Add A-share continuous-trading minutes, skipping the lunch break."""

    day = value.date()
    morning_open = datetime.combine(day, time(9, 30))
    morning_close = datetime.combine(day, time(11, 30))
    afternoon_open = datetime.combine(day, time(13, 0))
    afternoon_close = datetime.combine(day, time(15, 0))

    if value < morning_open:
        target = value + timedelta(minutes=minutes)
        if target <= morning_close:
            return target
        overflow = target - morning_close
        target = afternoon_open + overflow
        return target if target <= afternoon_close else None
    if value <= morning_close:
        target = value + timedelta(minutes=minutes)
        if target <= morning_close:
            return target
        target = afternoon_open + (target - morning_close)
        return target if target <= afternoon_close else None
    if value < afternoon_open:
        target = afternoon_open + timedelta(minutes=minutes)
        return target if target <= afternoon_close else None
    target = value + timedelta(minutes=minutes)
    return target if target <= afternoon_close else None


def _rows_for_day(
    db: Session,
    code: str,
    trade_date: str,
    cache: dict[tuple[str, str], list[VolumePriceSnapshot]] | None = None,
) -> list[VolumePriceSnapshot]:
    normalized = max(_aliases(code), key=len, default=code)
    key = (normalized, trade_date)
    if cache is not None and key in cache:
        return cache[key]
    rows = (
        db.query(VolumePriceSnapshot)
        .filter(
            VolumePriceSnapshot.code.in_(list(_aliases(code))),
            VolumePriceSnapshot.trade_date == trade_date,
        )
        .order_by(VolumePriceSnapshot.captured_at.asc(), VolumePriceSnapshot.id.asc())
        .all()
    )
    if cache is not None:
        cache[key] = rows
    return rows


def _reference_row(
    rows: list[VolumePriceSnapshot],
    signal_at: datetime,
) -> VolumePriceSnapshot | None:
    usable = [row for row in rows if _usable(row)]
    before = [
        row
        for row in usable
        if signal_at - REFERENCE_TOLERANCE <= row.captured_at <= signal_at
    ]
    if before:
        return before[-1]
    after = [
        row
        for row in usable
        if signal_at < row.captured_at <= signal_at + REFERENCE_TOLERANCE
    ]
    return after[0] if after else None


def _horizon_row(
    rows: list[VolumePriceSnapshot],
    target: datetime,
) -> VolumePriceSnapshot | None:
    return next(
        (
            row
            for row in rows
            if _usable(row) and target <= row.captured_at <= target + HORIZON_TOLERANCE
        ),
        None,
    )


def _close_row(
    rows: list[VolumePriceSnapshot],
    *,
    not_before: datetime | None = None,
) -> VolumePriceSnapshot | None:
    candidates = [
        row
        for row in rows
        if _usable(row) and time(14, 50) <= row.captured_at.time() <= time(15, 5)
        # The same-day close must be an observation after the immutable signal.
        # Reusing a 14:55 quote for a later recommendation is look-ahead in
        # reverse: it labels a pre-signal price as the recommendation's result.
        and (not_before is None or row.captured_at > not_before)
    ]
    return candidates[-1] if candidates else None


def _next_open_row(rows: list[VolumePriceSnapshot]) -> VolumePriceSnapshot | None:
    # ``open_price`` is an immutable official daily field once populated.  A
    # reliable 10:00 snapshot carrying it is more accurate than substituting a
    # 09:30 quote just because the former arrived later.
    official = [row for row in rows if _reliable(row) and float(row.open_price or 0) > 0]
    if official:
        return official[0]
    candidates = [
        row
        for row in rows
        if _reliable(row) and time(9, 30) <= row.captured_at.time() <= time(9, 45)
    ]
    return candidates[0] if candidates else None


def _next_reliable_trade_date(
    db: Session,
    code: str,
    after_date: str,
    through_date: str,
    cache: dict[tuple[str, str, str], str | None] | None = None,
) -> str | None:
    normalized = max(_aliases(code), key=len, default=code)
    key = (normalized, after_date, through_date)
    if cache is not None and key in cache:
        return cache[key]
    value = db.query(func.min(VolumePriceSnapshot.trade_date)).filter(
        VolumePriceSnapshot.code.in_(list(_aliases(code))),
        VolumePriceSnapshot.trade_date > after_date,
        VolumePriceSnapshot.trade_date <= through_date,
        VolumePriceSnapshot.price > 0,
        func.lower(VolumePriceSnapshot.data_quality).in_(list(RELIABLE_QUALITIES)),
    ).scalar()
    result = str(value) if value else None
    if cache is not None:
        cache[key] = result
    return result


def _aggregate_quality(rows: list[VolumePriceSnapshot]) -> str:
    qualities = {str(row.data_quality or "").lower() for row in rows}
    if not qualities:
        return "pending"
    if qualities <= RELIABLE_QUALITIES:
        return "reliable"
    return "degraded"


def _signal_sources(
    db: Session,
    *,
    cutoff_date: str,
    source_limit: int,
) -> list[dict[str, Any]]:
    recommendations = (
        db.query(ActionRecommendation)
        .filter(ActionRecommendation.trade_date >= cutoff_date)
        .order_by(ActionRecommendation.created_at.desc(), ActionRecommendation.id.desc())
        .limit(source_limit)
        .all()
    )
    if not recommendations:
        return []
    recommendation_ids = [row.id for row in recommendations]
    revisions = (
        db.query(ActionRecommendationRevision)
        .filter(ActionRecommendationRevision.recommendation_id.in_(recommendation_ids))
        .order_by(ActionRecommendationRevision.created_at.desc(), ActionRecommendationRevision.id.desc())
        .all()
    )
    revisions_by_recommendation: dict[int, list[ActionRecommendationRevision]] = defaultdict(list)
    for row in revisions:
        revisions_by_recommendation[row.recommendation_id].append(row)

    sources: list[dict[str, Any]] = []
    for recommendation in recommendations:
        signal_revisions = revisions_by_recommendation.get(recommendation.id) or []
        if signal_revisions:
            for revision in signal_revisions:
                sources.append(
                    {
                        "source_key": f"recommendation:{recommendation.id}:revision:{revision.id}",
                        "recommendation_id": recommendation.id,
                        "recommendation_revision_id": revision.id,
                        "trade_date": recommendation.trade_date,
                        "code": recommendation.code,
                        "name": recommendation.name,
                        "signal_at": revision.created_at,
                        "level": revision.level,
                        "state": revision.state,
                        "action": revision.action,
                        "recommended_ratio": revision.recommended_ratio,
                    }
                )
        else:
            sources.append(
                {
                    "source_key": f"recommendation:{recommendation.id}:base",
                    "recommendation_id": recommendation.id,
                    "recommendation_revision_id": None,
                    "trade_date": recommendation.trade_date,
                    "code": recommendation.code,
                    "name": recommendation.name,
                    "signal_at": recommendation.created_at,
                    "level": recommendation.level,
                    "state": recommendation.state,
                    "action": recommendation.action,
                    "recommended_ratio": recommendation.recommended_ratio,
                }
            )
    return sorted(sources, key=lambda item: (item["signal_at"], item["source_key"]), reverse=True)


def _ensure_outcomes(
    db: Session,
    *,
    now: datetime,
    lookback_days: int,
    source_limit: int,
) -> int:
    cutoff = (now.date() - timedelta(days=max(1, lookback_days))).isoformat()
    # A legacy base row may have been evaluated before immutable revisions
    # were introduced.  Once a recommendation owns revisions, retaining that
    # base row as a valid sample counts the same signal twice.  Preserve it for
    # audit, but explicitly supersede it before creating/reusing revision rows.
    revision_recommendation_ids = {
        int(row[0])
        for row in db.query(ActionRecommendationRevision.recommendation_id).distinct().all()
    }
    if revision_recommendation_ids:
        legacy_rows = (
            db.query(RecommendationOutcome)
            .filter(
                RecommendationOutcome.recommendation_id.in_(revision_recommendation_ids),
                RecommendationOutcome.recommendation_revision_id.is_(None),
            )
            .all()
        )
        for row in legacy_rows:
            if row.status == "invalid" and row.data_quality == "superseded":
                continue
            row.status = "invalid"
            row.data_quality = "superseded"
            row.invalid_reason = "已由不可变建议版本替代；保留本行仅用于审计。"
            row.updated_at = now
            db.add(row)
        if legacy_rows:
            db.flush()
    sources = _signal_sources(db, cutoff_date=cutoff, source_limit=source_limit)
    if not sources:
        return 0
    existing = {
        row[0]
        for row in db.query(RecommendationOutcome.source_key)
        .filter(RecommendationOutcome.trade_date >= cutoff)
        .all()
    }
    created = 0
    for source in sources:
        if source["source_key"] in existing:
            continue
        db.add(
            RecommendationOutcome(
                **source,
                status="pending",
                data_quality="pending",
                created_at=now,
                updated_at=now,
            )
        )
        created += 1
    if created:
        db.flush()
    return created


def _is_horizon_due(target: datetime | None, now: datetime) -> bool:
    return target is None or target + HORIZON_TOLERANCE <= now


def _update_one(
    db: Session,
    outcome: RecommendationOutcome,
    now: datetime,
    *,
    row_cache: dict[tuple[str, str], list[VolumePriceSnapshot]] | None = None,
    next_date_cache: dict[tuple[str, str, str], str | None] | None = None,
) -> None:
    same_day_rows = _rows_for_day(db, outcome.code, outcome.trade_date, row_cache)
    reference = None
    if outcome.reference_snapshot_id:
        candidate = next(
            (row for row in same_day_rows if row.id == outcome.reference_snapshot_id),
            None,
        )
        reference = candidate if candidate is not None and _usable(candidate) else None
    if reference is None:
        reference = _reference_row(same_day_rows, outcome.signal_at)
    signal_day_finished = outcome.trade_date < now.date().isoformat() or (
        outcome.trade_date == now.date().isoformat() and now.time() >= time(15, 0)
    )
    if reference is None:
        nearby = [
            row
            for row in same_day_rows
            if abs((row.captured_at - outcome.signal_at).total_seconds()) <= REFERENCE_TOLERANCE.total_seconds()
        ]
        if signal_day_finished:
            outcome.status = "invalid"
            outcome.data_quality = "invalid"
            outcome.invalid_reason = (
                "建议时点前后5分钟仅有手工或缺失价格快照，结果不纳入统计。"
                if nearby
                else "建议时点前后5分钟没有价格快照，结果不纳入统计。"
            )
        else:
            outcome.status = "pending"
            outcome.data_quality = "pending"
        outcome.missing_horizons_json = json.dumps(
            ["reference", "5m", "15m", "30m", "close", "next_open", "next_close"],
            ensure_ascii=False,
        )
        outcome.updated_at = now
        return

    outcome.reference_snapshot_id = reference.id
    outcome.reference_at = reference.captured_at
    outcome.reference_latency_seconds = round(
        (reference.captured_at - outcome.signal_at).total_seconds(),
        3,
    )
    outcome.reference_price = round(float(reference.price), 4)
    outcome.reference_source = str(reference.data_source or "")
    outcome.reference_quality = str(reference.data_quality or "")
    outcome.invalid_reason = ""
    used_rows = [reference]
    missing: list[str] = []
    future_pending = False

    for label, minutes in HORIZONS:
        # Evaluation windows belong to the immutable recommendation signal.
        # The reference snapshot may be up to five minutes earlier/later and
        # must never shift the 5/15/30-minute observation clocks.
        target = _session_target(outcome.signal_at, minutes)
        price_attr = f"price_{label}"
        return_attr = f"return_{label}_pct"
        if target is None:
            missing.append(f"{label}:建议时点过晚，超出当日交易时段")
            continue
        row = _horizon_row(same_day_rows, target)
        if row is not None:
            price = round(float(row.price), 4)
            setattr(outcome, price_attr, price)
            setattr(outcome, return_attr, _return_pct(price, outcome.reference_price))
            used_rows.append(row)
        elif _is_horizon_due(target, now):
            missing.append(label)
        else:
            future_pending = True

    close_available = signal_day_finished
    close = _close_row(same_day_rows, not_before=outcome.signal_at) if close_available else None
    if close is not None:
        outcome.close_price = round(float(close.price), 4)
        outcome.return_close_pct = _return_pct(outcome.close_price, outcome.reference_price)
        used_rows.append(close)
    elif close_available:
        missing.append("close")
    else:
        future_pending = True

    outcome.next_trade_date = _next_reliable_trade_date(
        db,
        outcome.code,
        outcome.trade_date,
        now.date().isoformat(),
        next_date_cache,
    )
    next_day_rows: list[VolumePriceSnapshot] = []
    next_close_due = False
    next_session_expired = False
    if outcome.next_trade_date:
        next_day_rows = _rows_for_day(db, outcome.code, outcome.next_trade_date, row_cache)
        next_open = _next_open_row(next_day_rows)
        if next_open is not None:
            open_price = float(next_open.open_price or 0) or float(next_open.price)
            outcome.next_open_price = round(open_price, 4)
            outcome.return_next_open_pct = _return_pct(outcome.next_open_price, outcome.reference_price)
            used_rows.append(next_open)
        elif outcome.next_trade_date < now.date().isoformat() or now.time() >= time(9, 45):
            missing.append("next_open")
        else:
            future_pending = True

        next_close_due = outcome.next_trade_date < now.date().isoformat() or now.time() >= time(15, 0)
        next_close = _close_row(next_day_rows) if next_close_due else None
        if next_close is not None:
            outcome.next_close_price = round(float(next_close.price), 4)
            outcome.return_next_close_pct = _return_pct(outcome.next_close_price, outcome.reference_price)
            used_rows.append(next_close)
        elif next_close_due:
            missing.append("next_close")
        else:
            future_pending = True
    else:
        try:
            next_session_expired = (
                now.date() - datetime.fromisoformat(outcome.trade_date).date()
            ).days >= NEXT_SESSION_EXPIRY_DAYS
        except (TypeError, ValueError):
            next_session_expired = True
        if next_session_expired:
            missing.extend(["next_open", "next_close"])
        else:
            future_pending = True

    sampled_path = [
        row
        for row in same_day_rows
        if _usable(row) and row.captured_at >= outcome.signal_at
    ]
    if next_day_rows:
        sampled_path.extend(row for row in next_day_rows if _usable(row))
    if sampled_path:
        path_returns = [
            _return_pct(float(row.price), outcome.reference_price)
            for row in sampled_path
        ]
        # Compatibility field names are retained, but these are deliberately
        # raw price-path extrema: sampled interval highest return and sampled
        # interval lowest return.  They are not action-aware favorable/adverse
        # excursion and must not be used as a success label.
        valid_returns = [0.0, *[value for value in path_returns if value is not None]]
        if valid_returns:
            outcome.mfe_pct = round(max(valid_returns), 4)
            outcome.mae_pct = round(min(valid_returns), 4)
        used_rows.extend(sampled_path)

    outcome.missing_horizons_json = json.dumps(missing, ensure_ascii=False)
    outcome.data_quality = _aggregate_quality(used_rows)
    outcome.evaluated_through_at = max(row.captured_at for row in used_rows)
    required_missing = [item for item in missing if ":建议时点过晚" not in item]
    if outcome.next_close_price is not None and not required_missing:
        outcome.status = "complete"
    elif next_session_expired:
        outcome.status = "invalid"
        outcome.data_quality = "invalid"
        outcome.invalid_reason = (
            f"建议后 {NEXT_SESSION_EXPIRY_DAYS} 天内没有该股票的可靠次日行情，"
            "样本停止轮转且不纳入统计。"
        )
    elif outcome.next_trade_date and next_close_due and required_missing:
        outcome.status = "invalid"
        outcome.data_quality = "invalid"
        outcome.invalid_reason = "完整前向观察窗口已结束，但缺少：" + "、".join(required_missing)
    elif any(
        value is not None
        for value in (
            outcome.price_5m,
            outcome.price_15m,
            outcome.price_30m,
            outcome.close_price,
            outcome.next_open_price,
            outcome.next_close_price,
        )
    ):
        outcome.status = "partial"
    else:
        outcome.status = "pending" if future_pending else "partial"
    outcome.updated_at = now


def refresh_recommendation_outcomes(
    db: Session,
    *,
    now: datetime | None = None,
    lookback_days: int = 15,
    limit: int = 250,
    commit: bool = True,
) -> dict[str, int]:
    """Advance the post-hoc ledger from already persisted snapshots only.

    This function never fetches live data and is never imported by the decision
    engine.  It can therefore run after collection without leaking future data
    into the recommendation that is being measured.
    """

    evaluated_at = shanghai_now_naive(now)
    source_limit = max(limit, 50) * 2
    created = _ensure_outcomes(
        db,
        now=evaluated_at,
        lookback_days=lookback_days,
        source_limit=source_limit,
    )
    rows = (
        db.query(RecommendationOutcome)
        .filter(RecommendationOutcome.status.in_(["pending", "partial"]))
        # Rotate through the backlog by least-recent evaluation time.  Ordering
        # only by newest signal permanently starves old partial rows whenever
        # the pending population is larger than the per-run limit.
        .order_by(
            RecommendationOutcome.updated_at.asc(),
            RecommendationOutcome.signal_at.asc(),
            RecommendationOutcome.id.asc(),
        )
        .limit(max(1, min(limit, 1000)))
        .all()
    )
    row_cache: dict[tuple[str, str], list[VolumePriceSnapshot]] = {}
    next_date_cache: dict[tuple[str, str, str], str | None] = {}
    for row in rows:
        _update_one(
            db,
            row,
            evaluated_at,
            row_cache=row_cache,
            next_date_cache=next_date_cache,
        )
        db.add(row)
    if commit:
        db.commit()
    else:
        db.flush()
    counts: dict[str, int] = {"created": created, "evaluated": len(rows)}
    for status, count in db.query(
        RecommendationOutcome.status,
        func.count(RecommendationOutcome.id),
    ).group_by(RecommendationOutcome.status).all():
        counts[str(status)] = int(count)
    return counts


def recommendation_outcome_summary(db: Session) -> dict[str, Any]:
    rows = db.query(RecommendationOutcome).all()
    eligible = [
        row
        for row in rows
        if row.status == "complete" and row.data_quality == "reliable"
    ]

    def average(field: str) -> float | None:
        values = [
            float(value)
            for row in eligible
            if (value := getattr(row, field)) is not None
        ]
        return round(sum(values) / len(values), 4) if values else None

    status_counts = {key: 0 for key in ("pending", "partial", "complete", "invalid")}
    quality_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
        quality_counts[row.data_quality] += 1
    valid_total = len(rows) - status_counts.get("invalid", 0)
    return {
        "total": len(rows),
        # Complete/reliable rows are objective *price* outcomes only.  They are
        # not yet action-direction-adjusted or de-correlated by decision
        # episode, so they must never unlock model-effectiveness claims.
        "price_outcome_sample_count": len(eligible),
        "calibration_eligible_sample_count": 0,
        # Backward-compatible alias for older clients.
        "eligible_sample_count": len(eligible),
        "minimum_calibration_samples": 30,
        "status_counts": status_counts,
        "quality_counts": dict(sorted(quality_counts.items())),
        "complete_coverage_pct": round(status_counts.get("complete", 0) / valid_total * 100, 2)
        if valid_total
        else 0,
        "average_returns": {
            "5m": average("return_5m_pct"),
            "15m": average("return_15m_pct"),
            "30m": average("return_30m_pct"),
            "close": average("return_close_pct"),
            "next_open": average("return_next_open_pct"),
            "next_close": average("return_next_close_pct"),
            "mfe": average("mfe_pct"),
            "mae": average("mae_pct"),
        },
        "note": (
            "收益率是建议时点后的客观价格变化；mfe/mae 兼容字段分别表示"
            "采样区间最高涨幅和最低跌幅，未按建议动作方向解释，也未按标的日/"
            "决策事件去相关，因此当前校准合格样本固定为0，不等同于规则胜率。"
        ),
    }


def unresolved_outcome_targets(
    db: Session,
    *,
    now: datetime | None = None,
    days: int = 15,
    limit: int = 30,
) -> list[tuple[str, str]]:
    evaluated_at = shanghai_now_naive(now)
    cutoff = (evaluated_at.date() - timedelta(days=max(1, days))).isoformat()
    rows = (
        db.query(
            RecommendationOutcome.code,
            func.max(RecommendationOutcome.name).label("name"),
            func.max(RecommendationOutcome.signal_at).label("latest_signal_at"),
        )
        .filter(
            RecommendationOutcome.trade_date >= cutoff,
            RecommendationOutcome.status.in_(["pending", "partial"]),
        )
        # One mutable recommendation may have many immutable revisions.  Group
        # before limiting so revisions for one stock cannot crowd other sold
        # names out of the continued result collection list.
        .group_by(RecommendationOutcome.code)
        .order_by(func.max(RecommendationOutcome.signal_at).desc())
        .limit(max(3, min(limit * 3, 300)))
        .all()
    )
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        normalized = next(iter(sorted(_aliases(row.code), key=len, reverse=True)), row.code)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((normalized, str(row.name or "")))
        if len(result) >= max(1, min(limit, 100)):
            break
    return result
