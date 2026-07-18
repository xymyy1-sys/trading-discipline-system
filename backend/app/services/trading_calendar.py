"""A-share trading-calendar helpers with a non-blocking remote refresh.

The collector calls these helpers every minute.  A calendar network problem
must therefore never delay the evidence loop or turn into a request storm.
AkShare is refreshed in one daemon thread and the last result is cached in the
process.  Until that result is available, the published SSE holiday schedule
is used for the years maintained below.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta
from typing import Any


AKSHARE_SUCCESS_TTL_SECONDS = 24 * 60 * 60
AKSHARE_FAILURE_TTL_SECONDS = 6 * 60 * 60

# SSE 2026 holiday announcement:
# https://www.sse.com.cn/disclosure/announcement/general/c/c_20251222_10802507.shtml
# This offline safety list must be updated after the exchange publishes each
# following year's holiday arrangement.  Weekends are closed independently.
_SSE_HOLIDAY_RANGES: dict[int, tuple[tuple[str, str], ...]] = {
    2026: (
        ("2026-01-01", "2026-01-03"),
        ("2026-02-15", "2026-02-23"),
        ("2026-04-04", "2026-04-06"),
        ("2026-05-01", "2026-05-05"),
        ("2026-06-19", "2026-06-21"),
        ("2026-09-25", "2026-09-27"),
        ("2026-10-01", "2026-10-07"),
    ),
}


def _expand_ranges(ranges: tuple[tuple[str, str], ...]) -> frozenset[date]:
    result: set[date] = set()
    for start_text, end_text in ranges:
        cursor = date.fromisoformat(start_text)
        end = date.fromisoformat(end_text)
        while cursor <= end:
            result.add(cursor)
            cursor += timedelta(days=1)
    return frozenset(result)


_SSE_HOLIDAYS = {
    year: _expand_ranges(ranges)
    for year, ranges in _SSE_HOLIDAY_RANGES.items()
}

_state_lock = threading.Lock()
_akshare_dates: frozenset[date] | None = None
_akshare_min_date: date | None = None
_akshare_max_date: date | None = None
_akshare_loading = False
_akshare_last_error = ""
_akshare_last_success_at: datetime | None = None
_akshare_next_refresh_at = 0.0


def _coerce_date(value: date | datetime | None) -> date:
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    return value


def _fetch_akshare_calendar() -> frozenset[date]:
    """Fetch the Sina trading-date history exposed by the installed AkShare."""

    import akshare as ak

    frame = ak.tool_trade_date_hist_sina()
    if frame is None or frame.empty:
        raise ValueError("AkShare returned an empty trading calendar")
    column = "trade_date" if "trade_date" in frame.columns else frame.columns[0]
    parsed: set[date] = set()
    for raw in frame[column].tolist():
        if hasattr(raw, "date"):
            parsed.add(raw.date())
            continue
        parsed.add(date.fromisoformat(str(raw)[:10]))
    if len(parsed) < 100:
        raise ValueError("AkShare trading calendar is unexpectedly short")
    return frozenset(parsed)


def _refresh_akshare_calendar() -> None:
    global _akshare_dates, _akshare_min_date, _akshare_max_date
    global _akshare_loading, _akshare_last_error, _akshare_last_success_at
    global _akshare_next_refresh_at

    try:
        dates = _fetch_akshare_calendar()
    except Exception as exc:
        with _state_lock:
            _akshare_last_error = f"{exc.__class__.__name__}: {exc}"
            _akshare_next_refresh_at = time.monotonic() + AKSHARE_FAILURE_TTL_SECONDS
            _akshare_loading = False
        return

    with _state_lock:
        _akshare_dates = dates
        _akshare_min_date = min(dates)
        _akshare_max_date = max(dates)
        _akshare_last_error = ""
        _akshare_last_success_at = datetime.now()
        _akshare_next_refresh_at = time.monotonic() + AKSHARE_SUCCESS_TTL_SECONDS
        _akshare_loading = False


def _schedule_akshare_refresh() -> None:
    """Schedule at most one refresh and return immediately to the caller."""

    global _akshare_loading, _akshare_next_refresh_at
    now = time.monotonic()
    with _state_lock:
        if _akshare_loading or now < _akshare_next_refresh_at:
            return
        _akshare_loading = True
        # Reserve the failure backoff before starting the thread.  Even if an
        # interpreter shutdown interrupts it, the minute collector will not
        # start another network request immediately.
        _akshare_next_refresh_at = now + AKSHARE_FAILURE_TTL_SECONDS
    threading.Thread(
        target=_refresh_akshare_calendar,
        name="a-share-trading-calendar-refresh",
        daemon=True,
    ).start()


def _calendar_decision(value: date) -> tuple[bool, str, str]:
    _schedule_akshare_refresh()
    with _state_lock:
        remote_dates = _akshare_dates
        remote_min = _akshare_min_date
        remote_max = _akshare_max_date
        loading = _akshare_loading
        last_error = _akshare_last_error

    if remote_dates is not None and remote_min is not None and remote_max is not None:
        if remote_min <= value <= remote_max:
            return value in remote_dates, "akshare", "AkShare交易日历"

    holidays = _SSE_HOLIDAYS.get(value.year)
    if holidays is not None:
        is_open = value.weekday() < 5 and value not in holidays
        suffix = "；AkShare后台更新中" if loading else ""
        if last_error:
            suffix = f"；AkShare暂不可用（{last_error.split(':', 1)[0]}），已进入失败缓存"
        return is_open, "sse_2026_fallback", f"上交所已公布休市日离线兜底{suffix}"

    is_open = value.weekday() < 5
    suffix = "AkShare后台更新中" if loading else (
        f"AkShare暂不可用（{last_error.split(':', 1)[0]}），已进入失败缓存"
        if last_error else "AkShare尚未覆盖该日期"
    )
    return (
        is_open,
        "weekday_fallback",
        f"{value.year}年离线休市表尚未维护，保守按工作日判断；{suffix}",
    )


def is_a_share_trading_day(value: date | datetime) -> bool:
    """Return whether *value* is an A-share trading day."""

    result, _source, _diagnostic = _calendar_decision(_coerce_date(value))
    return result


def next_a_share_trading_day(value: date | datetime) -> date:
    """Return the first A-share trading day strictly after *value*."""

    cursor = _coerce_date(value) + timedelta(days=1)
    for _ in range(370):
        if is_a_share_trading_day(cursor):
            return cursor
        cursor += timedelta(days=1)
    raise RuntimeError("unable to resolve the next A-share trading day within one year")


def previous_a_share_trading_day(value: date | datetime, *, inclusive: bool = False) -> date:
    """Return the latest A-share trading day before (or including) *value*."""

    cursor = _coerce_date(value)
    if not inclusive:
        cursor -= timedelta(days=1)
    for _ in range(370):
        if is_a_share_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise RuntimeError("unable to resolve the previous A-share trading day within one year")


def trading_calendar_diagnostic(value: date | datetime | None = None) -> dict[str, Any]:
    """Expose the active source so degraded calendar decisions are observable."""

    target = _coerce_date(value)
    is_open, source, diagnostic = _calendar_decision(target)
    with _state_lock:
        success_at = _akshare_last_success_at
        retry_seconds = max(0, int(_akshare_next_refresh_at - time.monotonic()))
        loading = _akshare_loading
    return {
        "date": target.isoformat(),
        "is_trading_day": is_open,
        "calendar_source": source,
        "diagnostic": diagnostic,
        "akshare_loading": loading,
        "akshare_last_success_at": success_at.isoformat() if success_at else None,
        "akshare_retry_after_seconds": retry_seconds,
    }


def _reset_calendar_cache_for_tests() -> None:
    """Reset process state; intentionally private and used by isolated tests."""

    global _akshare_dates, _akshare_min_date, _akshare_max_date
    global _akshare_loading, _akshare_last_error, _akshare_last_success_at
    global _akshare_next_refresh_at
    with _state_lock:
        _akshare_dates = None
        _akshare_min_date = None
        _akshare_max_date = None
        _akshare_loading = False
        _akshare_last_error = ""
        _akshare_last_success_at = None
        _akshare_next_refresh_at = time.monotonic() + AKSHARE_SUCCESS_TTL_SECONDS
