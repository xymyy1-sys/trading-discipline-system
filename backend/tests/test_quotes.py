from app.api.helpers.quotes import _attach_minute_bars, _eastmoney_minute_bars


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
    monkeypatch.setattr("app.api.helpers.quotes.requests.get", lambda *_args, **_kwargs: FakeResponse())

    bars = _eastmoney_minute_bars("600584")

    assert len(bars) == 2
    assert bars[0]["time"] == "09:31"
    assert bars[0]["price"] == 10.2
    assert bars[0]["volume"] == 123400
    assert bars[0]["amount"] == 1250000
    assert bars[1]["close"] == 10.1


def test_attach_minute_bars_marks_quote_reliable_source(monkeypatch):
    monkeypatch.setattr(
        "app.api.helpers.quotes._eastmoney_minute_bars",
        lambda code: [{"time": "09:31", "price": 10.2, "volume": 1000, "amount": 10200}],
    )
    quotes = {"600584": {"price": 10.2, "note": "东方财富实时行情"}}

    _attach_minute_bars(quotes)

    assert quotes["600584"]["minute_bars"][0]["price"] == 10.2
    assert quotes["600584"]["minute_bar_source"] == "东方财富1分钟分时K线"
    assert "东方财富1分钟成交" in quotes["600584"]["note"]
