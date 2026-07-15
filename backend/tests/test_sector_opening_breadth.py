from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.market_data import MarketDataProvider


def _real_rows(trade_date: str, count: int = 20) -> list[dict]:
    rows = []
    for index in range(count):
        high_open = index < 12
        rows.append({
            "name": f"行业{index}",
            "provider_trade_date": trade_date,
            "provider_updated_at": f"{trade_date}T09:31:00+08:00",
            "open_price": 101.0 if high_open else 100.1,
            "prev_close": 100.0,
        })
    return rows


def test_sector_opening_breadth_uses_same_day_provider_open_prices(monkeypatch):
    provider = MarketDataProvider()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    monkeypatch.setattr(
        provider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        lambda **_kwargs: _real_rows(today),
    )

    result = provider.sector_opening_breadth(today, force_refresh=True)

    assert result["data_quality"] == "ok"
    assert result["sample_count"] == 20
    assert result["sector_high_open_count"] == 12
    assert result["sector_component_count"] == 20
    assert result["sector_open_breadth_ratio"] == 0.6


def test_sector_opening_breadth_rejects_missing_provider_trade_dates(monkeypatch):
    provider = MarketDataProvider()
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    rows = _real_rows(today)
    for row in rows:
        row["provider_trade_date"] = None
    monkeypatch.setattr(
        provider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        lambda **_kwargs: rows,
    )

    result = provider.sector_opening_breadth(today, force_refresh=True)

    assert result["data_quality"] == "missing"
    assert result["sample_count"] == 0
    assert result["sector_open_breadth_ratio"] is None


def test_sector_opening_breadth_does_not_reuse_current_data_for_history(monkeypatch):
    provider = MarketDataProvider()
    calls = []
    monkeypatch.setattr(
        provider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        lambda **_kwargs: calls.append(True),
    )

    result = provider.sector_opening_breadth("2000-01-01", force_refresh=True)

    assert result["data_quality"] == "missing"
    assert calls == []
    assert result["sector_open_breadth_ratio"] is None
