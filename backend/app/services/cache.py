import time
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any

_SNAPSHOT_LOCK = threading.Lock()
_flow_snapshots: dict[str, list[dict[str, Any]]] = defaultdict(list)
_snapshot_seq = 0

_CACHE_LOCK = threading.Lock()
_last_good_flow_cache: dict[str, tuple[list[dict[str, Any]], str, str]] = {}
_response_cache: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300

def _cache_good_flow(key: str, raw_items: list[dict[str, Any]], source: str) -> None:
    with _CACHE_LOCK:
        _last_good_flow_cache[key] = (raw_items, source, datetime.now().strftime("%Y-%m-%d"))

def _get_cached_flow(key: str) -> tuple[list[dict[str, Any]], str, str] | None:
    with _CACHE_LOCK:
        return _last_good_flow_cache.get(key)

def _get_response_cache(key: str) -> Any | None:
    now = time.time()
    with _CACHE_LOCK:
        cached = _response_cache.get(key)
        if not cached:
            return None
        expires_at, value = cached
        if expires_at <= now:
            _response_cache.pop(key, None)
            return None
        return value

def _set_response_cache(key: str, value: Any) -> None:
    with _CACHE_LOCK:
        _response_cache[key] = (time.time() + _CACHE_TTL_SECONDS, value)

def _record_snapshot(flow_type: str, raw_items: list[dict[str, Any]]) -> None:
    global _snapshot_seq
    now = datetime.now()
    with _SNAPSHOT_LOCK:
        key = f"{now.strftime('%Y-%m-%d')}:{flow_type}"
        _snapshot_seq += 1
        _flow_snapshots[key].append(
            {"time": now.strftime("%H:%M:%S"), "seq": _snapshot_seq, "items": raw_items}
        )
        if len(_flow_snapshots[key]) > 120:
            _flow_snapshots[key] = _flow_snapshots[key][-120:]

def _get_snapshots(flow_type: str) -> list[dict[str, Any]]:
    today = datetime.now().strftime("%Y-%m-%d")
    key = f"{today}:{flow_type}"
    with _SNAPSHOT_LOCK:
        return list(_flow_snapshots.get(key, []))
