"""Persistence helpers for sector crowding and global-market evidence.

The service stores provider facts and model conclusions exactly as observed. It
does not recalculate a top, fabricate a missing value, or treat T+1 financing as
intraday evidence.  Sector rows are daily upserts; global rows are immutable and
deduplicated by a stable content hash.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.trading import GlobalEvidenceSnapshot, SectorCrowdingDailySnapshot


_SHANGHAI_TZ = timezone(timedelta(hours=8))
_VOLATILE_GLOBAL_KEYS = {
    "captured_at",
    "fetched_at",
    "generated_at",
    "received_at",
    "server_observed_at",
}


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    if is_dataclass(value):
        return dict(asdict(value))
    try:
        return dict(vars(value))
    except TypeError:
        return {}


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return str(value)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_integer(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_boolean(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "是"}:
        return True
    if normalized in {"0", "false", "no", "n", "否"}:
        return False
    return None


def _text(value: Any, limit: int | None = None) -> str:
    if value is None:
        result = ""
    elif isinstance(value, (list, tuple, set)):
        result = " + ".join(str(item).strip() for item in value if str(item).strip())
    else:
        result = str(value).strip()
    return result[:limit] if limit is not None else result


def _json_array(value: Any) -> str:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            decoded = [value] if value.strip() else []
        value = decoded
    if value is None:
        value = []
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return _json(list(value))


def _datetime(value: Any) -> datetime | None:
    """Normalize an input timestamp to a Shanghai timezone-naive DB value.

    Naive inputs are interpreted as Shanghai wall-clock timestamps.  Aware
    values are converted to Asia/Shanghai first, which makes ordering and date
    derivation deterministic across providers using UTC or local offsets.
    """

    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_SHANGHAI_TZ).replace(tzinfo=None)
    return parsed


def _shanghai_now_naive() -> datetime:
    return datetime.now(_SHANGHAI_TZ).replace(tzinfo=None)


def _shanghai_date(value: datetime | None = None) -> str:
    observed = _datetime(value) if value is not None else _shanghai_now_naive()
    return (observed or _shanghai_now_naive()).date().isoformat()


def _date_text(value: Any) -> str:
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _normalized_board_name(board_name: str) -> str:
    normalized = unicodedata.normalize("NFKC", board_name or "")
    return re.sub(r"\s+", "", normalized).casefold()


def _board_key(board_code: str, board_name: str) -> str:
    # Name-first identity keeps a board stable when a provider intermittently
    # omits its code.  Code remains a fallback for code-only payloads and an
    # alias for resolving legacy/code-first rows during upsert.
    normalized_name = _normalized_board_name(board_name)
    if normalized_name:
        return f"name:{normalized_name}"
    normalized_code = board_code.strip().upper()
    if normalized_code:
        return f"code:{normalized_code}"
    raise ValueError("板块快照缺少有效的板块名称和板块代码")


def _sector_values(
    item: Mapping[str, Any],
    *,
    payload: Mapping[str, Any],
    captured_at: datetime,
) -> dict[str, Any]:
    board_type = _text(item.get("board_type") or payload.get("board_type") or "行业", 16)
    board_code = _text(item.get("board_code"), 32).upper()
    board_name = _text(item.get("name") or item.get("board_name"), 128)
    provider_trade_date = _date_text(item.get("provider_trade_date"))
    trade_date = (
        provider_trade_date
        or _date_text(item.get("trade_date"))
        or _date_text(payload.get("trade_date"))
        or _shanghai_date(captured_at)
    )
    source = _text(item.get("source") or payload.get("source"), 512)
    raw_envelope = {
        "trade_date": trade_date,
        "board_type": board_type,
        "source": source,
        "updated_at": payload.get("updated_at"),
        "item": dict(item),
    }
    return {
        "trade_date": trade_date,
        "board_type": board_type,
        "board_key": _board_key(board_code, board_name),
        "board_code": board_code,
        "board_name": board_name,
        "captured_at": captured_at,
        "source": source,
        "data_quality": _text(item.get("data_quality") or payload.get("data_quality") or "missing", 24),
        "provider_trade_date": provider_trade_date,
        "provider_updated_at": _datetime(item.get("provider_updated_at")),
        "heat_score": _optional_integer(item.get("heat_score")),
        "status": _text(item.get("status") or "数据不足", 64),
        "risk_level": _text(item.get("risk_level") or "UNKNOWN", 16),
        "trend_score": _optional_float(item.get("trend_score")),
        "flow_score": _optional_float(item.get("flow_score")),
        "crowding_score": _optional_float(item.get("crowding_score")),
        "margin_score": _optional_float(item.get("margin_score")),
        "attention_score": _optional_float(item.get("attention_score")),
        "change_pct": _optional_float(item.get("change_pct")),
        "change_pct_5d": _optional_float(item.get("change_pct_5d")),
        "change_pct_10d": _optional_float(item.get("change_pct_10d")),
        "net_inflow": _optional_float(item.get("net_inflow")),
        "net_inflow_5d": _optional_float(item.get("net_inflow_5d")),
        "net_inflow_10d": _optional_float(item.get("net_inflow_10d")),
        "flow_speed": _optional_float(item.get("flow_speed")),
        "flow_acceleration": _optional_float(item.get("flow_acceleration")),
        "flow_turning": _text(item.get("flow_turning"), 48),
        "limit_up_count": _optional_integer(item.get("limit_up_count")),
        "financing_balance": _optional_float(item.get("financing_balance")),
        "financing_net_buy": _optional_float(item.get("financing_net_buy")),
        "financing_balance_ratio": _optional_float(item.get("financing_balance_ratio")),
        "financing_net_buy_5d": _optional_float(item.get("financing_net_buy_5d")),
        "financing_net_buy_10d": _optional_float(item.get("financing_net_buy_10d")),
        "financing_net_buy_20d": _optional_float(item.get("financing_net_buy_20d")),
        "margin_as_of": _date_text(item.get("margin_as_of")),
        # Public board financing disclosures are T+1 slow variables.  Preserve
        # the upstream value in ``raw_payload_json`` for audit, but never expose
        # it as a real-time persisted fact even if a caller labels it wrongly.
        "margin_realtime": False,
        "distribution_state": _text(item.get("distribution_state") or "数据不足", 48),
        "distribution_risk_level": _text(item.get("distribution_risk_level") or "UNKNOWN", 16),
        "distribution_risk_score": _optional_float(item.get("distribution_risk_score")),
        "order_flow_exhausted": _optional_boolean(item.get("order_flow_exhausted")),
        "leverage_crowding": _optional_boolean(item.get("leverage_crowding")),
        "price_response_weak": _optional_boolean(item.get("price_response_weak")),
        "distribution_confirmation_count": _optional_integer(
            item.get("distribution_confirmation_count")
        ),
        "evidence_json": _json_array(item.get("evidence")),
        "counter_evidence_json": _json_array(item.get("counter_evidence")),
        "actions_json": _json_array(item.get("actions")),
        "distribution_evidence_json": _json_array(item.get("distribution_evidence")),
        "distribution_counter_evidence_json": _json_array(item.get("distribution_counter_evidence")),
        "distribution_actions_json": _json_array(item.get("distribution_actions")),
        "raw_payload_json": _json(raw_envelope),
        "payload_hash": _hash(raw_envelope),
    }


def _freshness_time(value: Mapping[str, Any] | SectorCrowdingDailySnapshot) -> datetime:
    if isinstance(value, Mapping):
        provider_updated_at = value.get("provider_updated_at")
        captured_at = value.get("captured_at")
    else:
        provider_updated_at = value.provider_updated_at
        captured_at = value.captured_at
    return (
        _datetime(provider_updated_at)
        or _datetime(captured_at)
        or datetime.min
    )


def _find_sector_row(
    db: Session,
    values: Mapping[str, Any],
    *,
    lock: bool,
) -> SectorCrowdingDailySnapshot | None:
    identity_filters = [
        SectorCrowdingDailySnapshot.board_key == values["board_key"],
    ]
    if values.get("board_code"):
        identity_filters.append(
            SectorCrowdingDailySnapshot.board_code == values["board_code"]
        )
    query = db.query(SectorCrowdingDailySnapshot).filter(
        SectorCrowdingDailySnapshot.trade_date == values["trade_date"],
        SectorCrowdingDailySnapshot.board_type == values["board_type"],
        or_(*identity_filters),
    )
    if lock:
        query = query.with_for_update()
    candidates = query.order_by(SectorCrowdingDailySnapshot.captured_at.desc()).all()
    for candidate in candidates:
        if candidate.board_key == values["board_key"]:
            return candidate
    return candidates[0] if candidates else None


def _apply_sector_values(
    row: SectorCrowdingDailySnapshot,
    values: Mapping[str, Any],
) -> None:
    update_values = dict(values)
    # Do not erase a stronger identity when a later provider response omits one
    # side of the name/code pair.  A code-first legacy row is promoted to the
    # normalized name key as soon as a name becomes available.
    if not update_values.get("board_code") and row.board_code:
        update_values["board_code"] = row.board_code
    if not update_values.get("board_name") and row.board_name:
        update_values["board_name"] = row.board_name
        update_values["board_key"] = row.board_key
    for field, value in update_values.items():
        setattr(row, field, value)
    row.updated_at = _shanghai_now_naive()


def _upsert_sector_values(
    db: Session,
    values: Mapping[str, Any],
) -> SectorCrowdingDailySnapshot:
    last_conflict: IntegrityError | None = None
    for _attempt in range(2):
        row = _find_sector_row(db, values, lock=True)
        if row is not None:
            if _freshness_time(values) < _freshness_time(row):
                return row
            try:
                with db.begin_nested():
                    _apply_sector_values(row, values)
                    db.flush()
                return row
            except IntegrityError as exc:
                # A code-first row may race with a canonical name-key insert.
                # The savepoint keeps earlier rows in this batch intact.
                last_conflict = exc
                db.expire_all()
                continue

        row = SectorCrowdingDailySnapshot(
            **values,
            created_at=_shanghai_now_naive(),
            updated_at=_shanghai_now_naive(),
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
            return row
        except IntegrityError as exc:
            # Query-before-insert is inherently racy.  Retry once after the
            # conflicting transaction becomes visible, without rolling back
            # the caller's entire Session transaction.
            last_conflict = exc
            db.expire_all()

    concurrent = _find_sector_row(db, values, lock=True)
    if concurrent is not None:
        if _freshness_time(values) >= _freshness_time(concurrent):
            with db.begin_nested():
                _apply_sector_values(concurrent, values)
                db.flush()
        return concurrent
    if last_conflict is not None:
        raise last_conflict
    raise RuntimeError("板块快照写入失败，且未找到并发写入记录")


def persist_sector_temperature_snapshot(
    db: Session,
    payload: Mapping[str, Any] | Any,
) -> list[SectorCrowdingDailySnapshot]:
    """Upsert the newest daily state for every valid sector in ``payload``.

    This public helper is an application unit-of-work boundary and commits on
    success, matching its existing collectors.  Uniqueness races are isolated
    with savepoints so a retry does not require a full Session rollback.  A
    caller must therefore not stage unrelated writes in the same Session.
    """

    data = _mapping(payload)
    items = [_mapping(item) for item in data.get("items") or []]
    if not items:
        return []
    captured_at = (
        _datetime(data.get("updated_at") or data.get("captured_at"))
        or _shanghai_now_naive()
    )
    # Build and validate the entire batch before starting writes.  An invalid
    # anonymous board therefore cannot leave a partially persisted payload.
    values_list = [
        _sector_values(item, payload=data, captured_at=captured_at)
        for item in items
    ]
    latest_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for values in values_list:
        key = (values["trade_date"], values["board_type"], values["board_key"])
        previous = latest_by_key.get(key)
        if previous is None or _freshness_time(values) >= _freshness_time(previous):
            latest_by_key[key] = values

    persisted = [
        _upsert_sector_values(db, values)
        for values in latest_by_key.values()
    ]

    db.commit()
    unique_rows: list[SectorCrowdingDailySnapshot] = []
    seen_ids: set[int] = set()
    for row in persisted:
        if row.id in seen_ids:
            continue
        seen_ids.add(row.id)
        db.refresh(row)
        unique_rows.append(row)
    return unique_rows


def load_sector_history(
    db: Session,
    *,
    board_type: str | None = None,
    board_code: str | None = None,
    board_name: str | None = None,
    board_key: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 120,
    ascending: bool = False,
) -> list[SectorCrowdingDailySnapshot]:
    """Load a bounded, deterministic sector history for scoring or evidence UI."""

    query = db.query(SectorCrowdingDailySnapshot)
    if board_type:
        query = query.filter(SectorCrowdingDailySnapshot.board_type == board_type)
    if board_code:
        query = query.filter(SectorCrowdingDailySnapshot.board_code == board_code.strip().upper())
    if board_name:
        query = query.filter(SectorCrowdingDailySnapshot.board_name == board_name.strip())
    if board_key:
        query = query.filter(SectorCrowdingDailySnapshot.board_key == board_key.strip())
    if start_date:
        query = query.filter(SectorCrowdingDailySnapshot.trade_date >= start_date[:10])
    if end_date:
        query = query.filter(SectorCrowdingDailySnapshot.trade_date <= end_date[:10])
    order = (
        (SectorCrowdingDailySnapshot.trade_date.asc(), SectorCrowdingDailySnapshot.captured_at.asc())
        if ascending
        else (SectorCrowdingDailySnapshot.trade_date.desc(), SectorCrowdingDailySnapshot.captured_at.desc())
    )
    return query.order_by(*order).limit(max(1, min(int(limit), 1000))).all()


def _global_hash_material(data: Mapping[str, Any], source: str, quality: str) -> dict[str, Any]:
    nested = data.get("payload")
    body = dict(nested) if isinstance(nested, Mapping) else dict(data)
    for key in _VOLATILE_GLOBAL_KEYS:
        body.pop(key, None)
    # Top-level ``as_of`` is often the local collection time.  Provider quote
    # as-of values inside the item lists remain part of the hash.
    body.pop("as_of", None)
    body.pop("source", None)
    body.pop("sources", None)
    body.pop("quality", None)
    body.pop("data_quality", None)
    return {"source": source, "quality": quality, "payload": body}


def persist_global_evidence_snapshot(
    db: Session,
    payload: Mapping[str, Any] | Any,
) -> GlobalEvidenceSnapshot:
    """Persist one immutable global envelope, deduplicated within a trade day.

    Like the sector helper, this function commits its application unit of work.
    Insert races are contained in a savepoint; they never trigger a full
    ``Session.rollback()`` that could discard earlier collector writes.
    """

    data = _mapping(payload)
    source = _text(data.get("source") or data.get("sources"), 512)
    as_of = _text(data.get("as_of") or data.get("generated_at") or data.get("updated_at"), 64)
    quality = _text(data.get("data_quality") or data.get("quality") or "missing", 24)
    payload_hash = _hash(_global_hash_material(data, source, quality))
    observed_at = _datetime(as_of) or _shanghai_now_naive()
    trade_date = _date_text(data.get("trade_date")) or _shanghai_date(observed_at)
    existing = (
        db.query(GlobalEvidenceSnapshot)
        .filter(
            GlobalEvidenceSnapshot.trade_date == trade_date,
            GlobalEvidenceSnapshot.payload_hash == payload_hash,
        )
        .first()
    )
    if existing is not None:
        db.commit()
        db.refresh(existing)
        return existing

    row = GlobalEvidenceSnapshot(
        trade_date=trade_date,
        captured_at=_shanghai_now_naive(),
        as_of=as_of,
        source=source,
        data_quality=quality,
        payload_hash=payload_hash,
        payload_json=_json(data),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        db.expire_all()
        # The only expected conflict is the same content arriving twice on the
        # same trade date.  Retrying the lookup preserves cross-day history.
        concurrent = (
            db.query(GlobalEvidenceSnapshot)
            .filter(
                GlobalEvidenceSnapshot.trade_date == trade_date,
                GlobalEvidenceSnapshot.payload_hash == payload_hash,
            )
            .first()
        )
        if concurrent is None:
            raise
        row = concurrent

    try:
        db.commit()
    except Exception:
        # Commit failures are not uniqueness retries and must remain visible to
        # the caller; the helper intentionally avoids an implicit full rollback.
        raise
    db.refresh(row)
    return row


__all__ = [
    "load_sector_history",
    "persist_global_evidence_snapshot",
    "persist_sector_temperature_snapshot",
]
