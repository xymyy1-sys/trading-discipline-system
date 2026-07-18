from datetime import datetime
from io import BytesIO

import pytest

from app.models.trading import MarketRegimeSnapshot
from app.schemas.trading import (
    MarketIndexStateOut,
    MarketRegimeMetrics,
    MarketSectorEvidenceOut,
    SectorFlowItem,
    SectorFlowOut,
)
from app.services.market_regime import (
    MarketRegimeCollection,
    _fetch_official_turnover_day,
    _fetch_official_turnover_history,
    _fetch_limit_pool_count,
    _fetch_sector_evidence,
    _fetch_sse_stock_turnover_yi,
    _fetch_szse_stock_turnover_yi,
    _fetch_turnover_history,
    classify_market_regime,
    clear_market_regime_cache,
    summarize_all_a_rows,
)


def _metrics(**overrides) -> MarketRegimeMetrics:
    values = {
        "active_stock_count": 5000,
        "up_count": 2500,
        "down_count": 2400,
        "flat_count": 100,
        "up_5pct_count": 120,
        "down_5pct_count": 90,
        "limit_up_count": 35,
        "limit_down_count": 18,
        "median_change_pct": 0.05,
        "advance_ratio": 0.51,
        "turnover_yi": 11000.0,
        "projected_turnover_yi": 14000.0,
        "previous_turnover_yi": 13800.0,
        "avg5_turnover_yi": 14000.0,
        "volume_ratio_previous": 1.01,
        "volume_ratio_5d": 1.0,
        "market_main_net_inflow_yi": 10.0,
        "index_composite_change_pct": 0.1,
        "index_above_vwap_count": 2,
        "index_valid_count": 4,
        "positive_sector_count": 45,
        "negative_sector_count": 45,
        "positive_sector_ratio": 0.5,
        "sector_above_vwap_ratio": 0.5,
        "top3_inflow_share": 0.3,
    }
    values.update(overrides)
    return MarketRegimeMetrics(**values)


@pytest.mark.parametrize(
    ("expected", "overrides"),
    [
        (
            "EXTREME_SHRINK_DECLINE",
            {
                "volume_ratio_5d": 0.7,
                "advance_ratio": 0.2,
                "index_composite_change_pct": -1.2,
                "limit_up_count": 5,
                "limit_down_count": 30,
                "market_main_net_inflow_yi": -800.0,
                "positive_sector_ratio": 0.2,
            },
        ),
        (
            "VOLUME_SELL_OFF",
            {
                "volume_ratio_5d": 1.2,
                "advance_ratio": 0.25,
                "index_composite_change_pct": -1.5,
                "limit_up_count": 8,
                "limit_down_count": 35,
                "market_main_net_inflow_yi": -1000.0,
                "positive_sector_ratio": 0.2,
                "index_above_vwap_count": 0,
            },
        ),
        (
            "VOLUME_BROAD_RALLY",
            {
                "volume_ratio_5d": 1.15,
                "advance_ratio": 0.72,
                "index_composite_change_pct": 1.2,
                "limit_up_count": 80,
                "limit_down_count": 5,
                "market_main_net_inflow_yi": 900.0,
                "positive_sector_ratio": 0.75,
                "index_above_vwap_count": 4,
            },
        ),
        (
            "SHRINK_ROTATION",
            {
                "volume_ratio_5d": 0.85,
                "advance_ratio": 0.48,
                "index_composite_change_pct": 0.1,
                "limit_up_count": 30,
                "limit_down_count": 15,
                "market_main_net_inflow_yi": 50.0,
                "positive_sector_ratio": 0.45,
                "top3_inflow_share": 0.65,
            },
        ),
        ("NEUTRAL_DIVERGENCE", {}),
    ],
)
def test_six_state_classifier_static_states(expected, overrides):
    assert classify_market_regime(_metrics(**overrides)).regime_code == expected


def test_classifier_detects_stabilizing_repair_from_previous_snapshot():
    previous = _metrics(
        advance_ratio=0.25,
        index_composite_change_pct=-1.1,
        positive_sector_ratio=0.25,
        market_main_net_inflow_yi=-800.0,
        index_above_vwap_count=0,
    )
    current = _metrics(
        advance_ratio=0.46,
        index_composite_change_pct=0.1,
        positive_sector_ratio=0.5,
        market_main_net_inflow_yi=-100.0,
        index_above_vwap_count=3,
    )

    result = classify_market_regime(current, previous)

    assert result.regime_code == "STABILIZING_REPAIR"
    assert any("上一快照" in item for item in result.evidence)


def test_classifier_returns_unknown_instead_of_filling_a_critical_gap():
    result = classify_market_regime(_metrics(volume_ratio_5d=None))

    assert result.regime_code == "UNKNOWN"
    assert "预计全天成交额/5日均额" in result.missing_fields
    assert result.confidence < 0.98


def test_classifier_recognises_sharp_day_on_day_contraction_with_broad_decline():
    result = classify_market_regime(_metrics(
        volume_ratio_previous=0.836,
        volume_ratio_5d=0.974,
        advance_ratio=0.145,
        index_composite_change_pct=-3.015,
        limit_up_count=29,
        limit_down_count=172,
        market_main_net_inflow_yi=-1706.2,
        positive_sector_ratio=0.151,
    ))

    assert result.regime_code == "EXTREME_SHRINK_DECLINE"
    assert any("前日的0.84倍" in item for item in result.evidence)


@pytest.mark.parametrize(
    ("path", "total"),
    [("getTopicZTPool", 29), ("getTopicDTPool", 172)],
)
def test_dated_limit_pool_uses_returned_total_even_when_pool_is_empty(monkeypatch, path, total):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"qdate": 20260713, "tc": total, "pool": []}}

    monkeypatch.setattr("app.services.market_regime.requests.get", lambda *args, **kwargs: Response())

    assert _fetch_limit_pool_count(path, "20260713") == total


def test_all_a_summary_uses_real_rows_and_marks_incomplete_flow_coverage():
    rows = [
        {"f12": "600001", "f14": "样本一", "f2": 11, "f3": 10.01, "f6": 1e8, "f62": 2e7},
        {"f12": "300001", "f14": "样本二", "f2": 12, "f3": -19.8, "f6": 2e8},
        {"f12": "830001", "f14": "样本三", "f2": 13, "f3": 30.0, "f6": 3e8},
        {"f12": "600004", "f14": "ST样本", "f2": 14, "f3": -5.0, "f6": 4e8, "f62": -1e7},
    ]

    result, notes = summarize_all_a_rows(
        rows,
        expected_total=4,
        now=datetime(2026, 7, 13, 10, 30),
    )

    assert result["active_stock_count"] == 4
    assert result["up_count"] == 2
    assert result["down_count"] == 2
    assert result["limit_up_count"] == 2
    assert result["limit_down_count"] == 2
    assert result["turnover_yi"] == 10.0
    assert result["market_main_net_inflow_yi"] is None
    assert any("不足90%" in item for item in notes)


def test_all_a_summary_rejects_a_truncated_sorted_page():
    rows = [
        {"f12": f"60{index:04d}", "f14": f"样本{index}", "f2": 10, "f3": 5, "f6": 1e8, "f62": 1e7}
        for index in range(80)
    ]

    result, notes = summarize_all_a_rows(rows, expected_total=100, now=datetime(2026, 7, 13, 15, 0))

    assert result == {}
    assert any("拒绝用涨幅排序的局部榜单" in item for item in notes)


def test_completed_previous_trade_day_keeps_full_turnover_before_next_open():
    source_stamp = int(datetime(2026, 7, 13, 15, 0).timestamp())
    rows = [
        {"f12": "600001", "f14": "样本", "f2": 10, "f3": -1, "f6": 2e8, "f62": -1e7, "f124": source_stamp}
    ]

    result, notes = summarize_all_a_rows(
        rows,
        expected_total=1,
        now=datetime(2026, 7, 14, 8, 30),
    )

    assert result["turnover_yi"] == 2.0
    assert result["projected_turnover_yi"] == 2.0
    assert not any("09:35前" in item for item in notes)


def test_official_turnover_day_requires_both_exchange_totals(monkeypatch):
    monkeypatch.setattr(
        "app.services.market_regime._fetch_sse_stock_turnover_yi",
        lambda trade_date: 15644.43,
    )
    monkeypatch.setattr(
        "app.services.market_regime._fetch_szse_stock_turnover_yi",
        lambda trade_date: 18282.2032251522,
    )

    assert _fetch_official_turnover_day("2026-07-10") == 33926.63


def test_sse_official_turnover_reads_stock_total_in_yi(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "result": [
                    {"PRODUCT_CODE": "01", "TRADE_DATE": "20260710", "TRADE_AMT": "9603.99"},
                    {"PRODUCT_CODE": "17", "TRADE_DATE": "20260710", "TRADE_AMT": "15644.43"},
                ]
            }

    monkeypatch.setattr("app.services.market_regime.requests.get", lambda *args, **kwargs: Response())

    assert _fetch_sse_stock_turnover_yi("2026-07-10") == 15644.43


def test_szse_official_turnover_normalises_yuan_to_yi(monkeypatch):
    import pandas as pd

    workbook = BytesIO()
    pd.DataFrame([
        {"证券类别": "股票", "数量(只)": 2933, "成交金额(元)": "1,828,220,322,515.22"},
        {"证券类别": "基金", "数量(只)": 1006, "成交金额(元)": "176,229,752,491.92"},
    ]).to_excel(workbook, index=False, engine="openpyxl")

    class Response:
        headers = {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
        content = workbook.getvalue()

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.services.market_regime.requests.get", lambda *args, **kwargs: Response())

    assert _fetch_szse_stock_turnover_yi("2026-07-10") == pytest.approx(18282.2032251522)


def test_official_turnover_history_skips_missing_days_without_estimation(monkeypatch):
    values = {
        "2026-07-10": 33926.63,
        "2026-07-09": 29180.29,
        "2026-07-08": 25681.87,
        "2026-07-07": 25851.17,
        "2026-07-06": 30946.64,
    }

    def fake_day(trade_date):
        if trade_date not in values:
            raise ValueError("non-trading or unavailable day")
        return values[trade_date]

    monkeypatch.setattr("app.services.market_regime._fetch_official_turnover_day", fake_day)

    result = _fetch_official_turnover_history("2026-07-13")

    assert result == values


def test_turnover_history_falls_back_to_paired_exchange_official_data(monkeypatch):
    monkeypatch.setattr(
        "app.services.market_regime._fetch_index_daily_amount",
        lambda secid: (_ for _ in ()).throw(ConnectionError("eastmoney unavailable")),
    )
    official = {
        "2026-07-06": 30946.64,
        "2026-07-07": 25851.17,
        "2026-07-08": 25681.87,
        "2026-07-09": 29180.29,
        "2026-07-10": 33926.63,
    }
    monkeypatch.setattr(
        "app.services.market_regime._fetch_official_turnover_history",
        lambda trade_date: official,
    )

    previous, avg5, source, notes = _fetch_turnover_history("2026-07-13")

    assert previous == 33926.63
    assert avg5 == pytest.approx(29117.32, abs=0.01)
    assert source == "sse-szse-official-stock-turnover"
    assert any("上交所、深交所官方" in item for item in notes)


def test_turnover_history_does_not_invent_five_day_average(monkeypatch):
    monkeypatch.setattr(
        "app.services.market_regime._fetch_index_daily_amount",
        lambda secid: {},
    )
    monkeypatch.setattr(
        "app.services.market_regime._fetch_official_turnover_history",
        lambda trade_date: {"2026-07-10": 33926.63},
    )

    previous, avg5, source, notes = _fetch_turnover_history("2026-07-13")

    assert previous == 33926.63
    assert avg5 is None
    assert source == "sse-szse-official-stock-turnover"
    assert any("拒绝生成5日均值" in item for item in notes)


def test_sector_breadth_uses_full_industry_universe_not_top20_display(monkeypatch):
    visible = SectorFlowOut(
        source="eastmoney",
        updated_at=datetime(2026, 7, 13, 10, 30),
        inflow=[
            SectorFlowItem(
                name="行业甲",
                change_pct=3.0,
                net_inflow=40.0,
                main_inflow=30.0,
                strength=90,
                rank=1,
                leaders=[],
                timeline=[],
                sector_below_vwap=False,
            )
        ],
        outflow=[
            SectorFlowItem(
                name="行业己",
                change_pct=-2.0,
                net_inflow=-20.0,
                main_inflow=-15.0,
                strength=10,
                rank=1,
                leaders=[],
                timeline=[],
                sector_below_vwap=True,
            )
        ],
    )
    raw_rows = [
        {"name": "行业甲", "change_pct": 3.0, "net_inflow": 40.0, "main_inflow": 30.0},
        {"name": "行业乙", "change_pct": 2.0, "net_inflow": 30.0, "main_inflow": 20.0},
        {"name": "行业丙", "change_pct": 1.0, "net_inflow": 20.0, "main_inflow": 10.0},
        {"name": "行业丁", "change_pct": 0.5, "net_inflow": 10.0, "main_inflow": 5.0},
        {"name": "行业戊", "change_pct": -1.0, "net_inflow": -10.0, "main_inflow": -5.0},
        {"name": "行业己", "change_pct": -2.0, "net_inflow": -20.0, "main_inflow": -15.0},
    ]
    monkeypatch.setattr(
        "app.services.market_regime.MarketDataProvider.sector_flow",
        lambda self, **kwargs: visible,
    )
    monkeypatch.setattr(
        "app.services.market_regime.MarketDataProvider._fetch_direct_eastmoney_sector_flow_raw",
        lambda self, flow_type, period: raw_rows,
    )

    result, source, notes = _fetch_sector_evidence(force_refresh=True)

    assert result["positive_sector_count"] == 4
    assert result["negative_sector_count"] == 2
    assert result["positive_sector_ratio"] == pytest.approx(4 / 6, abs=0.0001)
    assert result["top3_inflow_share"] == 0.9
    assert len(result["strongest_sectors"]) == 4
    assert source.endswith("+eastmoney-sector-full")
    assert any("真实分钟曲线" in item for item in notes)


def test_market_regime_get_is_read_only_and_post_refresh_persists_collection(
    monkeypatch, client, db_session
):
    captured_at = datetime(2026, 7, 13, 10, 30)
    collection = MarketRegimeCollection(
        metrics=_metrics(
            volume_ratio_5d=1.15,
            advance_ratio=0.72,
            index_composite_change_pct=1.2,
            limit_up_count=80,
            limit_down_count=5,
            market_main_net_inflow_yi=900.0,
            positive_sector_ratio=0.75,
            index_above_vwap_count=4,
        ),
        trade_date="2026-07-13",
        captured_at=captured_at,
        source="eastmoney-test-fixture",
        indices=[
            MarketIndexStateOut(
                code="000001",
                name="上证指数",
                current=3500,
                change_pct=1.1,
                intraday_vwap=3480,
                above_vwap=True,
                data_quality="complete",
                source="eastmoney-test-fixture",
            )
        ],
        strongest_sectors=[
            MarketSectorEvidenceOut(
                name="半导体",
                change_pct=3.2,
                net_inflow=100.0,
                main_inflow=80.0,
                rank=1,
                above_vwap=True,
            )
        ],
        notes=["fixture仅替换采集层，验证接口与持久化。"],
    )
    collection_calls: list[bool] = []

    def fake_collection(force_refresh=False):
        collection_calls.append(force_refresh)
        return collection

    monkeypatch.setattr(
        "app.services.market_regime.collect_market_regime_inputs",
        fake_collection,
    )
    clear_market_regime_cache()

    read_response = client.get("/api/market/regime?force_refresh=true")

    assert read_response.status_code == 200
    assert read_response.json()["regime_code"] == "UNKNOWN"
    assert collection_calls == []
    assert db_session.query(MarketRegimeSnapshot).count() == 0

    response = client.post("/api/market/regime/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["regime_code"] == "VOLUME_BROAD_RALLY"
    assert payload["source"] == "eastmoney-test-fixture"
    assert payload["indices"][0]["code"] == "000001"
    assert collection_calls == [True]
    assert db_session.query(MarketRegimeSnapshot).count() == 1
    row = db_session.query(MarketRegimeSnapshot).one()
    assert row.trade_date == "2026-07-13"
    assert row.regime_code == "VOLUME_BROAD_RALLY"
