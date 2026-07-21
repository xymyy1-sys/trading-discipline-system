from __future__ import annotations

from datetime import datetime

from app.services.market_data import MarketDataProvider


def test_eastmoney_sector_flow_preserves_real_turnover_and_leader_return(monkeypatch) -> None:
    provider = MarketDataProvider()

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": {
                    "total": 1,
                    "diff": [
                        {
                            "f12": "BK_TEST",
                            "f14": "测试行业",
                            "f2": 1010,
                            "f3": 1.25,
                            "f6": 12_345_000_000,
                            "f8": 3.21,
                            "f62": 900_000_000,
                            "f72": 240_000_000,
                            "f104": 22,
                            "f105": 8,
                            "f106": 2,
                            "f128": "领涨样本",
                            "f136": 6.78,
                            "f124": int(datetime(2026, 7, 21, 10, 30).timestamp()),
                        }
                    ],
                }
            }

    monkeypatch.setattr(
        "app.services.market_data.requests.get",
        lambda *args, **kwargs: Response(),
    )

    rows = provider._fetch_direct_eastmoney_sector_flow_raw("行业资金流", "今日")

    assert rows[0]["turnover_amount"] == 123.45
    assert rows[0]["turnover_rate"] == 3.21
    assert rows[0]["leader_change_pct"] == 6.78
