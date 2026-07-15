from __future__ import annotations

from datetime import date, datetime, timedelta, timezone


# A-share business dates and session stages are defined by China Standard Time.
# Keep the returned value timezone-naive because the existing database columns
# and most market-data helpers use naive wall-clock timestamps.
SHANGHAI_TZ = timezone(timedelta(hours=8))


def shanghai_now_naive(value: datetime | None = None) -> datetime:
    """Return a naive Shanghai wall-clock datetime.

    Naive explicit values are treated as already being Shanghai wall-clock
    values for backwards compatibility with tests, database rows and callers.
    Aware values are converted, which makes boundary tests unambiguous.
    """

    if value is None:
        value = datetime.now(SHANGHAI_TZ)
    elif value.tzinfo is not None:
        value = value.astimezone(SHANGHAI_TZ)
    return value.replace(tzinfo=None)


def shanghai_today(value: datetime | None = None) -> date:
    return shanghai_now_naive(value).date()


def shanghai_from_timestamp(value: int | float) -> datetime:
    """Convert a Unix timestamp to a naive Shanghai market timestamp."""

    return datetime.fromtimestamp(value, tz=SHANGHAI_TZ).replace(tzinfo=None)


def shanghai_day_bounds_utc_naive(value: datetime | None = None) -> tuple[datetime, datetime]:
    """Return UTC-naive storage bounds for one Shanghai business date.

    This is for database columns that explicitly store UTC instants in a
    timezone-naive ``DateTime`` column.  Business-date decisions themselves
    should continue to use :func:`shanghai_today`.
    """

    business_date = shanghai_today(value)
    local_start = datetime.combine(business_date, datetime.min.time(), tzinfo=SHANGHAI_TZ)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )
