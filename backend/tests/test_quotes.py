import json
from datetime import datetime

import pandas as pd

from app.api.helpers.quotes import (
    _attach_minute_bars,
    _daily_history_metrics,
    _eastmoney_minute_bars,
    _eastmoney_secid,
    _eastmoney_tick_flow,
    _latest_a_share_quotes,
    _latest_a_share_quotes_tencent,
    _minute_target_trade_date,
    _sina_minute_bars,
    _tencent_minute_bars,
)
from app.services.effective_flow import INSUFFICIENT_DATA, analyze_effective_flow


def test_eastmoney_secid_handles_a_share_and_etf_markets():
    assert _eastmoney_secid("600584") == "1.600584"
    assert _eastmoney_secid("588710") == "1.588710"
    assert _eastmoney_secid("159915") == "0.159915"


def test_eastmoney_minute_bars_maps_kline_fields(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "klines": [
                        "2026-07-12 09:31,10.00,10.20,10.30,9.95,1234,1250000,3.5,2.0,0.20,1.2",
                        "2026-07-12 09:32,10.20,10.10,10.25,10.05,1000,1015000,2.0,1.0,-0.10,1.0",
                    ]
                }
            }

    monkeypatch.setattr("app.api.helpers.quotes.datetime", type("FixedDateTime", (), {
        "now": staticmethod(lambda: type("Now", (), {"date": lambda self: __import__("datetime").date(2026, 7, 12)})()),
    }))
    monkeypatch.setattr("app.api.helpers.quotes._minute_target_trade_date", lambda: "2026-07-12")
    monkeypatch.setattr("app.api.helpers.quotes.requests.get", lambda *_args, **_kwargs: FakeResponse())

    bars = _eastmoney_minute_bars("600584")

    assert len(bars) == 2
    assert bars[0]["time"] == "09:31"
    assert bars[0]["trade_date"] == "2026-07-12"
    assert bars[0]["price"] == 10.2
    assert bars[0]["volume"] == 123400
    assert bars[0]["amount"] == 1250000
    assert bars[1]["close"] == 10.1


def test_attach_minute_bars_marks_quote_reliable_source(monkeypatch):
    monkeypatch.setattr("app.api.helpers.quotes.datetime", type("FixedDateTime", (), {
        "now": staticmethod(lambda: type("Now", (), {"date": lambda self: __import__("datetime").date(2026, 7, 12)})()),
    }))
    monkeypatch.setattr("app.api.helpers.quotes._minute_target_trade_date", lambda: "2026-07-10")
    monkeypatch.setattr(
        "app.api.helpers.quotes._eastmoney_minute_bars",
        lambda code: [{"trade_date": "2026-07-10", "time": "09:31", "price": 10.2, "volume": 1000, "amount": 10200}],
    )
    quotes = {"600584": {"price": 10.2, "note": "东方财富实时行情"}}

    _attach_minute_bars(quotes)

    assert quotes["600584"]["minute_bars"][0]["price"] == 10.2
    assert quotes["600584"]["minute_bar_source"] == "东方财富1分钟分时K线"
    assert quotes["600584"]["minute_bar_status"] == "ok"
    assert quotes["600584"]["minute_bar_trade_date"] == "2026-07-10"
    assert "东方财富1分钟成交" in quotes["600584"]["note"]
    assert "2026-07-10" in quotes["600584"]["note"]


def test_attach_minute_bars_marks_empty_result_as_degraded(monkeypatch):
    monkeypatch.setattr("app.api.helpers.quotes._eastmoney_minute_bars", lambda code: [])
    monkeypatch.setattr("app.api.helpers.quotes._sina_minute_bars", lambda code: [])
    quotes = {"600584": {"price": 10.2, "note": "东方财富实时行情"}}

    _attach_minute_bars(quotes)

    assert "minute_bars" not in quotes["600584"]
    assert quotes["600584"]["minute_bar_status"] == "no_recent_rows"


def test_sina_minute_bars_maps_ohlcv_and_estimated_amount(monkeypatch):
    frame = pd.DataFrame([{
        "day": "2026-07-10 09:31:00", "open": "10.00", "high": "10.30",
        "low": "9.95", "close": "10.20", "volume": "1000",
    }])
    monkeypatch.setattr("app.api.helpers.quotes._minute_target_trade_date", lambda: "2026-07-10")
    monkeypatch.setattr("akshare.stock_zh_a_minute", lambda **kwargs: frame)
    bars = _sina_minute_bars("600584")
    assert bars[0]["time"] == "09:31"
    assert bars[0]["price"] == 10.2
    assert bars[0]["amount"] == 10200
    assert bars[0]["amount_estimated"] is True


def test_attach_minute_bars_falls_back_to_sina_and_marks_degraded(monkeypatch):
    monkeypatch.setattr("app.api.helpers.quotes._eastmoney_minute_bars", lambda code: (_ for _ in ()).throw(RuntimeError("primary down")))
    monkeypatch.setattr("app.api.helpers.quotes._sina_minute_bars", lambda code: [
        {"trade_date": "2026-07-10", "time": "09:31", "price": 10.2, "volume": 1000, "amount": 10200}
    ])
    quotes = {"600584": {"price": 10.2, "note": "实时行情"}}
    _attach_minute_bars(quotes)
    assert quotes["600584"]["minute_bar_status"] == "fallback_ok"
    assert quotes["600584"]["minute_amount_estimated"] is True
    assert "新浪1分钟" in quotes["600584"]["minute_bar_source"]
    assert "primary down" in quotes["600584"]["minute_fetch_error"]


def test_tencent_quote_maps_exchange_date_and_quote_fields(monkeypatch):
    fields = [""] * 39
    fields[0:7] = ["1", "金牛化工", "600722", "9.91", "9.01", "8.77", "1384875"]
    fields[30:39] = [
        "20260722150000", "0.90", "9.99", "9.91", "8.72", "x",
        "1384875", "132986", "20.36",
    ]
    payload = f'v_sh600722="{"~".join(fields)}";'

    class FakeResponse:
        content = payload.encode("gb18030")

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.api.helpers.quotes.requests.get", lambda *_args, **_kwargs: FakeResponse())
    quotes = _latest_a_share_quotes_tencent(["600722"])

    assert quotes["600722"]["price"] == 9.91
    assert quotes["600722"]["provider_event_at"] == datetime(2026, 7, 22, 15, 0)
    assert quotes["600722"]["amount"] == 13.3
    assert quotes["600722"]["volume"] == 138_487_500
    assert quotes["600722"]["is_delayed_endpoint"] is False


def test_tencent_minute_rows_are_differenced_and_post_close_rows_rejected(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "sh600722": {
                        "data": {
                            "date": "20260722",
                            "data": [
                                "0930 8.77 100 87700.00",
                                "0931 8.80 150 131700.00",
                                "1300 8.80 150 131700.00",
                                "1301 8.90 180 158400.00",
                                "1506 8.95 200 176300.00",
                            ],
                        }
                    }
                }
            }

    monkeypatch.setattr("app.api.helpers.quotes.requests.get", lambda *_args, **_kwargs: FakeResponse())
    bars = _tencent_minute_bars("600722")

    assert [item["time"] for item in bars] == ["09:30", "09:31", "13:01"]
    assert bars[0]["trade_date"] == "2026-07-22"
    assert bars[1]["volume"] == 5_000
    assert bars[1]["amount"] == 44_000
    assert bars[2]["volume"] == 3_000


def test_quote_fallback_prefers_tencent_over_two_session_delayed_edge(monkeypatch):
    delayed = {
        "600722": {
            "price": 8.95,
            "provider_event_at": datetime(2026, 7, 21, 15, 0),
            "provider": "eastmoney-push2delay",
            "is_delayed_endpoint": True,
        }
    }
    current = {
        "600722": {
            "price": 9.91,
            "provider_event_at": datetime(2026, 7, 22, 15, 0),
            "provider": "tencent-qt",
            "is_delayed_endpoint": False,
        }
    }
    monkeypatch.setattr("app.api.helpers.quotes._latest_a_share_quotes_eastmoney", lambda _codes: delayed)
    monkeypatch.setattr("app.api.helpers.quotes._latest_a_share_quotes_tencent", lambda _codes: current)
    monkeypatch.setattr("app.api.helpers.quotes._latest_a_share_quotes_sina", lambda _codes: {})
    monkeypatch.setattr("app.api.helpers.quotes._attach_minute_bars", lambda _quotes: None)

    quotes = _latest_a_share_quotes(["600722"])

    assert quotes["600722"]["price"] == 9.91
    assert quotes["600722"]["provider"] == "tencent-qt"


def test_daily_history_metrics_include_ma_returns_and_estimated_chip_distribution(monkeypatch):
    rows = []
    for index in range(30):
        close = 10 + index * 0.1
        rows.append([f"2026-06-{index + 1:02d}", close - .05, close, close + .1, close - .1, 1000 + index])

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"sh600584": {"day": rows}}}

    monkeypatch.setattr("app.api.helpers.quotes.requests.get", lambda *_args, **_kwargs: FakeResponse())
    metrics = _daily_history_metrics("600584")
    assert metrics["ma20"] > 0
    assert metrics["return_10d"] > 0
    assert 0 <= metrics["chip_profit_ratio"] <= 100
    assert metrics["chip_avg_cost"] > 0
    assert metrics["chip_90_concentration"] >= metrics["chip_70_concentration"]


def test_eastmoney_tick_flow_aggregates_active_and_large_orders(monkeypatch):
    class FakeResponse:
        text = 'data: {"data":{"details":["09:31:01,10.00,300,0,2","09:31:20,10.10,100,0,1","09:32:00,10.20,50,0,4"]}}\n\n'

        def raise_for_status(self):
            return None

    monkeypatch.setattr("app.api.helpers.quotes.requests.get", lambda *_args, **_kwargs: FakeResponse())
    flow = _eastmoney_tick_flow("600584", large_order_threshold=200_000)
    assert flow["09:31"]["active_buy_amount"] == 300_000
    assert flow["09:31"]["active_sell_amount"] == 101_000
    assert flow["09:31"]["large_order_net_amount"] == 300_000
    assert flow["09:31"]["large_order_threshold"] == 200_000
    assert flow["__meta__"]["tick_returned_count"] == 3
    assert flow["__meta__"]["tick_first_time"] == "09:31:01"
    assert flow["__meta__"]["tick_last_time"] == "09:32:00"
    assert flow["__meta__"]["tick_batch_truncated"] is False


def test_eastmoney_tick_truncation_metadata_reaches_effective_flow_guard(monkeypatch):
    trade_date = "2026-07-17"
    klines = [
        f"{trade_date} 10:{minute:02d},10.00,10.{minute:02d},10.30,9.95,1000,1000000,3.5,2.0,0.20,1.2"
        for minute in range(1, 11)
    ]
    details = [f"10:05:{index % 60:02d},10.00,1,0,2" for index in range(2000)]

    class FakeKlineResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"klines": klines}}

    class FakeTickResponse:
        text = f'data: {json.dumps({"data": {"details": details}})}\n\n'

        def raise_for_status(self):
            return None

    def fake_get(url, *_args, **_kwargs):
        return FakeTickResponse() if "/stock/details/" in url else FakeKlineResponse()

    monkeypatch.setattr("app.api.helpers.quotes._minute_target_trade_date", lambda: trade_date)
    monkeypatch.setattr("app.api.helpers.quotes.requests.get", fake_get)

    bars = _eastmoney_minute_bars("600584")

    assert len(bars) == 10
    assert all(bar["tick_batch_truncated"] is True for bar in bars)
    assert all(bar["tick_returned_count"] == 2000 for bar in bars)
    assert all(bar["tick_first_time"] == "10:05:00" for bar in bars)
    assert all("__meta__" not in bar for bar in bars)

    result = analyze_effective_flow(
        bars,
        now=datetime(2026, 7, 17, 10, 10),
        trade_date=trade_date,
        vwap=10.05,
        vwap_reliable=True,
        data_quality="realtime",
        active_flow_source="eastmoney_tick",
    )

    assert result.state == INSUFFICIENT_DATA
    assert "TRUNCATED_TICK_WINDOW" in result.reason_codes
