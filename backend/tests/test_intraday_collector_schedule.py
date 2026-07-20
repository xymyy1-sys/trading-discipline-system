import asyncio
from datetime import datetime, timedelta

from app.models.trading import MarketRegimeSnapshot
from app.services import intraday_collector as collector


def _reset_market_regime_runtime_state() -> None:
    collector._failure_counts.pop("市场环境", None)
    collector._circuit_until.pop("市场环境", None)
    collector._market_regime_last_error = ""
    collector._market_regime_last_success_at = None
    collector._market_auxiliary_last_attempt_at.clear()
    collector._market_auxiliary_last_success_at.clear()
    collector._market_auxiliary_errors.clear()


def test_market_regime_collection_window_includes_close_snapshot():
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 9, 25)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 11, 30)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 11, 30, 59)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 11, 31)) is False
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 12, 59)) is False
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 13, 0)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 15, 5)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 15, 5, 59)) is True
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 9, 24)) is False
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 13, 15, 6)) is False
    assert collector._is_market_regime_watch_time(datetime(2026, 7, 12, 10, 0)) is False


def test_all_core_windows_are_closed_on_exchange_holiday():
    spring_festival = datetime(2026, 2, 18, 10, 0)

    assert collector._is_market_watch_time(spring_festival) is False
    assert collector._is_market_regime_watch_time(spring_festival) is False
    assert collector._is_simulation_match_time(spring_festival) is False


def test_collector_iteration_skips_intraday_and_close_jobs_on_exchange_holiday(monkeypatch):
    holiday = datetime(2026, 10, 1, 15, 10)
    calls: list[str] = []
    monkeypatch.setattr(collector, "COLLECTOR_ENABLED", True)
    monkeypatch.setattr(collector, "_shanghai_now_naive", lambda: holiday)
    monkeypatch.setattr(collector, "run_intraday_collection_once", lambda *_args: calls.append("holding"))
    monkeypatch.setattr(collector, "run_simulation_matching_once", lambda **_kwargs: calls.append("matching"))
    monkeypatch.setattr(collector, "run_simulation_shadow_once", lambda **_kwargs: calls.append("shadow"))
    monkeypatch.setattr(collector, "run_simulation_shadow_equity_once", lambda **_kwargs: calls.append("equity"))

    asyncio.run(collector._collector_iteration())

    assert calls == []


def test_forced_market_regime_collection_is_rejected_on_exchange_holiday(monkeypatch):
    monkeypatch.setattr(
        collector,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("database should not be opened")),
    )

    assert collector.run_market_regime_collection_once(
        now=datetime(2026, 10, 1, 10, 0),
        force=True,
    ) is None


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
    auxiliary_calls = []
    monkeypatch.setattr(collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(collector, "get_market_regime", lambda *_args, **_kwargs: calls.append(True))
    monkeypatch.setattr(
        collector,
        "_run_market_auxiliary_collections",
        lambda db, *, force=False: auxiliary_calls.append((db, force)) or "",
    )

    result = collector.run_market_regime_collection_once(now=now)

    assert result is None
    assert calls == []
    assert auxiliary_calls == [(db_session, False)]
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
    monkeypatch.setattr(
        collector,
        "_run_market_auxiliary_collections",
        lambda _db, *, force=False: "",
    )

    result = collector.run_market_regime_collection_once(now=now)

    assert result is expected
    assert len(calls) == 1
    assert calls[0][1] is False
    assert collector._market_regime_last_success_at is not None
    assert "市场环境" not in collector._failure_counts
    assert collector._market_regime_running is False


def test_market_auxiliary_collections_use_independent_intervals(monkeypatch, db_session):
    from app.api.routes import market

    _reset_market_regime_runtime_state()
    now_clock = [1000.0]
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(collector.clock, "time", lambda: now_clock[0])
    monkeypatch.setattr(
        collector.global_market_service,
        "snapshot",
        lambda *, force_refresh=False: calls.append(("global", force_refresh)) or {
            "generated_at": "2026-07-13T10:00:00+08:00",
            "data_quality": "ok",
        },
    )
    monkeypatch.setattr(
        collector,
        "persist_global_evidence_snapshot",
        lambda _db, _snapshot: None,
    )
    monkeypatch.setattr(
        market,
        "_sector_temperature_snapshot",
        lambda board_type, force_refresh=False, db=None: calls.append(
            (f"sector:{board_type}", force_refresh)
        ),
    )
    monkeypatch.setattr(
        collector.opportunity_market_provider,
        "theme_radar",
        lambda force_refresh=False: calls.append(("theme", force_refresh)),
    )

    collector._run_market_auxiliary_collections(db_session)
    now_clock[0] += 300
    collector._run_market_auxiliary_collections(db_session)
    assert [name for name, _ in calls].count("global") == 1
    assert [name for name, _ in calls].count("sector:行业") == 1
    assert [name for name, _ in calls].count("sector:概念") == 1
    assert [name for name, _ in calls].count("theme") == 1

    now_clock[0] = 1600
    collector._run_market_auxiliary_collections(db_session)
    assert [name for name, _ in calls].count("global") == 2
    assert [name for name, _ in calls].count("sector:行业") == 1
    assert [name for name, _ in calls].count("theme") == 1

    now_clock[0] = 1900
    collector._run_market_auxiliary_collections(db_session)
    assert [name for name, _ in calls].count("global") == 2
    assert [name for name, _ in calls].count("sector:行业") == 2
    assert [name for name, _ in calls].count("sector:概念") == 2
    assert [name for name, _ in calls].count("theme") == 1

    now_clock[0] = 2800
    collector._run_market_auxiliary_collections(db_session)
    assert [name for name, _ in calls].count("global") == 3
    assert [name for name, _ in calls].count("sector:行业") == 3
    assert [name for name, _ in calls].count("sector:概念") == 3
    assert [name for name, _ in calls].count("theme") == 2
    assert all(force_refresh is False for _, force_refresh in calls)


def test_force_bypasses_every_market_auxiliary_throttle(monkeypatch, db_session):
    from app.api.routes import market

    _reset_market_regime_runtime_state()
    monkeypatch.setattr(collector.clock, "time", lambda: 1000.0)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        collector.global_market_service,
        "snapshot",
        lambda *, force_refresh=False: calls.append(("global", force_refresh)) or {
            "generated_at": "2026-07-13T10:00:00+08:00",
            "data_quality": "ok",
        },
    )
    monkeypatch.setattr(collector, "persist_global_evidence_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        market,
        "_sector_temperature_snapshot",
        lambda board_type, force_refresh=False, db=None: calls.append(
            (f"sector:{board_type}", force_refresh)
        ),
    )
    monkeypatch.setattr(
        collector.opportunity_market_provider,
        "theme_radar",
        lambda force_refresh=False: calls.append(("theme", force_refresh)),
    )

    collector._run_market_auxiliary_collections(db_session, force=False)
    collector._run_market_auxiliary_collections(db_session, force=True)

    assert len(calls) == 8
    assert [force_refresh for _, force_refresh in calls[:4]] == [False] * 4
    assert [force_refresh for _, force_refresh in calls[4:]] == [True] * 4


def test_auxiliary_failures_are_diagnostic_without_failing_main_snapshot(monkeypatch, db_session):
    from app.api.routes import market

    _reset_market_regime_runtime_state()
    expected = object()
    monkeypatch.setattr(collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(collector, "get_market_regime", lambda *_args, **_kwargs: expected)
    monkeypatch.setattr(
        collector.global_market_service,
        "snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("global unavailable")),
    )

    def sector_snapshot(board_type, **_kwargs):
        if board_type == "行业":
            raise TimeoutError("industry timeout")
        return None

    monkeypatch.setattr(market, "_sector_temperature_snapshot", sector_snapshot)
    monkeypatch.setattr(
        collector.opportunity_market_provider,
        "theme_radar",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("theme invalid")),
    )

    result = collector.run_market_regime_collection_once(
        now=datetime(2026, 7, 13, 10, 5),
    )

    assert result is expected
    assert collector._market_regime_last_success_at is not None
    assert "RuntimeError" in collector._market_regime_last_error
    assert "TimeoutError" in collector._market_regime_last_error
    assert "ValueError" in collector._market_regime_last_error
    status = collector.collector_status()
    assert status["market_auxiliary_errors"]


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
