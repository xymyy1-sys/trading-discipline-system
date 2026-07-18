from datetime import date, datetime

from app.services import market_data, trading_calendar


def _offline_calendar() -> None:
    # Keep unit tests deterministic and never start a real provider request.
    trading_calendar._reset_calendar_cache_for_tests()


def test_2026_sse_holiday_fallback_covers_spring_festival_and_national_day():
    _offline_calendar()

    assert trading_calendar.is_a_share_trading_day(date(2026, 2, 18)) is False
    assert trading_calendar.is_a_share_trading_day(date(2026, 10, 5)) is False
    assert trading_calendar.is_a_share_trading_day(date(2026, 7, 16)) is True

    diagnostic = trading_calendar.trading_calendar_diagnostic(date(2026, 2, 18))
    assert diagnostic["calendar_source"] == "sse_2026_fallback"
    assert "上交所" in diagnostic["diagnostic"]


def test_next_trading_day_skips_exchange_holidays_not_only_weekends():
    _offline_calendar()

    assert trading_calendar.next_a_share_trading_day(date(2026, 2, 14)) == date(2026, 2, 24)
    assert trading_calendar.next_a_share_trading_day(date(2026, 9, 30)) == date(2026, 10, 8)


def test_market_data_last_day_and_ladder_candidates_share_exchange_calendar(monkeypatch):
    _offline_calendar()
    monkeypatch.setattr(
        market_data,
        "_shanghai_now_naive",
        lambda: datetime(2026, 10, 3, 10, 0),
    )

    assert market_data._last_trading_day() == "2026-09-30"
    assert market_data._limit_up_default_candidate_dates(
        datetime(2026, 10, 8, 9, 20),
        lookback=2,
    ) == ["2026-09-30", "2026-09-29"]


def test_unknown_offline_year_is_observable_weekday_fallback():
    _offline_calendar()

    diagnostic = trading_calendar.trading_calendar_diagnostic(date(2027, 1, 4))

    assert diagnostic["is_trading_day"] is True
    assert diagnostic["calendar_source"] == "weekday_fallback"
    assert "尚未维护" in diagnostic["diagnostic"]


def test_akshare_failure_enters_long_backoff_instead_of_minute_retry(monkeypatch):
    _offline_calendar()
    monkeypatch.setattr(trading_calendar, "_akshare_next_refresh_at", 0.0)
    monkeypatch.setattr(
        trading_calendar,
        "_fetch_akshare_calendar",
        lambda: (_ for _ in ()).throw(ConnectionError("offline")),
    )

    trading_calendar._refresh_akshare_calendar()
    first_retry = trading_calendar._akshare_next_refresh_at
    scheduled: list[bool] = []
    monkeypatch.setattr(
        trading_calendar.threading,
        "Thread",
        lambda **_kwargs: scheduled.append(True),
    )
    trading_calendar._schedule_akshare_refresh()

    assert first_retry > trading_calendar.time.monotonic() + 5 * 60 * 60
    assert scheduled == []
    assert "ConnectionError" in trading_calendar.trading_calendar_diagnostic(date(2026, 7, 16))["diagnostic"]
