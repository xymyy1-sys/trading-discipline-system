from __future__ import annotations

from datetime import datetime

from app.models.trading import (
    AccountDailyRisk,
    AccountState,
    DataCaptureSnapshot,
    ExpectationSnapshot,
    Holding,
    TimeStopRule,
    VolumePriceSnapshot,
    WatchlistEntry,
)
from app.schemas.trading import LimitUpGroupOut, LimitUpLadderOut, LimitUpStockOut


def _forbid_commit(*_args, **_kwargs):
    raise AssertionError("GET endpoint attempted to commit")


def _forbid_provider(*_args, **_kwargs):
    raise AssertionError("GET endpoint attempted external provider I/O")


def test_response_cache_keeps_last_snapshot_after_freshness_ttl(monkeypatch):
    from app.services import cache

    cache._response_cache.clear()
    monkeypatch.setattr(cache.time, "time", lambda: 1000.0)
    cache._set_response_cache("read-model", {"value": 7})

    monkeypatch.setattr(cache.time, "time", lambda: 1000.0 + cache._CACHE_TTL_SECONDS + 1)
    assert cache._get_response_cache("read-model") is None
    assert cache._get_response_cache("read-model", allow_stale=True) == {"value": 7}
    assert "read-model" in cache._response_cache
    cache._response_cache.clear()


def test_holdings_get_uses_persisted_price_without_network_or_account_seed(
    client,
    db_session,
    monkeypatch,
):
    from app.api.routes import holdings as holdings_routes

    holding = Holding(
        code="600001",
        name="只读持仓",
        quantity=100,
        cost_price=9.5,
        current_price=10.0,
        total_asset=100000,
    )
    db_session.add(holding)
    db_session.commit()
    original_updated_at = holding.updated_at

    monkeypatch.setattr(
        holdings_routes,
        "_refresh_holding_prices",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GET fetched quotes")),
    )
    monkeypatch.setattr(db_session, "commit", _forbid_commit)

    response = client.get("/api/holdings")

    assert response.status_code == 200
    assert response.json()[0]["current_price"] == 10.0
    assert response.json()[0]["price_source"] == "manual"
    assert db_session.query(AccountState).count() == 0
    db_session.expire(holding)
    assert holding.current_price == 10.0
    assert holding.updated_at == original_updated_at


def test_watchlist_get_does_not_rotate_convert_or_commit(
    client,
    db_session,
    monkeypatch,
):
    from app.services.market_data import MarketDataProvider

    saved = WatchlistEntry(
        code="600101",
        name="已保存观察标的",
        source="auto",
        status="active",
        snapshot_date="2026-07-17",
        snapshot_rank=1,
        category="昨日涨停承接观察",
        entry_reason="上一轮盘后入选",
    )
    db_session.add(saved)
    db_session.add(Holding(
        code="600101",
        name="已转持仓但尚未同步生命周期",
        quantity=100,
        cost_price=10,
        current_price=10,
        total_asset=100000,
    ))
    db_session.commit()

    ladder = LimitUpLadderOut(
        source="只读测试行情",
        trade_date="2026-07-18",
        updated_at=datetime.now(),
        groups=[LimitUpGroupOut(
            level=1,
            label="首板",
            stocks=[LimitUpStockOut(
                code="600102",
                name="不应由GET新增",
                price=10,
                turnover=10,
                sealed_amount=1,
                break_count=0,
                consecutive_limit_days=1,
                concepts=["测试"],
            )],
        )],
        clusters=[],
        summary=[],
        notes=[],
    )
    monkeypatch.setattr(MarketDataProvider, "theme_radar", _forbid_provider)
    monkeypatch.setattr(MarketDataProvider, "limit_up_ladder", _forbid_provider)
    monkeypatch.setattr(MarketDataProvider, "broken_limit_pool", _forbid_provider)
    monkeypatch.setattr(db_session, "commit", _forbid_commit)

    response = client.get("/api/watchlist-recommendations")

    assert response.status_code == 200
    assert [item["code"] for item in response.json()] == ["600101"]
    assert response.json()[0]["converted"] is True  # derived, not persisted
    assert db_session.query(WatchlistEntry).count() == 1
    db_session.expire(saved)
    assert saved.snapshot_date == "2026-07-17"
    assert saved.converted_at is None


def test_account_risk_get_returns_transient_default_without_daily_row(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(db_session, "commit", _forbid_commit)

    response = client.get("/api/account/risk")

    assert response.status_code == 200
    assert response.json()["data_complete"] is False
    assert db_session.query(AccountDailyRisk).count() == 0


def test_time_stop_get_returns_transient_defaults_without_seeding(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(db_session, "commit", _forbid_commit)

    response = client.get("/api/time-stop-rules")

    assert response.status_code == 200
    assert {item["script_type"] for item in response.json()} == {"default", "breakout", "trend"}
    assert all(item["id"] is None for item in response.json())
    assert db_session.query(TimeStopRule).count() == 0


def test_theme_and_news_cache_only_modes_do_not_touch_external_sources(monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "_get_response_cache", lambda _key, **_kwargs: None)
    monkeypatch.setattr(
        market_data.MarketDataProvider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        _forbid_provider,
    )
    monkeypatch.setattr(
        market_data.MarketDataProvider,
        "_fetch_sina_sector_flow_raw",
        _forbid_provider,
    )
    monkeypatch.setattr(
        market_data.MarketDataProvider,
        "_fetch_eastmoney_fast_news",
        _forbid_provider,
    )
    monkeypatch.setattr(
        market_data.MarketDataProvider,
        "_fetch_cctv_news",
        _forbid_provider,
    )

    provider = market_data.MarketDataProvider()
    theme = provider.theme_radar(cache_only=True)
    news = provider.information_differential(cache_only=True)

    assert theme.source == "cache-unavailable"
    assert theme.themes == []
    assert news.source == "cache-unavailable"
    assert news.items == []


def test_market_navigation_gets_use_cache_and_never_provider_io(
    client,
    db_session,
    monkeypatch,
):
    from app.api.helpers import seesaw
    from app.api.routes import market as market_routes
    from app.services import market_data
    from app.services.global_market import global_market_service

    db_session.add(Holding(
        code="600888",
        name="只读驾驶舱持仓",
        quantity=100,
        cost_price=10,
        current_price=10,
        total_asset=100000,
    ))
    db_session.commit()

    monkeypatch.setattr(market_data, "_get_response_cache", lambda _key, **_kwargs: None)
    monkeypatch.setattr(
        market_data.MarketDataProvider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        _forbid_provider,
    )
    monkeypatch.setattr(market_routes, "get_market_regime", _forbid_provider)
    monkeypatch.setattr(
        market_routes.market_provider,
        "sector_opening_breadth",
        _forbid_provider,
    )
    monkeypatch.setattr(seesaw.requests, "get", _forbid_provider)
    monkeypatch.setattr(seesaw.market_provider, "sector_flow", _forbid_provider)
    monkeypatch.setattr(seesaw.market_provider, "limit_up_ladder", _forbid_provider)
    monkeypatch.setattr(seesaw, "_latest_a_share_quotes", _forbid_provider)
    monkeypatch.setattr(global_market_service, "global_index_loader", _forbid_provider)
    monkeypatch.setattr(global_market_service, "us_stock_loader", _forbid_provider)
    monkeypatch.setattr(global_market_service, "sox_loader", _forbid_provider)
    monkeypatch.setattr(db_session, "commit", _forbid_commit)

    for path in (
        "/api/market/theme-radar",
        "/api/market/regime",
        "/api/market/reflexivity",
        "/api/market/global-cues",
        "/api/market/seesaw-monitor",
        "/api/market/capital-rotation",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)


def test_market_flow_gets_are_cache_only_and_refresh_is_explicit(client, monkeypatch):
    from app.api.routes import market as market_routes

    monkeypatch.setattr(market_routes, "_get_response_cache", lambda _key, **_kwargs: None)
    for method_name in (
        "sector_flow",
        "board_flow_panel",
        "hot_themes",
        "dark_trade",
        "sector_detail",
        "limit_up_ladder",
        "limit_up_atmosphere",
    ):
        monkeypatch.setattr(market_routes.market_provider, method_name, _forbid_provider)
    monkeypatch.setattr(market_routes, "_sector_temperature_snapshot", _forbid_provider)

    paths = (
        "/api/market/sector-flow",
        "/api/market/board-flow-panel",
        "/api/market/hot-themes",
        "/api/market/dark-trade",
        "/api/market/sector-detail?name=测试板块",
        "/api/market/limit-up-ladder",
        "/api/market/sector-temperature",
        "/api/market/limit-up-atmosphere",
    )
    fallback_payloads = {}
    for path in paths:
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.json()["source"] == "cache-unavailable"
        fallback_payloads[path] = response.json()

    calls: list[str] = []

    def refreshed(name: str, payload: dict):
        def loader(*_args, **kwargs):
            assert kwargs.get("force_refresh") is True
            calls.append(name)
            return payload
        return loader

    monkeypatch.setattr(
        market_routes.market_provider,
        "sector_flow",
        refreshed("sector-flow", fallback_payloads[paths[0]]),
    )
    monkeypatch.setattr(
        market_routes.market_provider,
        "board_flow_panel",
        refreshed("board-flow-panel", fallback_payloads[paths[1]]),
    )
    monkeypatch.setattr(
        market_routes.market_provider,
        "hot_themes",
        refreshed("hot-themes", fallback_payloads[paths[2]]),
    )
    monkeypatch.setattr(
        market_routes.market_provider,
        "dark_trade",
        refreshed("dark-trade", fallback_payloads[paths[3]]),
    )
    monkeypatch.setattr(
        market_routes.market_provider,
        "sector_detail",
        refreshed("sector-detail", fallback_payloads[paths[4]]),
    )
    monkeypatch.setattr(
        market_routes.market_provider,
        "limit_up_ladder",
        refreshed("limit-up-ladder", fallback_payloads[paths[5]]),
    )
    monkeypatch.setattr(
        market_routes,
        "_sector_temperature_snapshot",
        refreshed("sector-temperature", fallback_payloads[paths[6]]),
    )
    monkeypatch.setattr(
        market_routes.market_provider,
        "limit_up_atmosphere",
        refreshed("limit-up-atmosphere", fallback_payloads[paths[7]]),
    )

    refresh_paths = (
        "/api/market/sector-flow/refresh",
        "/api/market/board-flow-panel/refresh",
        "/api/market/hot-themes/refresh",
        "/api/market/dark-trade/refresh",
        "/api/market/sector-detail/refresh?name=测试板块",
        "/api/market/limit-up-ladder/refresh",
        "/api/market/sector-temperature/refresh",
        "/api/market/limit-up-atmosphere/refresh",
    )
    for path in refresh_paths:
        response = client.post(path)
        assert response.status_code == 200, (path, response.text)

    assert calls == [
        "sector-flow",
        "board-flow-panel",
        "hot-themes",
        "dark-trade",
        "sector-detail",
        "limit-up-ladder",
        "sector-temperature",
        "limit-up-atmosphere",
    ]


def test_stock_decision_gets_are_transient_read_models_without_network_or_writes(
    client,
    db_session,
    monkeypatch,
):
    from app.api.helpers import seesaw

    holding = Holding(
        code="600889",
        name="只读个股决策",
        quantity=100,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
    )
    db_session.add(holding)
    db_session.commit()

    original_profile = seesaw._holding_stock_board_profile

    def guarded_profile(value, *, allow_network=True):
        assert allow_network is False
        return original_profile(value, allow_network=False)

    monkeypatch.setattr(seesaw, "_holding_stock_board_profile", guarded_profile)
    monkeypatch.setattr(seesaw.requests, "get", _forbid_provider)
    monkeypatch.setattr(seesaw.market_provider, "sector_flow", _forbid_provider)
    monkeypatch.setattr(seesaw.market_provider, "limit_up_ladder", _forbid_provider)
    monkeypatch.setattr(db_session, "commit", _forbid_commit)

    before = {
        "expectation": db_session.query(ExpectationSnapshot).count(),
        "volume": db_session.query(VolumePriceSnapshot).count(),
        "capture": db_session.query(DataCaptureSnapshot).count(),
    }
    for path in (
        "/api/stocks/600889/decision-card",
        "/api/stocks/600889/reflexivity",
        "/api/stocks/600889/expectation",
        "/api/stocks/600889/volume-price",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)

    assert db_session.query(ExpectationSnapshot).count() == before["expectation"]
    assert db_session.query(VolumePriceSnapshot).count() == before["volume"]
    assert db_session.query(DataCaptureSnapshot).count() == before["capture"]
