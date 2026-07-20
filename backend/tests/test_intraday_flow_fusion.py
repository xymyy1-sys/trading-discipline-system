from types import SimpleNamespace

from app.models.trading import Holding
from app.services import intraday_collector


def test_collector_builds_seesaw_once_and_passes_each_holding_item(db_session, monkeypatch):
    holdings = [
        Holding(code="600584", name="长电科技", quantity=100, cost_price=100, current_price=101, total_asset=100000),
        Holding(code="600879", name="航天电子", quantity=200, cost_price=20, current_price=21, total_asset=100000),
    ]
    db_session.add_all(holdings)
    db_session.commit()

    monitor_calls: list[list[str]] = []
    cache_reads = 0
    global_snapshot = {
        "data_quality": "ok",
        "us_indices": [{"symbol": "SPX", "status": "ok", "change_pct": -1.2}],
    }
    received: list[tuple[str, object | None, object | None]] = []
    flow_items = {
        row.code: SimpleNamespace(code=row.code, sector_flow_turning="TURN_TO_OUTFLOW")
        for row in holdings
    }

    def fake_monitor(rows, force_refresh=False):
        monitor_calls.append([row.code for row in rows])
        assert force_refresh is True
        return SimpleNamespace(holding_alerts=list(flow_items.values()))

    def fake_cached_global_snapshot():
        nonlocal cache_reads
        cache_reads += 1
        return global_snapshot

    def fake_collect(_db, holding, *, stage, now, seesaw=None, global_cues=None):
        received.append((holding.code, seesaw, global_cues))
        return SimpleNamespace(), SimpleNamespace(recommendation=None), SimpleNamespace()

    monkeypatch.setattr(intraday_collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(intraday_collector, "_is_market_watch_time", lambda _now=None: True)
    monkeypatch.setattr(intraday_collector, "_market_seesaw_monitor", fake_monitor)
    monkeypatch.setattr(
        intraday_collector.global_market_service,
        "read_cached_snapshot",
        fake_cached_global_snapshot,
    )
    monkeypatch.setattr(intraday_collector, "collect_holding_evidence", fake_collect)

    result = intraday_collector.run_intraday_collection_once("test")

    assert result.status == "success"
    assert cache_reads == 1
    assert len(monitor_calls) == 1
    assert sorted(monitor_calls[0]) == ["600584", "600879"]
    assert sorted(code for code, _, _ in received) == ["600584", "600879"]
    assert all(seesaw is flow_items[code] for code, seesaw, _ in received)
    assert all(global_cues is global_snapshot for _, _, global_cues in received)


def test_collector_isolates_global_cache_failure_and_keeps_holdings_running(db_session, monkeypatch):
    holding = Holding(
        code="600584",
        name="长电科技",
        quantity=100,
        cost_price=100,
        current_price=101,
        total_asset=100000,
    )
    db_session.add(holding)
    db_session.commit()
    received: list[object | None] = []

    monkeypatch.setattr(intraday_collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(intraday_collector, "_is_market_watch_time", lambda _now=None: True)
    monkeypatch.setattr(
        intraday_collector,
        "_market_seesaw_monitor",
        lambda _rows, **_kwargs: SimpleNamespace(holding_alerts=[]),
    )
    monkeypatch.setattr(
        intraday_collector.global_market_service,
        "read_cached_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("cache unavailable")),
    )

    def fake_collect(_db, _holding, *, stage, now, seesaw=None, global_cues=None):
        received.append(global_cues)
        return SimpleNamespace(), SimpleNamespace(recommendation=None), SimpleNamespace()

    monkeypatch.setattr(intraday_collector, "collect_holding_evidence", fake_collect)

    result = intraday_collector.run_intraday_collection_once("test-global-cache-failure")

    assert result.status == "success"
    assert received == [None]
    assert "RuntimeError" in result.notes_json
