from datetime import datetime

import pytest

from app.schemas.trading import LimitUpCatcherCriteria, LimitUpCatcherOut
from app.services.market_data import MarketDataProvider


def _row(
    code: str,
    *,
    price: float = 10.5,
    change_pct: float = 4.5,
    volume_lots: float = 1_000,
    intraday_average: float = 10.2,
    turnover_rate: float = 5,
    volume_ratio: float = 4,
) -> dict:
    return {
        "f12": code,
        "f13": "1",
        "f14": f"测试{code}",
        "f2": price,
        "f3": change_pct,
        "f5": volume_lots,
        "f6": intraday_average * volume_lots * 100,
        "f8": turnover_rate,
        "f10": volume_ratio,
        "f124": 1784424600,
    }


def test_limit_up_catcher_applies_all_real_quote_conditions(monkeypatch):
    provider = MarketDataProvider()
    updated_at = datetime(2026, 7, 19, 10, 30)
    rows = [
        _row("600001"),
        _row("600002", volume_ratio=3),
        _row("600003", change_pct=0),
        _row("600004", change_pct=5.01),
        _row("600005", turnover_rate=2.99),
        _row("600006", turnover_rate=8.01),
        _row("600007", price=10.1, intraday_average=10.2),
    ]
    monkeypatch.setattr(
        provider,
        "_fetch_limit_up_catcher_rows",
        lambda: (rows, "eastmoney-all-a@test", updated_at, len(rows)),
    )

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "ok"
    assert result.total_scanned == 7
    assert result.matched_count == 1
    assert [item.code for item in result.items] == ["600001"]
    assert result.items[0].intraday_average == 10.2
    assert result.items[0].average_deviation_pct == 2.94
    assert result.items[0].source == "eastmoney-all-a@test"
    assert result.criteria == LimitUpCatcherCriteria()


def test_limit_up_catcher_provider_failure_is_explicit_data_gap_and_not_cached(monkeypatch):
    from app.services import market_data

    provider = MarketDataProvider()
    writes: list[object] = []

    def unavailable():
        raise RuntimeError("offline")

    monkeypatch.setattr(provider, "_fetch_limit_up_catcher_rows", unavailable)
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "data_gap"
    assert result.source == "eastmoney-unavailable"
    assert result.items == []
    assert writes == []
    assert any("未生成模拟股票" in note for note in result.notes)


@pytest.mark.parametrize("raw_total", [None, 3_999])
def test_limit_up_catcher_rejects_missing_or_implausibly_small_market_total(
    monkeypatch,
    raw_total,
):
    from app.services import market_data

    provider = MarketDataProvider()
    writes: list[object] = []
    payload_data = {"diff": [_row(f"{index:06d}") for index in range(100)]}
    if raw_total is not None:
        payload_data["total"] = raw_total

    monkeypatch.setattr(
        market_data,
        "_get_json_from_hosts",
        lambda *_args, **_kwargs: ({"data": payload_data}, "https://push2.eastmoney.com"),
    )
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "data_gap"
    assert result.items == []
    assert writes == []


def test_limit_up_catcher_rejects_incomplete_pagination_and_does_not_cache(monkeypatch):
    from app.services import market_data

    provider = MarketDataProvider()
    writes: list[object] = []
    first_page = [_row(f"{index:06d}") for index in range(2_000)]
    # 30 of the provider-declared 4,000 securities are deliberately absent.
    second_page = [_row(f"{index:06d}") for index in range(2_000, 3_970)]

    def fake_get(_path, params, timeout=8):
        del timeout
        page = int(params.get("pn") or 1)
        rows = first_page if page == 1 else second_page if page == 2 else []
        return {
            "data": {"total": 4_000, "diff": rows},
        }, "https://push2.eastmoney.com"

    monkeypatch.setattr(market_data, "_get_json_from_hosts", fake_get)
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "data_gap"
    assert result.items == []
    assert writes == []


def test_limit_up_catcher_get_is_cache_only_and_post_is_explicit(client, monkeypatch):
    from app.api.routes import market as market_routes

    monkeypatch.setattr(market_routes, "_get_response_cache", lambda *_args, **_kwargs: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET must not call a provider")

    monkeypatch.setattr(market_routes.market_provider, "limit_up_catcher", forbidden)
    response = client.get("/api/market/limit-up-catcher")
    assert response.status_code == 200
    assert response.json()["data_status"] == "data_gap"
    assert response.json()["source"] == "cache-unavailable"

    calls: list[bool] = []
    payload = LimitUpCatcherOut(
        source="eastmoney-all-a@test",
        updated_at=datetime(2026, 7, 19, 10, 30),
        trade_date="2026-07-19",
        data_status="ok",
        items=[],
        total_scanned=5_400,
        matched_count=0,
        notes=["真实行情中暂无匹配"],
    )

    def refreshed(*, force_refresh: bool = False):
        calls.append(force_refresh)
        return payload

    monkeypatch.setattr(market_routes.market_provider, "limit_up_catcher", refreshed)
    response = client.post("/api/market/limit-up-catcher/refresh")
    assert response.status_code == 200
    assert response.json()["data_status"] == "ok"
    assert response.json()["total_scanned"] == 5_400
    assert calls == [True]
