from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.global_market import GlobalMarketService, KISConfiguration


NOW = datetime(2026, 7, 13, 8, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def _indices():
    return [
        {"代码": "KS11", "名称": "韩国KOSPI", "最新价": 3210.4, "涨跌幅": -1.2, "时间": "2026-07-13 08:30"},
        {"代码": "KOSPI200", "名称": "韩国KOSPI200", "最新价": 431.2, "涨跌幅": -1.0},
        {"代码": "SPX", "名称": "标普500", "最新价": 6120.1, "涨跌幅": 0.8},
        {"代码": "NDX", "名称": "纳斯达克100", "最新价": 22100, "涨跌幅": 1.1},
        {"代码": "DJIA", "名称": "道琼斯", "最新价": 43200, "涨跌幅": 0.2},
    ]


def _us_stocks():
    return [
        {"代码": "105.XLK", "名称": "Technology Select Sector SPDR", "最新价": 250, "涨跌幅": 1.5},
        {"代码": "105.XLE", "名称": "Energy Select Sector SPDR", "最新价": 92, "涨跌幅": -0.4},
        {"代码": "105.SMH", "名称": "VanEck Semiconductor ETF", "最新价": 310, "涨跌幅": 2.1},
    ]


def _sox():
    return [
        {"日期": "2026-07-10", "最新值": 5500, "涨跌幅": 1.7},
        {"日期": "2026-07-11", "最新值": 5575, "涨跌幅": 1.36},
    ]


def test_global_snapshot_normalizes_real_sources_and_ranks_sector_etfs():
    service = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    assert result["quality"] == "ok"
    assert [item["symbol"] for item in result["korea_indices"]] == ["KS11", "KOSPI200"]
    assert {item["symbol"] for item in result["us_indices"]} == {"SPX", "NDX", "DJIA", "SOX"}
    assert [item["symbol"] for item in result["us_sector_rank"]] == ["SMH", "XLK", "XLE"]
    assert result["us_sector_rank"][0]["theme"] == "半导体"
    assert result["generated_at"].startswith("2026-07-13T08:30")
    assert result["as_of"] == result["generated_at"]
    assert result["data_quality"] == result["quality"]
    assert result["source"] == result["sources"]
    assert {item["group"] for item in result["items"]} == {
        "korea_index",
        "korea_equity",
        "us_index",
        "us_sector_proxy",
    }


def test_korean_equities_are_explicitly_unavailable_without_kis_and_have_no_fake_prices():
    service = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    assert result["kis"]["configured"] is False
    assert {item["symbol"] for item in result["korea_equities"]} == {"005930", "000660"}
    assert all(item["status"] == "unavailable" for item in result["korea_equities"])
    assert all(item["price"] is None and item["change_pct"] is None for item in result["korea_equities"])


def test_kis_loader_can_supply_authorized_korean_equities():
    def kis_loader(codes):
        assert set(codes) == {"005930", "000660"}
        return [
            {"code": "005930", "price": 81200, "change_pct": 1.3, "timestamp": "2026-07-13 09:00"},
            {"code": "000660", "price": 238000, "change_pct": 2.4, "timestamp": "2026-07-13 09:00"},
        ]

    service = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        kis_equity_loader=kis_loader,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(app_key="key", app_secret="secret"),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    assert result["kis"]["adapter_enabled"] is True
    assert [item["price"] for item in result["korea_equities"]] == [81200, 238000]
    assert all(item["status"] == "ok" for item in result["korea_equities"])


def test_source_failures_stay_missing_instead_of_creating_zero_quotes():
    def fail():
        raise RuntimeError("offline")

    service = GlobalMarketService(
        global_index_loader=fail,
        us_stock_loader=fail,
        sox_loader=fail,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    assert result["quality"] == "missing"
    assert result["korea_indices"] == []
    assert result["us_indices"] == []
    assert result["us_sector_rank"] == []
    assert any("不生成模拟行业排行" in note for note in result["notes"])


def test_snapshot_cache_avoids_repeated_external_loads():
    calls = {"indices": 0}

    def indices():
        calls["indices"] += 1
        return _indices()

    service = GlobalMarketService(
        global_index_loader=indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        cache_ttl_seconds=60,
        now_provider=lambda: NOW,
    )
    service.snapshot()
    service.snapshot()
    assert calls["indices"] == 1
    service.snapshot(force_refresh=True)
    assert calls["indices"] == 2


def test_yahoo_is_explicit_delayed_fallback_when_kis_is_unavailable():
    def yahoo_loader(symbols):
        assert set(symbols) == {"005930.KS", "000660.KS"}
        return [
            {
                "symbol": "005930.KS",
                "price": 81200,
                "previous_close": 80000,
                "change_pct": 1.5,
                "timestamp": "2026-07-13T08:30:00+08:00",
            },
            {
                "symbol": "000660.KS",
                "price": 238000,
                "previous_close": 235000,
                "change_pct": 1.28,
                "timestamp": "2026-07-13T08:30:00+08:00",
            },
        ]

    service = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        yahoo_equity_loader=yahoo_loader,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    assert all(item["status"] == "delayed" for item in result["korea_equities"])
    assert all(item["source"].startswith("Yahoo Finance") for item in result["korea_equities"])
    assert all("KIS实时行情不可用" in item["note"] for item in result["korea_equities"])
    assert "Yahoo Finance chart v8（只读延迟降级）" in result["sources"]
