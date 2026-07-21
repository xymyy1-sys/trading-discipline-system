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
import math
import re
import unicodedata
from typing import Any, Mapping

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.trading import (
    GlobalEvidenceSnapshot,
    SectorCrowdingDailySnapshot,
    SectorCrowdingSnapshotSample,
)
from app.services.trading_calendar import (
    is_a_share_trading_day,
    previous_a_share_trading_day,
)


_SHANGHAI_TZ = timezone(timedelta(hours=8))
_VOLATILE_GLOBAL_KEYS = {
    "captured_at",
    "fetched_at",
    "generated_at",
    "observed_at",
    "received_at",
    "server_observed_at",
    "snapshot_id",
    "snapshot_origin",
    "persisted_at",
}


def _without_global_collection_metadata(value: Any) -> Any:
    """Remove collector timestamps recursively while retaining market facts.

    Provider ``published_at`` and quote ``as_of`` fields deliberately remain in
    the material: changing either means the provider exposed a new fact.  In
    contrast, collector-owned timestamps (including an adapter's nested
    ``observed_at``) must not create a second immutable row for the same facts.
    """

    if isinstance(value, Mapping):
        return {
            str(key): _without_global_collection_metadata(item)
            for key, item in value.items()
            if str(key) not in _VOLATILE_GLOBAL_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_without_global_collection_metadata(item) for item in value]
    return value

_STRICT_SECTOR_STATES = (
    "健康增量",
    "杠杆追涨观察",
    "资金承载衰减",
    "高位派发风险",
    "去杠杆踩踏",
    "超跌企稳观察",
)
_STRICT_STATE_ALIASES = {
    "健康": "健康增量",
    "健康增量": "健康增量",
    "杠杆追涨": "杠杆追涨观察",
    "杠杆追涨观察": "杠杆追涨观察",
    "资金承载衰减": "资金承载衰减",
    "高位派发警戒": "高位派发风险",
    "高位派发风险": "高位派发风险",
    "去杠杆踩踏": "去杠杆踩踏",
    "超跌企稳": "超跌企稳观察",
    "超跌企稳观察": "超跌企稳观察",
    "HEALTHY_INCREMENT": "健康增量",
    "LEVERAGE_CHASING_WATCH": "杠杆追涨观察",
    "CAPITAL_ABSORPTION_DECAY": "资金承载衰减",
    "HIGH_LEVEL_DISTRIBUTION_RISK": "高位派发风险",
    "DELEVERAGING_STAMPEDE": "去杠杆踩踏",
    "OVERSOLD_STABILIZATION_WATCH": "超跌企稳观察",
}

# Immutable samples represent provider-visible market observations.  Historical
# enrichments are intentionally excluded so a later recalculation of slopes,
# percentiles or persistence counters cannot create a self-confirming sample.
_SECTOR_SAMPLE_FACT_KEYS = {
    "provider_trade_date",
    "data_quality",
    "change_pct",
    "net_inflow",
    "flow_speed",
    "flow_acceleration",
    "flow_turning",
    "flow_ratio",
    "order_flow_turnover_ratio",
    "turnover_amount",
    "sector_turnover_amount",
    "turnover_complete",
    "sector_turnover_complete",
    "leader_change_pct",
    "leader_divergence_pct",
    "advance_count",
    "decline_count",
    "constituent_count",
    "new_high_count",
    "promotion_rate",
    "break_rate",
    "sector_price",
    "sector_vwap",
    "sector_vwap_reliable",
    "limit_up_count",
    "financing_balance",
    "financing_buy",
    "financing_net_buy",
    "financing_balance_ratio",
    "margin_as_of",
    "non_leveraged_net_inflow",
    "non_leveraged_flow_audited",
    "non_leveraged_net_inflow_unit",
    "non_leveraged_methodology_id",
    "etf_share_net_change",
    "etf_share_change_pct",
    "etf_flow_audited",
    "etf_id",
    "etf_share_unit",
    "etf_share_base",
    "etf_methodology_id",
}

_DEFAULT_CONFIRMATION_MIN_INTERVAL_SECONDS = 5 * 60
_DEFAULT_CARRYING_MIN_INTERVAL_SECONDS = 5 * 60
_DEFAULT_CARRYING_MIN_TRANSITIONS = 3
_DEFAULT_CARRYING_MIN_SPAN_SECONDS = 15 * 60


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


def _turnover_archive_complete(
    item: Mapping[str, Any],
    *,
    provider_trade_date: str,
    provider_updated_at: datetime | None,
    data_quality: str,
) -> bool:
    """Return whether daily turnover is known to include the full session.

    An explicit provider flag takes precedence.  Without one, completion is
    inferred only from a valid provider timestamp at/after the A-share close
    on the same trade date.  Collection time is deliberately not used because
    replaying an old intraday cache after 15:00 would otherwise look complete.
    """

    explicit = None
    for key in (
        "turnover_complete",
        "sector_turnover_complete",
        "session_complete",
        "is_close_snapshot",
    ):
        if key in item:
            explicit = _optional_boolean(item.get(key))
            break
    if explicit is not None:
        return explicit
    if data_quality.strip().lower() not in {"high", "good", "complete", "realtime"}:
        return False
    if not provider_trade_date or provider_updated_at is None:
        return False
    return bool(
        provider_updated_at.date().isoformat() == provider_trade_date
        and provider_updated_at.time() >= datetime.min.replace(hour=15).time()
    )


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
    data_quality = _text(
        item.get("data_quality") or payload.get("data_quality") or "missing",
        24,
    )
    provider_updated_at = _datetime(item.get("provider_updated_at"))
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
        "data_quality": data_quality,
        "provider_trade_date": provider_trade_date,
        "provider_updated_at": provider_updated_at,
        "turnover_complete": _turnover_archive_complete(
            item,
            provider_trade_date=provider_trade_date,
            provider_updated_at=provider_updated_at,
            data_quality=data_quality,
        ),
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


def _sector_sample_identity_hash(values: Mapping[str, Any]) -> str:
    try:
        envelope = json.loads(str(values.get("raw_payload_json") or "{}"))
    except (TypeError, ValueError):
        envelope = {}
    item = envelope.get("item") if isinstance(envelope, dict) else None
    item = item if isinstance(item, dict) else {}
    facts = {
        key: item[key]
        for key in sorted(_SECTOR_SAMPLE_FACT_KEYS)
        if key in item and item[key] is not None
    }
    facts["instantaneous_distribution_state"] = (
        item.get("instantaneous_distribution_state")
        or item.get("distribution_state")
        or ""
    )
    material = {
        "trade_date": values.get("trade_date"),
        "board_type": values.get("board_type"),
        "board_key": values.get("board_key"),
        "facts": facts,
    }
    return _hash(material)


def _sample_values(values: Mapping[str, Any]) -> dict[str, Any]:
    """Select the auditable subset stored for one immutable observation."""

    distribution_evidence = values.get("distribution_evidence_json") or "[]"
    distribution_counter = values.get("distribution_counter_evidence_json") or "[]"
    distribution_actions = values.get("distribution_actions_json") or "[]"
    try:
        raw_envelope = json.loads(str(values.get("raw_payload_json") or "{}"))
    except (TypeError, ValueError):
        raw_envelope = {}
    raw_item = raw_envelope.get("item") if isinstance(raw_envelope, dict) else None
    raw_item = raw_item if isinstance(raw_item, dict) else {}
    instantaneous_state = _text(
        raw_item.get("instantaneous_distribution_state")
        or raw_item.get("distribution_state")
        or values.get("distribution_state")
        or "数据不足",
        48,
    )
    return {
        "trade_date": values["trade_date"],
        "board_type": values["board_type"],
        "board_key": values["board_key"],
        "board_code": values.get("board_code") or "",
        "board_name": values.get("board_name") or "",
        "captured_at": values["captured_at"],
        "provider_updated_at": values.get("provider_updated_at"),
        "source": values.get("source") or "",
        "data_quality": values.get("data_quality") or "missing",
        "status": values.get("status") or "数据不足",
        "risk_level": values.get("risk_level") or "UNKNOWN",
        "distribution_state": values.get("distribution_state") or "数据不足",
        # Keep the state calculated from this provider envelope separately
        # from the persistence-gated state exposed by the final builder.  A
        # high-risk state may be temporarily downgraded until another sample
        # confirms it; storing only that downgraded value would make genuine
        # confirmation impossible on the next collection.
        "instantaneous_distribution_state": instantaneous_state,
        "distribution_risk_level": values.get("distribution_risk_level") or "UNKNOWN",
        "distribution_risk_score": values.get("distribution_risk_score"),
        "distribution_confirmation_count": values.get("distribution_confirmation_count"),
        "change_pct": values.get("change_pct"),
        "net_inflow": values.get("net_inflow"),
        "flow_speed": values.get("flow_speed"),
        "flow_acceleration": values.get("flow_acceleration"),
        "flow_turning": values.get("flow_turning") or "",
        "financing_balance": values.get("financing_balance"),
        "financing_net_buy": values.get("financing_net_buy"),
        "margin_as_of": values.get("margin_as_of") or "",
        "evidence_json": distribution_evidence if distribution_evidence != "[]" else values.get("evidence_json") or "[]",
        "counter_evidence_json": distribution_counter if distribution_counter != "[]" else values.get("counter_evidence_json") or "[]",
        "actions_json": distribution_actions if distribution_actions != "[]" else values.get("actions_json") or "[]",
        "raw_payload_json": values.get("raw_payload_json") or "{}",
        "payload_hash": _sector_sample_identity_hash(values),
        "created_at": _shanghai_now_naive(),
    }


def _persist_sector_sample(
    db: Session,
    values: Mapping[str, Any],
) -> SectorCrowdingSnapshotSample:
    sample_values = _sample_values(values)
    identity = (
        SectorCrowdingSnapshotSample.trade_date == sample_values["trade_date"],
        SectorCrowdingSnapshotSample.board_type == sample_values["board_type"],
        SectorCrowdingSnapshotSample.board_key == sample_values["board_key"],
        SectorCrowdingSnapshotSample.payload_hash == sample_values["payload_hash"],
    )
    existing = db.query(SectorCrowdingSnapshotSample).filter(*identity).one_or_none()
    if existing is not None:
        return existing
    try:
        with db.begin_nested():
            row = SectorCrowdingSnapshotSample(**sample_values)
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        # A concurrent collector can persist the same immutable provider
        # envelope.  The unique constraint makes this idempotent.
        db.expire_all()
        concurrent = db.query(SectorCrowdingSnapshotSample).filter(*identity).one_or_none()
        if concurrent is not None:
            return concurrent
        raise


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

    # Preserve each materially distinct collection before updating the
    # backwards-compatible end-of-day summary.  The sample table is immutable;
    # repeated reads of the exact same cached envelope are idempotent.
    for values in latest_by_key.values():
        _persist_sector_sample(db, values)

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


def load_latest_sector_temperature_snapshot(
    db: Session,
    *,
    board_type: str,
) -> dict[str, Any] | None:
    """Rehydrate the latest persisted sector panel without a provider call.

    The daily table stores the exact item returned to the caller in
    ``raw_payload_json``.  A process restart may clear the response cache, but
    it must not make that already-audited panel disappear.  No missing board or
    metric is reconstructed here; malformed archived rows are simply skipped.
    """

    normalized = "概念" if board_type == "概念" else "行业"
    latest = (
        db.query(SectorCrowdingDailySnapshot)
        .filter(SectorCrowdingDailySnapshot.board_type == normalized)
        .order_by(
            SectorCrowdingDailySnapshot.trade_date.desc(),
            SectorCrowdingDailySnapshot.provider_updated_at.desc(),
            SectorCrowdingDailySnapshot.captured_at.desc(),
            SectorCrowdingDailySnapshot.id.desc(),
        )
        .first()
    )
    if latest is None:
        return None
    rows = (
        db.query(SectorCrowdingDailySnapshot)
        .filter(
            SectorCrowdingDailySnapshot.board_type == normalized,
            SectorCrowdingDailySnapshot.trade_date == latest.trade_date,
        )
        .order_by(
            SectorCrowdingDailySnapshot.heat_score.desc(),
            SectorCrowdingDailySnapshot.board_key.asc(),
        )
        .all()
    )
    items = [_raw_item(row) for row in rows]
    items = [item for item in items if item.get("name") or item.get("board_name")]
    if not items:
        return None
    observed_times = [
        row.provider_updated_at or row.captured_at
        for row in rows
        if row.provider_updated_at or row.captured_at
    ]
    updated_at = max(observed_times) if observed_times else latest.captured_at
    sources = list(dict.fromkeys(row.source for row in rows if row.source))
    source = "+".join(sources) or "persisted-sector-evidence"
    statuses = lambda accepted: [
        item for item in items if str(item.get("status") or "") in accepted
    ]
    now = _shanghai_now_naive()
    age_minutes = max(0, int((now - updated_at).total_seconds() // 60))
    return {
        "source": f"数据库最近板块证据快照（{source}）",
        "updated_at": updated_at,
        "board_type": normalized,
        "lookback_windows": [1, 5, 10, 20],
        "items": items,
        "overheated": statuses({"过热分歧", "过热兑现风险"}),
        "stabilizing": statuses({"过冷企稳观察", "修复初步确认"}),
        "oversold_watch": statuses({"过冷仍下跌", "过冷企稳观察"}),
        "notes": [
            f"进程缓存为空，已恢复交易日 {latest.trade_date} 的数据库快照；"
            f"快照年龄约 {age_minutes} 分钟，本次未调用外部数据源。"
        ],
    }


def load_sector_samples(
    db: Session,
    *,
    board_type: str | None = None,
    board_code: str | None = None,
    board_name: str | None = None,
    board_key: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 500,
    ascending: bool = False,
) -> list[SectorCrowdingSnapshotSample]:
    """Load immutable samples without recreating unavailable history."""

    query = db.query(SectorCrowdingSnapshotSample)
    if board_type:
        query = query.filter(SectorCrowdingSnapshotSample.board_type == board_type)
    if board_code:
        query = query.filter(SectorCrowdingSnapshotSample.board_code == board_code.strip().upper())
    if board_name:
        query = query.filter(SectorCrowdingSnapshotSample.board_name == board_name.strip())
    if board_key:
        query = query.filter(SectorCrowdingSnapshotSample.board_key == board_key.strip())
    if start_date:
        query = query.filter(SectorCrowdingSnapshotSample.trade_date >= start_date[:10])
    if end_date:
        query = query.filter(SectorCrowdingSnapshotSample.trade_date <= end_date[:10])
    ordering = (
        (SectorCrowdingSnapshotSample.captured_at.asc(), SectorCrowdingSnapshotSample.id.asc())
        if ascending
        else (SectorCrowdingSnapshotSample.captured_at.desc(), SectorCrowdingSnapshotSample.id.desc())
    )
    return query.order_by(*ordering).limit(max(1, min(int(limit), 5000))).all()


def _strict_state(value: Any) -> str:
    text = _text(value, 64)
    if text in _STRICT_STATE_ALIASES:
        return _STRICT_STATE_ALIASES[text]
    upper = text.upper()
    if upper in _STRICT_STATE_ALIASES:
        return _STRICT_STATE_ALIASES[upper]
    return ""


def _array(raw: str) -> list[object]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        value = []
    return value if isinstance(value, list) else []


def _raw_item(row: SectorCrowdingDailySnapshot | SectorCrowdingSnapshotSample) -> dict[str, Any]:
    """Return the exact archived item without filling or estimating fields."""

    try:
        envelope = json.loads(row.raw_payload_json or "{}")
    except (TypeError, ValueError):
        return {}
    item = envelope.get("item") if isinstance(envelope, dict) else None
    return item if isinstance(item, dict) else {}


def _sample_state(row: SectorCrowdingSnapshotSample) -> str:
    """Resolve the instantaneous state that was visible at sample time."""

    state = _strict_state(row.instantaneous_distribution_state)
    if state:
        return state
    state = _strict_state(_raw_item(row).get("instantaneous_distribution_state"))
    return state or _strict_state(row.distribution_state)


def _daily_state(row: SectorCrowdingDailySnapshot) -> str:
    """Resolve the daily envelope's ungated state for cross-day confirmation."""

    item = _raw_item(row)
    state = _strict_state(item.get("instantaneous_distribution_state"))
    return state or _strict_state(row.distribution_state)


def _eligible_confirmation_sample(row: SectorCrowdingSnapshotSample) -> bool:
    quality = (row.data_quality or "").strip().lower()
    return bool(_sample_state(row)) and quality not in {
        "",
        "missing",
        "unavailable",
        "error",
        "stale",
    }


def _consecutive_sample_count(
    rows: list[SectorCrowdingSnapshotSample],
    state: str,
    *,
    min_interval_seconds: int = _DEFAULT_CONFIRMATION_MIN_INTERVAL_SECONDS,
) -> int:
    if not rows or not state:
        return 0
    current_date = rows[0].trade_date
    count = 0
    seen_fingerprints: set[str] = set()
    newest_accepted_at: datetime | None = None
    minimum_gap = max(0, int(min_interval_seconds))
    for row in rows:
        if row.trade_date != current_date:
            break
        if not _eligible_confirmation_sample(row) or _sample_state(row) != state:
            break
        # The immutable payload hash is a facts-only fingerprint.  A provider
        # timestamp change or a manual refresh therefore cannot manufacture a
        # second confirmation point.
        fingerprint = (row.payload_hash or "").strip()
        if not fingerprint or fingerprint in seen_fingerprints:
            continue
        seen_fingerprints.add(fingerprint)
        observed_at = row.provider_updated_at or row.captured_at
        if newest_accepted_at is not None:
            gap = (newest_accepted_at - observed_at).total_seconds()
            if gap < minimum_gap:
                continue
        if newest_accepted_at is None:
            newest_accepted_at = observed_at
        elif observed_at >= newest_accepted_at:
            continue
        else:
            newest_accepted_at = observed_at
        count += 1
    return count


def _consecutive_trading_day_count(
    rows: list[SectorCrowdingDailySnapshot],
    state: str,
) -> int:
    count = 0
    seen_dates: set[str] = set()
    newer_date: date | None = None
    for row in rows:
        if row.trade_date in seen_dates:
            continue
        seen_dates.add(row.trade_date)
        try:
            current_date = date.fromisoformat(row.trade_date)
        except ValueError:
            break
        if not is_a_share_trading_day(current_date):
            break
        if (
            newer_date is not None
            and previous_a_share_trading_day(newer_date) != current_date
        ):
            break
        quality = (row.data_quality or "").strip().lower()
        if quality in {"", "missing", "unavailable", "error", "stale"}:
            break
        if _daily_state(row) != state:
            break
        count += 1
        newer_date = current_date
    return count


def build_sector_state_evolution(
    db: Session,
    *,
    board_type: str | None = None,
    board_code: str | None = None,
    board_name: str | None = None,
    sample_limit: int = 8,
    board_limit: int = 20,
    min_sample_interval_seconds: int = _DEFAULT_CONFIRMATION_MIN_INTERVAL_SECONDS,
) -> list[dict[str, Any]]:
    """Return observed state paths and persistence, never inferred samples.

    Persistence is confirmed by either two distinct eligible intraday samples
    or two consecutive stored trading-day summaries.  Missing/stale samples
    interrupt the chain instead of being silently skipped.
    """

    sample_limit = max(1, min(int(sample_limit), 24))
    board_limit = max(1, min(int(board_limit), 100))
    samples = load_sector_samples(
        db,
        board_type=board_type,
        board_code=board_code,
        board_name=board_name,
        limit=max(500, sample_limit * board_limit * 4),
        ascending=False,
    )
    grouped: dict[tuple[str, str], list[SectorCrowdingSnapshotSample]] = {}
    for row in samples:
        key = (row.board_type, row.board_key)
        if key not in grouped and len(grouped) >= board_limit:
            continue
        bucket = grouped.setdefault(key, [])
        bucket.append(row)
    for key, bucket in grouped.items():
        bucket.sort(
            key=lambda row: (row.provider_updated_at or row.captured_at, row.captured_at, row.id),
            reverse=True,
        )
        grouped[key] = bucket[:sample_limit]

    output: list[dict[str, Any]] = []
    for (_type, board_key), rows in grouped.items():
        if not rows:
            continue
        latest = rows[0]
        current_state = _sample_state(latest)
        sample_count = _consecutive_sample_count(
            rows,
            current_state,
            min_interval_seconds=min_sample_interval_seconds,
        )
        daily_rows = load_sector_history(
            db,
            board_type=latest.board_type,
            board_key=board_key,
            limit=20,
            ascending=False,
        )
        trading_day_count = _consecutive_trading_day_count(daily_rows, current_state)
        sample_confirmed = sample_count >= 2
        trading_day_confirmed = trading_day_count >= 2
        output.append({
            "board_type": latest.board_type,
            "board_code": latest.board_code,
            "name": latest.board_name,
            "strict_state": current_state or "数据不足",
            "raw_state": latest.instantaneous_distribution_state or latest.distribution_state,
            "resolved_state": latest.distribution_state,
            "risk_level": latest.distribution_risk_level,
            "data_as_of": latest.provider_updated_at or latest.captured_at,
            "sample_confirmation_count": sample_count,
            "sample_confirmation_min_interval_seconds": max(
                0, int(min_sample_interval_seconds)
            ),
            "trading_day_confirmation_count": trading_day_count,
            "sample_confirmed": sample_confirmed,
            "trading_day_confirmed": trading_day_confirmed,
            "persistence_confirmed": sample_confirmed or trading_day_confirmed,
            "confirmation_basis": [
                *([f"盘中连续 {sample_count} 个有效采样点保持同一状态"] if sample_confirmed else []),
                *([f"连续 {trading_day_count} 个交易日保持同一状态"] if trading_day_confirmed else []),
            ],
            "samples": [
                {
                    "trade_date": row.trade_date,
                    "captured_at": row.captured_at,
                    "provider_updated_at": row.provider_updated_at,
                    "data_quality": row.data_quality,
                    "strict_state": _sample_state(row) or "数据不足",
                    "raw_state": row.instantaneous_distribution_state or row.distribution_state,
                    "resolved_state": row.distribution_state,
                    "risk_level": row.distribution_risk_level,
                    "risk_score": row.distribution_risk_score,
                    "change_pct": row.change_pct,
                    "net_inflow": row.net_inflow,
                    "flow_speed": row.flow_speed,
                    "flow_acceleration": row.flow_acceleration,
                    "flow_turning": row.flow_turning,
                    "margin_as_of": row.margin_as_of,
                    "evidence": _array(row.evidence_json),
                    "counter_evidence": _array(row.counter_evidence_json),
                    "actions": _array(row.actions_json),
                }
                for row in reversed(rows)
            ],
            "daily_states": [
                {
                    "trade_date": row.trade_date,
                    "strict_state": _daily_state(row) or "数据不足",
                    "raw_state": (
                        _raw_item(row).get("instantaneous_distribution_state")
                        or row.distribution_state
                    ),
                    "resolved_state": row.distribution_state,
                    "risk_level": row.distribution_risk_level,
                    "data_quality": row.data_quality,
                }
                for row in reversed(daily_rows[:10])
            ],
        })
    return output


def _raw_item_metric(row: SectorCrowdingDailySnapshot, key: str) -> float | None:
    """Read an exact provider/model field from the archived envelope."""

    return _optional_float(_raw_item(row).get(key))


def _linear_slope(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    ys = values[-window:]
    mean_x = (window - 1) / 2
    mean_y = sum(ys) / window
    denominator = sum((index - mean_x) ** 2 for index in range(window))
    if denominator <= 0:
        return None
    numerator = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(ys)
    )
    return round(numerator / denominator, 4)


def _complete_metric_window(
    points: list[tuple[str, float | None]],
    window: int,
) -> list[float] | None:
    """Return the latest complete disclosed window, or fail closed.

    ``points`` contains every archived disclosure date, including dates whose
    metric is missing.  Missing values are therefore not silently skipped to
    create a synthetic continuous 5/10/20-day series.
    """

    if len(points) < window:
        return None
    tail = points[-window:]
    if not _complete_disclosure_sequence(tail):
        return None
    if any(value is None for _day, value in tail):
        return None
    return [float(value) for _day, value in tail if value is not None]


def _complete_disclosure_sequence(
    points: list[tuple[str, float | None]],
) -> bool:
    """Require one observation for every consecutive A-share trading day."""

    if not points:
        return False
    parsed: list[date] = []
    try:
        parsed = [date.fromisoformat(day[:10]) for day, _value in points]
    except ValueError:
        return False
    if len(set(parsed)) != len(parsed):
        return False
    if any(not is_a_share_trading_day(day) for day in parsed):
        return False
    return all(
        previous_a_share_trading_day(current) == previous
        for previous, current in zip(parsed, parsed[1:])
    )


def _complete_linear_slope(
    points: list[tuple[str, float | None]],
    window: int,
) -> float | None:
    values = _complete_metric_window(points, window)
    return _linear_slope(values, window) if values is not None else None


def _complete_historical_percentile(
    points: list[tuple[str, float | None]],
    window: int,
) -> float | None:
    values = _complete_metric_window(points, window)
    return _historical_percentile(values, window) if values is not None else None


def _sample_observed_at(row: SectorCrowdingSnapshotSample) -> datetime:
    return row.provider_updated_at or row.captured_at


def _capital_price_carrying_metrics(
    rows: list[SectorCrowdingSnapshotSample],
    *,
    min_interval_seconds: int = _DEFAULT_CARRYING_MIN_INTERVAL_SECONDS,
    min_transitions: int = _DEFAULT_CARRYING_MIN_TRANSITIONS,
    min_span_seconds: int = _DEFAULT_CARRYING_MIN_SPAN_SECONDS,
    rolling_window: int = 6,
) -> dict[str, Any]:
    """Calculate rolling capital/price carrying from immutable intraday facts.

    The series uses changes between time-spaced observations: change in board
    return versus change in order-flow/turnover ratio.  It never combines the
    current, 5-day and 10-day aggregate rows as if those were observations.
    Insufficient transitions or elapsed time returns ``None`` instead of a
    low-confidence score.
    """

    ordered = sorted(
        rows,
        key=lambda row: (_sample_observed_at(row), row.captured_at, row.id),
    )
    if not ordered:
        return {
            "capital_price_carrying_efficiency": None,
            "capital_price_carrying_sample_count": 0,
            "capital_price_carrying_span_minutes": None,
            "capital_price_carrying_slope": None,
            "capital_price_carrying_method": "immutable_intraday_delta_rolling",
        }

    # Carrying efficiency is intraday.  Do not splice yesterday's close into
    # today's open as though it were a continuous five-minute transition.
    latest_trade_date = ordered[-1].trade_date
    points: list[tuple[datetime, float, float]] = []
    seen_hashes: set[str] = set()
    last_accepted_at: datetime | None = None
    minimum_gap = max(0, int(min_interval_seconds))
    for row in ordered:
        if row.trade_date != latest_trade_date:
            continue
        quality = (row.data_quality or "").strip().lower()
        if quality in {"", "missing", "unavailable", "error", "stale"}:
            continue
        fingerprint = (row.payload_hash or "").strip()
        if not fingerprint or fingerprint in seen_hashes:
            continue
        seen_hashes.add(fingerprint)
        observed_at = _sample_observed_at(row)
        if last_accepted_at is not None:
            elapsed = (observed_at - last_accepted_at).total_seconds()
            if elapsed < minimum_gap:
                continue
        price_change = _optional_float(_raw_item(row).get("change_pct"))
        if price_change is None:
            price_change = row.change_pct
        item = _raw_item(row)
        flow_ratio = _optional_float(
            item.get("flow_ratio")
            if item.get("flow_ratio") is not None
            else item.get("order_flow_turnover_ratio")
        )
        if price_change is None or flow_ratio is None:
            continue
        points.append((observed_at, float(price_change), float(flow_ratio)))
        last_accepted_at = observed_at

    transition_scores: list[float] = []
    for previous, current in zip(points, points[1:]):
        _previous_at, previous_price, previous_flow = previous
        _current_at, current_price, current_flow = current
        flow_delta = current_flow - previous_flow
        if abs(flow_delta) < 0.05:
            continue
        price_delta = current_price - previous_price
        directional_response = price_delta * (1.0 if flow_delta > 0 else -1.0)
        normalized_response = directional_response / max(abs(flow_delta), 0.10)
        transition_scores.append(
            max(0.0, min(100.0, 50.0 + 50.0 * math.tanh(normalized_response / 2.0)))
        )

    required_transitions = max(1, int(min_transitions))
    span_seconds = (
        (points[-1][0] - points[0][0]).total_seconds()
        if len(points) >= 2
        else 0.0
    )
    if (
        len(transition_scores) < required_transitions
        or span_seconds < max(0, int(min_span_seconds))
    ):
        return {
            "capital_price_carrying_efficiency": None,
            "capital_price_carrying_sample_count": len(transition_scores),
            "capital_price_carrying_span_minutes": round(span_seconds / 60.0, 2)
            if points else None,
            "capital_price_carrying_slope": None,
            "capital_price_carrying_method": "immutable_intraday_delta_rolling",
        }

    window = max(1, min(int(rolling_window), len(transition_scores)))
    rolling_scores = transition_scores[-window:]
    return {
        "capital_price_carrying_efficiency": round(
            sum(rolling_scores) / len(rolling_scores), 2
        ),
        "capital_price_carrying_sample_count": len(transition_scores),
        "capital_price_carrying_span_minutes": round(span_seconds / 60.0, 2),
        "capital_price_carrying_slope": _linear_slope(
            transition_scores,
            min(3, len(transition_scores)),
        ),
        "capital_price_carrying_method": "immutable_intraday_delta_rolling",
    }


def _historical_percentile(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    series = values[-window:]
    current = series[-1]
    # Empirical percentile, including the current observation.  This avoids
    # distribution assumptions and remains reproducible from persisted facts.
    return round(sum(value <= current for value in series) * 100 / window, 2)


def load_sector_persistence_features(
    db: Session,
    board_type: str | None = None,
    *,
    min_sample_interval_seconds: int = _DEFAULT_CONFIRMATION_MIN_INTERVAL_SECONDS,
    carrying_min_interval_seconds: int = _DEFAULT_CARRYING_MIN_INTERVAL_SECONDS,
    carrying_min_transitions: int = _DEFAULT_CARRYING_MIN_TRANSITIONS,
    carrying_min_span_seconds: int = _DEFAULT_CARRYING_MIN_SPAN_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Load persistence and slow-variable features keyed by name and code.

    Financing slopes use distinct, disclosed ``margin_as_of`` daily values;
    cumulative 5/10/20-day fields are never treated as daily observations.
    A 60/120-day percentile remains ``None`` until that many true disclosures
    exist.  The same feature object is addressable by normalized board name and
    provider board code so route integration does not rely on one identifier.
    """

    daily_query = db.query(SectorCrowdingDailySnapshot)
    sample_query = db.query(SectorCrowdingSnapshotSample)
    if board_type:
        daily_query = daily_query.filter(SectorCrowdingDailySnapshot.board_type == board_type)
        sample_query = sample_query.filter(SectorCrowdingSnapshotSample.board_type == board_type)
    daily_rows_all = daily_query.order_by(
        SectorCrowdingDailySnapshot.trade_date.desc(),
        SectorCrowdingDailySnapshot.captured_at.desc(),
    ).all()
    sample_rows_all = sample_query.order_by(
        SectorCrowdingSnapshotSample.captured_at.desc(),
        SectorCrowdingSnapshotSample.id.desc(),
    ).limit(5000).all()

    daily_by_board: dict[tuple[str, str], list[SectorCrowdingDailySnapshot]] = {}
    for row in daily_rows_all:
        bucket = daily_by_board.setdefault((row.board_type, row.board_key), [])
        if len(bucket) < 140:
            bucket.append(row)
    samples_by_board: dict[tuple[str, str], list[SectorCrowdingSnapshotSample]] = {}
    for row in sample_rows_all:
        bucket = samples_by_board.setdefault((row.board_type, row.board_key), [])
        bucket.append(row)
    for key, bucket in samples_by_board.items():
        bucket.sort(
            key=lambda row: (row.provider_updated_at or row.captured_at, row.captured_at, row.id),
            reverse=True,
        )
        samples_by_board[key] = bucket[:24]

    result: dict[str, dict[str, Any]] = {}
    board_keys = set(daily_by_board) | set(samples_by_board)
    for key in board_keys:
        daily_rows = daily_by_board.get(key, [])
        sample_rows = samples_by_board.get(key, [])
        latest_daily = daily_rows[0] if daily_rows else None
        latest_sample = sample_rows[0] if sample_rows else None
        if latest_sample is not None:
            current_state = _sample_state(latest_sample)
            board_name = latest_sample.board_name
            board_code = latest_sample.board_code
            data_as_of = latest_sample.provider_updated_at or latest_sample.captured_at
        elif latest_daily is not None:
            current_state = _daily_state(latest_daily)
            board_name = latest_daily.board_name
            board_code = latest_daily.board_code
            data_as_of = latest_daily.provider_updated_at or latest_daily.captured_at
        else:
            continue

        sample_count = _consecutive_sample_count(
            sample_rows,
            current_state,
            min_interval_seconds=min_sample_interval_seconds,
        )
        trading_day_count = _consecutive_trading_day_count(daily_rows, current_state)

        # One disclosed financing date must contribute at most one observation,
        # even when multiple application trade dates carried the same T+1 row.
        margin_by_date: dict[str, SectorCrowdingDailySnapshot] = {}
        for row in daily_rows:
            margin_date = (row.margin_as_of or "").strip()
            if not margin_date or margin_date in margin_by_date:
                continue
            margin_by_date[margin_date] = row
        margin_rows = [margin_by_date[key] for key in sorted(margin_by_date)]
        net_buy_points: list[tuple[str, float | None]] = []
        balance_ratio_points: list[tuple[str, float | None]] = []
        for row in margin_rows:
            net_buy = row.financing_net_buy
            if net_buy is None:
                net_buy = _raw_item_metric(row, "financing_net_buy")
            net_buy_points.append((row.margin_as_of, net_buy))
            balance_ratio = row.financing_balance_ratio
            if balance_ratio is None:
                balance_ratio = _raw_item_metric(row, "financing_balance_ratio")
            balance_ratio_points.append((row.margin_as_of, balance_ratio))

        missing_net_buy_dates = [
            day for day, value in net_buy_points if value is None
        ]
        missing_balance_ratio_dates = [
            day for day, value in balance_ratio_points if value is None
        ]
        margin_sequence_complete = _complete_disclosure_sequence(net_buy_points)

        sample_confirmed = sample_count >= 2
        trading_day_confirmed = trading_day_count >= 2
        confirmation_basis = [
            *(
                [f"盘中连续 {sample_count} 个有效采样点保持同一状态"]
                if sample_confirmed
                else []
            ),
            *(
                [f"连续 {trading_day_count} 个交易日保持同一状态"]
                if trading_day_confirmed
                else []
            ),
        ]

        # Financing disclosures are T+1.  The financing-buy/turnover ratio
        # must therefore use turnover archived for the same disclosed trade
        # date, never today's live turnover and never an estimated fallback.
        daily_turnover_by_trade_date: dict[str, float] = {}
        incomplete_turnover_dates: list[str] = []
        for row in daily_rows:
            turnover_date = (row.provider_trade_date or row.trade_date or "")[:10]
            if not turnover_date or turnover_date in daily_turnover_by_trade_date:
                continue
            turnover = _raw_item_metric(row, "sector_turnover_amount")
            if not row.turnover_complete:
                if turnover is not None and turnover > 0:
                    incomplete_turnover_dates.append(turnover_date)
                continue
            if turnover is not None and turnover > 0:
                daily_turnover_by_trade_date[turnover_date] = float(turnover)
            if len(daily_turnover_by_trade_date) >= 20:
                break
        carrying_metrics = _capital_price_carrying_metrics(
            sample_rows,
            min_interval_seconds=carrying_min_interval_seconds,
            min_transitions=carrying_min_transitions,
            min_span_seconds=carrying_min_span_seconds,
        )
        feature = {
            "board_type": key[0],
            "board_code": board_code,
            "name": board_name,
            "strict_state": current_state or "数据不足",
            "last_state": current_state or None,
            "last_sample_at": (
                latest_sample.provider_updated_at or latest_sample.captured_at
                if latest_sample is not None
                else None
            ),
            "last_trade_date": (
                latest_sample.trade_date
                if latest_sample is not None
                else latest_daily.trade_date if latest_daily is not None else None
            ),
            "confirmed_state": current_state if current_state and (sample_confirmed or trading_day_confirmed) else None,
            "sample_confirmation_count": sample_count,
            "sample_confirmation_min_interval_seconds": max(
                0, int(min_sample_interval_seconds)
            ),
            "trading_day_confirmation_count": trading_day_count,
            "persistence_confirmed": sample_confirmed or trading_day_confirmed,
            "confirmation_basis": confirmation_basis,
            "data_as_of": data_as_of,
            "financing_net_buy_slope_5d": _complete_linear_slope(net_buy_points, 5),
            "financing_net_buy_slope_10d": _complete_linear_slope(net_buy_points, 10),
            "financing_net_buy_slope_20d": _complete_linear_slope(net_buy_points, 20),
            "financing_balance_ratio_percentile_60d": _complete_historical_percentile(
                balance_ratio_points, 60
            ),
            "financing_balance_ratio_percentile_120d": _complete_historical_percentile(
                balance_ratio_points, 120
            ),
            "financing_net_buy_observations": sum(
                value is not None for _day, value in net_buy_points
            ),
            "financing_balance_ratio_observations": sum(
                value is not None for _day, value in balance_ratio_points
            ),
            "margin_history_disclosure_dates": len(margin_rows),
            "margin_history_degraded": bool(
                missing_net_buy_dates
                or missing_balance_ratio_dates
                or not margin_sequence_complete
            ),
            "margin_history_sequence_complete": margin_sequence_complete,
            "margin_history_missing_net_buy_dates": missing_net_buy_dates[-20:],
            "margin_history_missing_balance_ratio_dates": missing_balance_ratio_dates[-20:],
            "margin_history_method": "逐日真实披露序列；缺失交易日不跨越计算",
            "daily_turnover_by_trade_date": daily_turnover_by_trade_date,
            "daily_turnover_observations": len(daily_turnover_by_trade_date),
            "incomplete_turnover_dates": sorted(set(incomplete_turnover_dates))[-20:],
            **carrying_metrics,
            "last_sample_fact_hash": latest_sample.payload_hash if latest_sample else "",
            "recent_samples": [
                {
                    "trade_date": row.trade_date,
                    "captured_at": row.captured_at,
                    "provider_updated_at": row.provider_updated_at,
                    "data_quality": row.data_quality,
                    "strict_state": _sample_state(row) or "数据不足",
                    "risk_level": row.distribution_risk_level,
                    "risk_score": row.distribution_risk_score,
                }
                for row in reversed(sample_rows[:8])
            ],
        }
        if board_name:
            result[board_name] = feature
        if board_code:
            result[board_code.upper()] = feature
    return result


def _global_hash_material(data: Mapping[str, Any], source: str, quality: str) -> dict[str, Any]:
    nested = data.get("payload")
    raw_body = dict(nested) if isinstance(nested, Mapping) else dict(data)
    body = _without_global_collection_metadata(raw_body)
    if not isinstance(body, dict):
        body = {}
    # Top-level ``as_of`` is often the local collection time.  Provider quote
    # as-of values inside the item lists remain part of the hash.
    body.pop("as_of", None)
    body.pop("source", None)
    body.pop("sources", None)
    body.pop("quality", None)
    body.pop("data_quality", None)
    return {"source": source, "quality": quality, "payload": body}


def global_evidence_recency_key(payload: Mapping[str, Any] | None) -> tuple[datetime, datetime]:
    """Return a deterministic provider-time/persistence-time ordering key.

    The provider/collection envelope time is primary.  ``persisted_at`` is only
    a tie breaker, so persisting an old provider response cannot make it newer
    than a genuinely fresher process-cache response.
    """

    data = payload or {}
    fact_time = None
    for key in ("generated_at", "as_of", "updated_at"):
        fact_time = _datetime(data.get(key))
        if fact_time is not None:
            break
    persisted_time = _datetime(data.get("persisted_at") or data.get("captured_at"))
    floor = datetime.min
    return (fact_time or persisted_time or floor, persisted_time or floor)


def _global_snapshot_payload(row: GlobalEvidenceSnapshot) -> dict[str, Any] | None:
    """Decode one immutable global snapshot without repairing its facts."""

    try:
        payload = json.loads(row.payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload.setdefault("generated_at", row.as_of or row.captured_at.isoformat())
    payload.setdefault("as_of", row.as_of or row.captured_at.isoformat())
    payload.setdefault("quality", row.data_quality or "missing")
    payload.setdefault("data_quality", row.data_quality or "missing")
    payload["snapshot_id"] = row.id
    payload["snapshot_origin"] = "database"
    payload["persisted_at"] = row.captured_at.isoformat()
    return payload


def load_latest_global_evidence_snapshot(db: Session) -> dict[str, Any] | None:
    """Return the newest persisted provider envelope after a process restart.

    The function performs no provider calls and never substitutes one evidence
    family for another.  Invalid historical JSON is skipped instead of being
    represented as a valid empty market.
    """

    rows = (
        db.query(GlobalEvidenceSnapshot)
        .order_by(
            GlobalEvidenceSnapshot.captured_at.desc(),
            GlobalEvidenceSnapshot.id.desc(),
        )
        .limit(10)
        .all()
    )
    for row in rows:
        payload = _global_snapshot_payload(row)
        if payload is not None:
            return payload
    return None


def load_global_evidence_history(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 60,
    ascending: bool = False,
) -> list[dict[str, Any]]:
    """Read auditable immutable global snapshots without external refresh."""

    query = db.query(GlobalEvidenceSnapshot)
    if start_date:
        query = query.filter(GlobalEvidenceSnapshot.trade_date >= str(start_date)[:10])
    if end_date:
        query = query.filter(GlobalEvidenceSnapshot.trade_date <= str(end_date)[:10])
    ordering = (
        (GlobalEvidenceSnapshot.captured_at.asc(), GlobalEvidenceSnapshot.id.asc())
        if ascending
        else (GlobalEvidenceSnapshot.captured_at.desc(), GlobalEvidenceSnapshot.id.desc())
    )
    rows = query.order_by(*ordering).limit(min(max(int(limit), 1), 500)).all()
    output: list[dict[str, Any]] = []
    for row in rows:
        payload = _global_snapshot_payload(row)
        if payload is None:
            continue
        output.append({
            "snapshot_id": row.id,
            "trade_date": row.trade_date,
            "captured_at": row.captured_at.isoformat(),
            "as_of": row.as_of,
            "source": row.source,
            "data_quality": row.data_quality,
            "payload_hash": row.payload_hash,
            "payload": payload,
        })
    return output


def _global_evolution_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    quote_groups = (
        "korea_indices",
        "korea_equities",
        "us_indices",
        "us_sector_rank",
        "strategic_assets",
        "macro_indicators",
    )
    flow_groups = (
        "etf_flows",
        "korea_foreign_flows",
        "korea_leverage_products",
        "official_rates",
    )
    valid_quotes: list[Mapping[str, Any]] = []
    for key in quote_groups:
        valid_quotes.extend(
            item for item in list(payload.get(key) or [])
            if isinstance(item, Mapping)
            and str(item.get("status") or "").lower() in {"ok", "delayed"}
            and item.get("change_pct") is not None
        )
    valid_metrics: list[Mapping[str, Any]] = []
    for key in flow_groups:
        valid_metrics.extend(
            item for item in list(payload.get(key) or [])
            if isinstance(item, Mapping)
            and str(item.get("status") or "").lower() == "ok"
            and item.get("value") is not None
            and str(item.get("source_url") or "").startswith("https://")
            and item.get("published_at")
        )
    weak_quotes = []
    for item in valid_quotes:
        try:
            if float(item.get("change_pct")) <= -1.0:
                weak_quotes.append(str(item.get("name") or item.get("symbol") or "外围标的"))
        except (TypeError, ValueError):
            continue
    negative_flows = []
    for item in valid_metrics:
        direction = str(item.get("direction") or "").lower()
        kind = str(item.get("metric_kind") or "")
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        if direction == "outflow" or (kind == "korea_foreign_net_flow" and value < 0):
            negative_flows.append(str(item.get("name") or item.get("metric_id") or "机构资金"))
    return {
        "quote_quality": str(payload.get("quote_quality") or payload.get("data_quality") or "missing"),
        "institutional_flow_quality": str(payload.get("institutional_flow_quality") or "missing"),
        "valid_quote_count": len(valid_quotes),
        "valid_official_metric_count": len(valid_metrics),
        "weak_quote_count": len(weak_quotes),
        "weak_quotes": weak_quotes,
        "negative_flow_count": len(negative_flows),
        "negative_flows": negative_flows,
    }


def build_global_evidence_evolution(
    db: Session,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 60,
) -> list[dict[str, Any]]:
    """Return the observable quality/risk transitions between snapshots."""

    snapshots = load_global_evidence_history(
        db,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        ascending=True,
    )
    output: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for row in snapshots:
        summary = _global_evolution_summary(row["payload"])
        changes: list[str] = []
        if previous is not None:
            if summary["quote_quality"] != previous["quote_quality"]:
                changes.append(
                    f"行情质量：{previous['quote_quality']} → {summary['quote_quality']}"
                )
            if summary["institutional_flow_quality"] != previous["institutional_flow_quality"]:
                changes.append(
                    "机构资金质量："
                    f"{previous['institutional_flow_quality']} → {summary['institutional_flow_quality']}"
                )
            if summary["weak_quote_count"] != previous["weak_quote_count"]:
                changes.append(
                    f"弱势外围标的：{previous['weak_quote_count']} → {summary['weak_quote_count']}"
                )
            if summary["negative_flow_count"] != previous["negative_flow_count"]:
                changes.append(
                    f"机构流出证据：{previous['negative_flow_count']} → {summary['negative_flow_count']}"
                )
        output.append({
            "snapshot_id": row["snapshot_id"],
            "trade_date": row["trade_date"],
            "captured_at": row["captured_at"],
            "as_of": row["as_of"],
            "source": row["source"],
            "payload_hash": row["payload_hash"],
            **summary,
            "changes": changes,
        })
        previous = summary
    return output


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
    "build_global_evidence_evolution",
    "build_sector_state_evolution",
    "global_evidence_recency_key",
    "load_sector_history",
    "load_global_evidence_history",
    "load_latest_global_evidence_snapshot",
    "load_latest_sector_temperature_snapshot",
    "load_sector_persistence_features",
    "load_sector_samples",
    "persist_global_evidence_snapshot",
    "persist_sector_temperature_snapshot",
]
