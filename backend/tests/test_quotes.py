import pandas as pd

from app.api.helpers.quotes import _attach_minute_bars, _daily_history_metrics, _eastmoney_minute_bars, _eastmoney_secid, _sina_minute_bars


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
    monkeypatch.setattr("app.api.helpers.quotes._last_trading_day", lambda: "2026-07-12")
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
    monkeypatch.setattr("app.api.helpers.quotes._last_trading_day", lambda: "2026-07-10")
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
    monkeypatch.setattr("app.api.helpers.quotes._last_trading_day", lambda: "2026-07-10")
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
