import time
import threading
from collections import defaultdict
from typing import Any

from app.core.trading_clock import shanghai_now_naive, shanghai_today

_SNAPSHOT_LOCK = threading.Lock()
_flow_snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
_snapshot_seq = 0

_CACHE_LOCK = threading.Lock()
_last_good_flow_cache: dict[str, tuple[list[dict[str, Any]], str, str]] = {}
_response_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300

def _cache_good_flow(key: str, raw_items: list[dict[str, Any]], source: str) -> None:
    with _CACHE_LOCK:
        _last_good_flow_cache[key] = (raw_items, source, shanghai_today().isoformat())

def _get_cached_flow(key: str) -> tuple[list[dict[str, Any]], str, str] | None:
    with _CACHE_LOCK:
        return _last_good_flow_cache.get(key)

def _get_response_cache(key: str, *, allow_stale: bool = False) -> Any | None:
    """Read a response snapshot.

    Normal provider calls keep the five-minute freshness contract.  Read-only
    page endpoints may opt into the last in-process snapshot so navigation does
    not turn a previously refreshed screen blank merely because the TTL elapsed.
    Expired entries are intentionally retained until a later explicit refresh
    replaces them; process restart remains an explicit data-gap boundary.
    """
    now = time.time()
    with _CACHE_LOCK:
        cached = _response_cache.get(key)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at <= now:
            return value if allow_stale else None
        return value

def _set_response_cache(key: str, value: Any) -> None:
    with _CACHE_LOCK:
        _response_cache[key] = (time.time() + _CACHE_TTL_SECONDS, value)


def _set_response_cache_unless_data_status(
    key: str,
    value: Any,
    protected_status: str,
) -> bool:
    """Atomically keep a stronger cached response from being downgraded.

    A refresh may take long enough for another request to finish first.  The
    comparison and write therefore have to share the cache lock; checking the
    cache before doing provider work is not sufficient to prevent a slower
    partial response from replacing a newly completed snapshot.
    """

    with _CACHE_LOCK:
        cached = _response_cache.get(key)
        current = cached[1] if cached else None
        if getattr(current, "data_status", None) == protected_status:
            return False
        _response_cache[key] = (time.time() + _CACHE_TTL_SECONDS, value)
        return True


def _record_snapshot(flow_type: str, raw_items: list[dict[str, Any]]) -> None:
    global _snapshot_seq
    now = shanghai_now_naive()
    with _SNAPSHOT_LOCK:
        key = f"{now.strftime('%Y-%m-%d')}:{flow_type}"
        _snapshot_seq += 1
        _flow_snapshots[key].append(
            {"time": now.strftime("%H:%M:%S"), "seq": _snapshot_seq, "items": raw_items}
        )
        if len(_flow_snapshots[key]) > 120:
            _flow_snapshots[key] = _flow_snapshots[key][-120:]

def _get_snapshots(flow_type: str) -> list[dict[str, Any]]:
    today = shanghai_today().isoformat()
    key = f"{today}:{flow_type}"
    with _SNAPSHOT_LOCK:
        return list(_flow_snapshots.get(key, []))
