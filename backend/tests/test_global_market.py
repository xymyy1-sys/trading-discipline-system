from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.global_market import (
    GlobalMarketService,
    KISConfiguration,
    _load_configured_official_json,
)


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
        {"代码": "105.EWY", "名称": "iShares MSCI South Korea ETF", "最新价": 86, "涨跌幅": -0.7},
        {"代码": "105.MU", "名称": "Micron Technology", "最新价": 151, "涨跌幅": 1.8},
    ]


def _macro():
    return [
        {"symbol": "KRW=X", "price": 1392.4, "change_pct": 0.35, "timestamp": "2026-07-13T08:20:00+08:00"},
        {"symbol": "DX-Y.NYB", "price": 99.8, "change_pct": -0.1, "timestamp": "2026-07-13T08:20:00+08:00"},
        {"symbol": "^TNX", "price": 4.31, "change_pct": 0.2, "timestamp": "2026-07-13T08:20:00+08:00"},
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
        macro_loader=_macro,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    assert result["quality"] == "degraded"
    assert result["quote_quality"] == "ok"
    assert result["institutional_flow_quality"] == "missing"
    assert [item["symbol"] for item in result["korea_indices"]] == ["KS11", "KOSPI200"]
    assert {item["symbol"] for item in result["us_indices"]} == {"SPX", "NDX", "DJIA", "SOX"}
    assert [item["symbol"] for item in result["us_sector_rank"]] == ["SMH", "XLK", "XLE"]
    assert result["us_sector_rank"][0]["theme"] == "半导体"
    assert {item["symbol"] for item in result["strategic_assets"]} == {"EWY", "MU"}
    assert {item["symbol"] for item in result["macro_indicators"]} == {"USDKRW", "DXY", "US10Y"}
    assert result["quality_details"]["core_semiconductor_available"] is True
    assert all(item["source_url"] for item in result["strategic_assets"])
    assert all(item["observed_at"] for item in result["macro_indicators"])
    assert all(item["metric_kind"] == "index_price" for item in result["korea_indices"])
    assert all(item["data_quality"] in {"ok", "delayed"} for item in result["us_indices"])
    assert result["generated_at"].startswith("2026-07-13T08:30")
    assert result["as_of"] == result["generated_at"]
    assert result["data_quality"] == result["quality"]
    assert result["source"] == result["sources"]
    assert {item["group"] for item in result["items"]} == {
        "korea_index",
        "korea_equity",
        "us_index",
        "us_sector_proxy",
        "strategic_asset",
        "macro_indicator",
    }


def test_unlicensed_flow_datasets_are_explicitly_unavailable_and_never_inferred():
    service = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        macro_loader=_macro,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    result = service.snapshot()

    for group in ("etf_flows", "korea_foreign_flows", "korea_leverage_products", "official_rates"):
        assert len(result[group]) == 1
        assert result[group][0]["status"] == "unavailable"
        assert result[group][0]["value"] is None
        assert "禁止" in result[group][0]["note"]
    assert result["official_adapters"]["etf_flow"]["configured"] is False


def test_official_adapter_rejects_credentials_embedded_in_endpoint_url():
    with pytest.raises(ValueError, match="HTTPS"):
        _load_configured_official_json("https://user:secret@licensed.example.test/data")


def test_authorised_metric_adapter_requires_value_url_and_source_timestamp():
    def etf_flow_loader():
        return [{
            "metric_id": "EWY_SHARES",
            "name": "EWY基金份额",
            "value": 115_000_000,
            "unit": "份",
            "change": -2_000_000,
            "direction": "redemption",
            "published_at": "2026-07-12T20:00:00-04:00",
            "source": "iShares authorised adapter",
            "source_url": "https://www.ishares.com/us/products/239681/",
            "data_quality": "official_audited",
            "metric_kind": "official_interest_rate",
            "related_a_share_sectors": ["半导体", "消费电子"],
        }]

    service = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        macro_loader=_macro,
        etf_flow_loader=etf_flow_loader,
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    )

    item = service.snapshot()["etf_flows"][0]
    assert item["status"] == "ok"
    assert item["value"] == 115_000_000
    assert item["metric_kind"] == "etf_share_creation_redemption"
    assert item["data_quality"] == "official_audited"
    assert item["change"] == -2_000_000
    assert item["direction"] == "outflow"
    assert item["source_url"].startswith("https://www.ishares.com/")
    assert item["observed_at"].startswith("2026-07-13T08:30")

    incomplete = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        macro_loader=_macro,
        korea_foreign_flow_loader=lambda: [{"value": 99, "source": "vendor"}],
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    ).snapshot()["korea_foreign_flows"][0]
    assert incomplete["status"] == "unavailable"
    assert incomplete["value"] is None


def test_quote_and_institutional_flow_quality_are_reported_independently():
    def metric_loader(metric_id: str, value: float):
        return lambda: [{
            "metric_id": metric_id,
            "name": metric_id,
            "value": value,
            "published_at": "2026-07-13T08:00:00+08:00",
            "source": "authorised test adapter",
            "source_url": f"https://licensed.example.test/{metric_id}",
            "data_quality": "audited",
        }]

    result = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        macro_loader=_macro,
        etf_flow_loader=metric_loader("ETF_NET_CREATION", 2),
        korea_foreign_flow_loader=metric_loader("KR_FOREIGN_NET", 3),
        korea_leverage_loader=metric_loader("KR_LEVERAGE", 4),
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    ).snapshot()

    assert result["quote_quality"] == "ok"
    assert result["institutional_flow_quality"] == "ok"
    assert result["data_quality"] == "ok"
    assert result["quality_details"]["institutional_flow_available_count"] == 3


@pytest.mark.parametrize(
    "updates",
    [
        {"data_quality": "ok"},
        {"data_quality": "estimated"},
        {"source_url": "http://licensed.example.test/metric"},
        {"source_url": "https://user:secret@licensed.example.test/metric"},
        {"published_at": "2026-07-13 08:00:00"},
        {"published_at": "2026-07-13T08:40:00+08:00"},
        {"published_at": "2026-07-01T08:00:00+08:00"},
        {"metric_id": ""},
        {"name": ""},
        {"source": ""},
    ],
)
def test_authorised_metric_adapter_rejects_unverifiable_rows(updates):
    row = {
        "metric_id": "KR_FOREIGN_NET",
        "name": "韩国外资净买卖",
        "value": -12.5,
        "published_at": "2026-07-13T08:00:00+08:00",
        "source": "official provider",
        "source_url": "https://licensed.example.test/metric",
        "data_quality": "official",
    }
    row.update(updates)
    item = GlobalMarketService(
        global_index_loader=_indices,
        us_stock_loader=_us_stocks,
        sox_loader=_sox,
        macro_loader=_macro,
        korea_foreign_flow_loader=lambda: [row],
        yahoo_equity_loader=None,
        kis_config=KISConfiguration(),
        now_provider=lambda: NOW,
    ).snapshot()["korea_foreign_flows"][0]

    assert item["status"] == "unavailable"
    assert item["value"] is None
    assert item["data_quality"] == "missing"


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
