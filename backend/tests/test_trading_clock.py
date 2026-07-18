from datetime import date, datetime, timezone

from app.api.helpers.decision import _today, current_expectation_stage
from app.api.helpers.quotes import _provider_event_metadata
from app.api.helpers.volume_price import _trading_elapsed_ratio
from app.api.routes.stocks import _completed_trading_days
from app.core.trading_clock import (
    shanghai_day_bounds_utc_naive,
    shanghai_from_timestamp,
    shanghai_now_naive,
)
from app.services.intraday_evidence_engine import _trade_date, nearest_sample_label
from app.services.market_regime import summarize_all_a_rows
from app.services.next_day_expectations import next_trading_date


def test_aware_utc_time_is_converted_across_shanghai_midnight():
    value = datetime(2026, 7, 15, 16, 30, tzinfo=timezone.utc)

    assert shanghai_now_naive(value) == datetime(2026, 7, 16, 0, 30)
    assert _today(value) == "2026-07-16"
    assert _trade_date(value) == "2026-07-16"

    day_start, day_end = shanghai_day_bounds_utc_naive(value)
    assert day_start == datetime(2026, 7, 15, 16, 0)
    assert day_end == datetime(2026, 7, 16, 16, 0)


def test_auction_stage_and_sample_label_use_shanghai_clock():
    # 01:27 UTC is 09:27 in Shanghai, inside the call-auction confirmation stage.
    value = datetime(2026, 7, 16, 1, 27, tzinfo=timezone.utc)

    assert current_expectation_stage(value) == "竞价确认"
    assert nearest_sample_label(value) == "09:25"


def test_market_progress_and_completed_day_use_shanghai_close_boundary():
    close = datetime(2026, 7, 16, 7, 10, tzinfo=timezone.utc)  # 15:10 CST

    assert _trading_elapsed_ratio(close) == 1.0
    assert _completed_trading_days(1, close) == ["2026-07-16"]


def test_next_trade_date_uses_shanghai_business_date():
    # Thursday 16:30 UTC is already Friday in Shanghai; the next weekday is Monday.
    value = datetime(2026, 7, 16, 16, 30, tzinfo=timezone.utc)

    assert next_trading_date(now=value) == "2026-07-20"


def test_next_trade_date_skips_published_exchange_holiday():
    assert next_trading_date(value=date(2026, 9, 30)) == "2026-10-08"


def test_quote_freshness_compares_event_and_receipt_in_same_timezone():
    event_at = datetime(2026, 7, 16, 10, 0, 0)
    received_utc = datetime(2026, 7, 16, 2, 0, 5, tzinfo=timezone.utc)

    metadata = _provider_event_metadata(event_at, received_at=received_utc)

    assert metadata["received_at"] == datetime(2026, 7, 16, 10, 0, 5)
    assert metadata["age_seconds"] == 5.0


def test_eastmoney_timestamp_is_always_interpreted_as_shanghai_time():
    source_timestamp = int(datetime(2026, 7, 16, 1, 30, tzinfo=timezone.utc).timestamp())

    assert shanghai_from_timestamp(source_timestamp) == datetime(2026, 7, 16, 9, 30)

    summary, _ = summarize_all_a_rows(
        [{
            "f12": "600000",
            "f14": "浦发银行",
            "f2": 10,
            "f3": 1,
            "f6": 100_000_000,
            "f62": 10_000_000,
            "f124": source_timestamp,
        }],
        expected_total=1,
        now=datetime(2026, 7, 16, 2, 0, tzinfo=timezone.utc),
    )

    assert summary["source_time"] == datetime(2026, 7, 16, 9, 30)
