from datetime import datetime, timedelta

from app.models.trading import MarketRegimeSnapshot
from app.services import intraday_collector as collector


def _reset_market_regime_runtime_state() -> None:
    collector._failure_counts.pop("市场环境", None)
    collector._circuit_until.pop("市场环境", None)
    collector._market_regime_last_error = ""
    collector._market_regime_last_success_at = None


def test_market_regime_collection_window_includes_close_snapshot():
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 9, 25)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 15, 5)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 9, 24)) is False
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 15, 6)) is False
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 12, 10, 0)) is False


def test_default_market_windows_use_shanghai_clock(monkeypatch):
    """A UTC-configured host must still collect during the A-share session."""
    monkeypatch.setattr(collector, "_shanghai_now_naive", lambda: datetime(2026, 7, 15, 10, 30))

    assert collector._is_market_watch_time() is True
    assert collector._is_market_regime_watch_time() is True
    assert collector._is_simulation_match_time() is True


def test_market_regime_scheduler_skips_recent_persisted_snapshot(monkeypatch, db_session):
    _reset_market_regime_runtime_state()
    now = datetime(2026, 7, 13, 10, 0)
    db_session.add(
        MarketRegimeSnapshot(
            trade_date=now.date().isoformat(),
            captured_at=now - timedelta(seconds=60),
        )
    )
    db_session.commit()
    calls = []
    monkeypatch.setattr(collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(collector, "get_market_regime", lambda *_args, **_kwargs: calls.append(True))

    result = collector.run_market_regime_collection_once(now=now)

    assert result is None
    assert calls == []
    assert collector._market_regime_running is False


def test_market_regime_scheduler_collects_after_minimum_gap(monkeypatch, db_session):
    _reset_market_regime_runtime_state()
    now = datetime(2026, 7, 13, 10, 5)
    db_session.add(
        MarketRegimeSnapshot(
            trade_date=now.date().isoformat(),
            captured_at=now - timedelta(seconds=collector.MARKET_REGIME_MIN_PERSIST_SECONDS + 1),
        )
    )
    db_session.commit()
    expected = object()
    calls = []

    def fake_get_market_regime(db, force_refresh=False):
        calls.append((db, force_refresh))
        return expected

    monkeypatch.setattr(collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(collector, "get_market_regime", fake_get_market_regime)

    result = collector.run_market_regime_collection_once(now=now)

    assert result is expected
    assert len(calls) == 1
    assert calls[0][1] is False
    assert collector._market_regime_last_success_at is not None
    assert "市场环境" not in collector._failure_counts
    assert collector._market_regime_running is False


def test_market_regime_failures_are_isolated_and_circuit_break(monkeypatch, db_session):
    _reset_market_regime_runtime_state()
    monkeypatch.setattr(collector, "SessionLocal", lambda: db_session)

    def failed_collection(*_args, **_kwargs):
        raise RuntimeError("upstream unavailable")

    monkeypatch.setattr(collector, "get_market_regime", failed_collection)

    for minute in range(3):
        result = collector.run_market_regime_collection_once(
            now=datetime(2026, 7, 13, 10, minute),
            force=True,
        )
        assert result is None

    assert collector._failure_counts["市场环境"] == 3
    assert collector._circuit_until["市场环境"] > collector.clock.time()
    assert "RuntimeError" in collector._market_regime_last_error
    assert collector._market_regime_running is False


def test_resilient_database_job_rolls_back_before_every_retry(monkeypatch):
    attempts = 0
    rollbacks = 0
    notes: list[str] = []

    def callback():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("failed transaction")
        return "ok"

    def rollback():
        nonlocal rollbacks
        rollbacks += 1

    monkeypatch.setattr(collector.clock, "sleep", lambda _seconds: None)

    result = collector._run_with_resilience(
        "测试数据库任务",
        callback,
        notes,
        on_error=rollback,
    )

    assert result == "ok"
    assert attempts == 3
    assert rollbacks == 2
