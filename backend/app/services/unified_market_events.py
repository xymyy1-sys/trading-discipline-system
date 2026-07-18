"""Persist news impact and intraday sector expansion in one traceable event stream.

The opportunity radar deliberately separates a news *claim* from subsequent
market validation.  This module preserves that separation when serialising the
result into ``intraday_evidence_events``: an unverified headline can only
create a pending observation, never a confirmed risk/opportunity event.
"""

from __future__ import annotations

import hashlib
import json
import re
from threading import RLock
from datetime import datetime, time, timezone
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import IntradayEvidenceEvent


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
_CONFIRMED_NEWS_STATUSES = {"IMPACT_CONFIRMED"}
_VERIFIED_CLAIMS = {"OFFICIAL", "MEDIA_ATTRIBUTION"}
_PERSIST_LOCK = RLock()


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _list(item: Any, key: str) -> list[Any]:
    value = _value(item, key, [])
    return list(value or [])


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(SHANGHAI_TZ).replace(tzinfo=None)
    return parsed


def _code(value: Any) -> str:
    matched = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return matched.group(1) if matched else str(value or "").strip()


def _hash_key(prefix: str, value: str, length: int = 12) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]
    return f"{prefix}:{digest}"


def _sources(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " + ".join(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))[:128]
    return str(value or "").strip()[:128]


def _safe_source_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    return text if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else None


def _news_event_type(item: Any, *, holding_related: bool) -> tuple[str, str, int, bool]:
    prefix = "HOLDING_" if holding_related else ""
    status = str(_value(item, "news_impact_status", "UNVERIFIED") or "UNVERIFIED").upper()
    validation = str(_value(item, "market_validation", "PENDING") or "PENDING").upper()
    claim = str(_value(item, "claim_level", "RUMOR") or "RUMOR").upper()
    sentiment = str(_value(item, "sentiment", "待验证") or "待验证")

    # A title/classifier alone is never enough.  Positive/negative impact is
    # confirmed only for a traceable claim with subsequent market validation.
    impact_confirmed = (
        status in _CONFIRMED_NEWS_STATUSES
        and validation == "CONFIRMED"
        and claim in _VERIFIED_CLAIMS
    )
    if impact_confirmed and sentiment == "利空":
        return f"{prefix}NEWS_NEGATIVE_IMPACT_CONFIRMED", "warning", 78 if holding_related else 66, True
    if impact_confirmed and sentiment == "利好":
        return f"{prefix}NEWS_POSITIVE_IMPACT_CONFIRMED", "info", 62 if holding_related else 54, True
    if validation == "INVALIDATED" or status == "IMPACT_INVALIDATED":
        return f"{prefix}NEWS_IMPACT_INVALIDATED", "info", 32, True
    return f"{prefix}NEWS_PENDING_VALIDATION", "info", 24 if holding_related else 12, False


def _event_fingerprint(event_type: str, metadata: Mapping[str, Any]) -> str:
    material = {
        "event_type": event_type,
        "status": metadata.get("status"),
        "market_validation": metadata.get("market_validation"),
        "flow_turning": metadata.get("flow_turning"),
        "new_limit_up_count": metadata.get("new_limit_up_count"),
        "leaders": metadata.get("leaders"),
    }
    return hashlib.sha1(_json(material).encode("utf-8")).hexdigest()[:16]


def _upsert_transition(
    db: Session,
    *,
    trade_date: str,
    observed_at: datetime,
    scope: str,
    target_code: str,
    target_name: str,
    event_type: str,
    severity: str,
    priority: int,
    group_key: str,
    confirmed: bool,
    value: float,
    previous_value: float,
    evidence: list[str],
    counter_evidence: list[str],
    source: str,
    source_url: str | None,
    source_published_at: datetime | None,
    metadata: dict[str, Any],
) -> tuple[IntradayEvidenceEvent, bool]:
    fingerprint = _event_fingerprint(event_type, metadata)
    metadata = {**metadata, "state_fingerprint": fingerprint}
    latest = (
        db.query(IntradayEvidenceEvent)
        .filter(
            IntradayEvidenceEvent.trade_date == trade_date,
            IntradayEvidenceEvent.group_key == group_key,
        )
        .order_by(IntradayEvidenceEvent.id.desc())
        .first()
    )
    latest_metadata: dict[str, Any] = {}
    if latest is not None:
        try:
            latest_metadata = json.loads(latest.metadata_json or "{}")
        except (TypeError, ValueError):
            latest_metadata = {}
    if latest is not None and latest_metadata.get("state_fingerprint") == fingerprint:
        # Only the latest identical state is coalesced.  An A -> B -> A
        # sequence must remain three events, otherwise the second turning point
        # disappears from both replay and SSE.
        if not latest.state_key:
            latest.state_key = hashlib.sha1(
                f"{trade_date}\x1f{group_key}\x1f{fingerprint}\x1flegacy:{latest.id}".encode("utf-8")
            ).hexdigest()
        latest.last_seen_at = observed_at
        latest.occurrence_count = int(latest.occurrence_count or 1) + 1
        latest.value = float(value or 0)
        latest.previous_value = float(previous_value or 0)
        latest.evidence_json = _json(evidence)
        latest.counter_evidence_json = _json(counter_evidence)
        latest.metadata_json = _json(metadata)
        latest.source = source
        latest.source_url = source_url
        latest.source_published_at = source_published_at
        return latest, False

    predecessor_id = int(latest.id or 0) if latest is not None else 0
    state_key = hashlib.sha1(
        f"{trade_date}\x1f{group_key}\x1f{fingerprint}\x1f{predecessor_id}".encode("utf-8")
    ).hexdigest()

    row = IntradayEvidenceEvent(
        trade_date=trade_date,
        captured_at=observed_at,
        scope=scope,
        target_code=target_code[:16],
        target_name=target_name[:64],
        event_type=event_type,
        severity=severity,
        value=float(value or 0),
        previous_value=float(previous_value or 0),
        priority=priority,
        group_key=group_key[:64],
        state_key=state_key,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        occurrence_count=1,
        confirmed=confirmed,
        evidence_json=_json(evidence),
        counter_evidence_json=_json(counter_evidence),
        source=source,
        source_url=source_url,
        source_published_at=source_published_at,
        metadata_json=_json(metadata),
    )
    # The read-before-write check is not sufficient when two radar requests
    # arrive together.  The database key is the arbiter; a losing request
    # rolls back only its savepoint and reuses the winning row.
    savepoint = db.begin_nested()
    try:
        db.add(row)
        db.flush([row])
        savepoint.commit()
        return row, True
    except IntegrityError:
        savepoint.rollback()
        winner = (
            db.query(IntradayEvidenceEvent)
            .filter(
                IntradayEvidenceEvent.trade_date == trade_date,
                IntradayEvidenceEvent.state_key == state_key,
            )
            .first()
        )
        if winner is None:
            raise
        winner.last_seen_at = observed_at
        winner.occurrence_count = int(winner.occurrence_count or 1) + 1
        winner.value = float(value or 0)
        winner.previous_value = float(previous_value or 0)
        winner.evidence_json = _json(evidence)
        winner.counter_evidence_json = _json(counter_evidence)
        winner.metadata_json = _json(metadata)
        winner.source = source
        winner.source_url = source_url
        winner.source_published_at = source_published_at
        return winner, False


def _persist_unified_market_events(
    db: Session,
    radar: Mapping[str, Any] | Any,
    holdings: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> list[IntradayEvidenceEvent]:
    """Persist material radar transitions and return newly emitted rows.

    Repeated polling updates ``last_seen_at`` on the same state instead of
    flooding the stream.  A validation/flow/leader transition creates a new
    row and therefore a new SSE event id.
    """

    observed_at = shanghai_now_naive(now)
    trade_date = observed_at.date().isoformat()
    holding_map = {_code(code): name for code, name in dict(holdings or {}).items()}
    emitted: list[IntradayEvidenceEvent] = []

    expansion = _value(radar, "intraday_expansion") or {}
    expansion_as_of = _parse_datetime(_value(expansion, "as_of")) or observed_at
    for item in _list(expansion, "items"):
        sector = str(_value(item, "sector", "") or "").strip()
        if not sector:
            continue
        status = str(_value(item, "status", "") or "")
        confirmed = status == "增量已确认"
        event_type = "SECTOR_INCREMENT_CONFIRMED" if confirmed else "SECTOR_INCREMENT_WATCH"
        evidence = [str(value) for value in _list(item, "evidence") if str(value).strip()]
        counter = [str(value) for value in _list(item, "counter_evidence") if str(value).strip()]
        counter.extend(f"风险：{value}" for value in _list(item, "risk") if str(value).strip())
        metadata = {
            "status": status,
            "confirmation_score": int(_value(item, "confirmation_score", 0) or 0),
            "window_minutes": int(_value(item, "window_minutes", 0) or 0),
            "new_limit_up_count": int(_value(item, "new_limit_up_count", 0) or 0),
            "total_limit_up_count": int(_value(item, "total_limit_up_count", 0) or 0),
            "leaders": [str(value) for value in _list(item, "leaders")],
            "flow_turning": _value(item, "flow_turning"),
            "flow_speed": _value(item, "flow_speed"),
            "flow_acceleration": _value(item, "flow_acceleration"),
            "action": str(_value(item, "action", "") or ""),
            "invalidation": [str(value) for value in _list(item, "invalidation")],
            "buy_signal": False,
        }
        row, is_new = _upsert_transition(
            db,
            trade_date=trade_date,
            observed_at=expansion_as_of,
            scope="sector",
            target_code=_hash_key("sector", sector, 8),
            target_name=sector,
            event_type=event_type,
            severity="info",
            priority=64 if confirmed else 34,
            group_key=_hash_key("sector-expansion", sector, 16),
            confirmed=confirmed,
            value=float(metadata["confirmation_score"]),
            previous_value=float(_value(item, "net_inflow", 0) or 0),
            evidence=evidence or [f"{sector}进入盘中增量观察，等待新增涨停、订单流方向与价格共同确认。"],
            counter_evidence=counter,
            source=_sources(_value(item, "source") or _value(expansion, "source")),
            source_url=None,
            source_published_at=None,
            metadata=metadata,
        )
        if is_new:
            emitted.append(row)

    for item in _list(radar, "items"):
        news_id = str(_value(item, "id", "") or "").strip()
        title = str(_value(item, "title", "") or "").strip()
        if not news_id or not title:
            continue
        related_codes = list(dict.fromkeys(_code(value) for value in _list(item, "related_stocks") if _code(value)))
        holding_codes = [code for code in related_codes if code in holding_map]
        # General pending headlines remain in the opportunity-radar response;
        # only material transitions or holding-related claims enter the live
        # event stream, which prevents a title feed from becoming risk spam.
        news_status = str(_value(item, "news_impact_status", "UNVERIFIED") or "UNVERIFIED").upper()
        market_validation = str(_value(item, "market_validation", "PENDING") or "PENDING").upper()
        targets: list[tuple[str, str, str]] = [
            ("stock", code, holding_map[code]) for code in holding_codes
        ]
        if not targets and (news_status in {"IMPACT_CONFIRMED", "IMPACT_INVALIDATED"} or market_validation == "INVALIDATED"):
            sector = str(_value(item, "primary_sector", "") or "").strip()
            targets = [("sector" if sector else "market", _hash_key("news", news_id, 8), sector or "全市场资讯")]
        if not targets:
            continue

        published_at = _parse_datetime(_value(item, "published_at"))
        # The decision event clock is the source publication time, not the
        # crawler time.  Old headlines re-collected today and pre/post-market
        # articles stay in the news browser but cannot masquerade as today's
        # intraday causal evidence.
        if (
            published_at is None
            or published_at.date().isoformat() != trade_date
            or not (time(9, 15) <= published_at.time() <= time(15, 0))
        ):
            continue
        evidence = [str(value) for value in _list(item, "evidence") if str(value).strip()]
        counter = [str(value) for value in _list(item, "counter_evidence") if str(value).strip()]
        missing = [str(value) for value in _list(item, "missing") if str(value).strip()]
        if missing:
            counter.append("待补证据：" + "、".join(missing))
        for scope, target_code, target_name in targets:
            holding_related = scope == "stock"
            event_type, severity, priority, confirmed = _news_event_type(item, holding_related=holding_related)
            metadata = {
                "status": news_status,
                "title": title,
                "claim_level": str(_value(item, "claim_level", "RUMOR") or "RUMOR"),
                "market_validation": market_validation,
                "sentiment": str(_value(item, "sentiment", "待验证") or "待验证"),
                "sentiment_reason": str(_value(item, "sentiment_reason", "") or ""),
                "primary_sector": _value(item, "primary_sector"),
                "sectors": [str(value) for value in _list(item, "sectors")],
                "related_stocks": related_codes,
                "action": str(_value(item, "action", "") or ""),
                "trade_constraint": str(_value(item, "trade_constraint", "资讯不得单独触发交易。") or ""),
                "buy_signal": False,
            }
            row, is_new = _upsert_transition(
                db,
                trade_date=trade_date,
                observed_at=observed_at,
                scope=scope,
                target_code=target_code,
                target_name=target_name,
                event_type=event_type,
                severity=severity,
                priority=priority,
                group_key=_hash_key("news", f"{news_id}:{target_code}", 20),
                confirmed=confirmed,
                value=float(_value(item, "confirmation_score", 0) or 0),
                previous_value=0,
                evidence=evidence or ["消息仅形成待验证假设，尚无发布后的资金与量价共同确认。"],
                counter_evidence=counter,
                source=_sources(_value(item, "source")),
                source_url=_safe_source_url(_value(item, "url")),
                source_published_at=published_at,
                metadata=metadata,
            )
            if is_new:
                emitted.append(row)

    if emitted or db.dirty:
        db.commit()
        for row in emitted:
            db.refresh(row)
    return emitted


def persist_unified_market_events(
    db: Session,
    radar: Mapping[str, Any] | Any,
    holdings: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> list[IntradayEvidenceEvent]:
    # SQLite is the production store today.  Serialising writers in one worker
    # avoids avoidable "database is locked" errors; the unique state key still
    # provides the cross-worker/process correctness boundary.
    with _PERSIST_LOCK:
        return _persist_unified_market_events(db, radar, holdings, now=now)


__all__ = ["persist_unified_market_events"]
