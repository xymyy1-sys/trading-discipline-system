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
    received: list[tuple[str, object | None]] = []
    flow_items = {
        row.code: SimpleNamespace(code=row.code, sector_flow_turning="TURN_TO_OUTFLOW")
        for row in holdings
    }

    def fake_monitor(rows, force_refresh=False):
        monitor_calls.append([row.code for row in rows])
        assert force_refresh is True
        return SimpleNamespace(holding_alerts=list(flow_items.values()))

    def fake_collect(_db, holding, *, stage, now, seesaw=None):
        received.append((holding.code, seesaw))
        return SimpleNamespace(), SimpleNamespace(recommendation=None), SimpleNamespace()

    monkeypatch.setattr(intraday_collector, "SessionLocal", lambda: db_session)
    monkeypatch.setattr(intraday_collector, "_is_market_watch_time", lambda _now=None: True)
    monkeypatch.setattr(intraday_collector, "_market_seesaw_monitor", fake_monitor)
    monkeypatch.setattr(intraday_collector, "collect_holding_evidence", fake_collect)

    result = intraday_collector.run_intraday_collection_once("test")

    assert result.status == "success"
    assert len(monitor_calls) == 1
    assert sorted(monitor_calls[0]) == ["600584", "600879"]
    assert sorted(code for code, _ in received) == ["600584", "600879"]
    assert all(seesaw is flow_items[code] for code, seesaw in received)
