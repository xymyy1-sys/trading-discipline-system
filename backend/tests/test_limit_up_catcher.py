from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.schemas.trading import LimitUpCatcherCriteria, LimitUpCatcherOut
from app.services.market_data import MarketDataProvider


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_FRIDAY_INTRADAY = datetime(2026, 7, 17, 10, 30, tzinfo=_SHANGHAI)
_FRIDAY_CLOSE = datetime(2026, 7, 17, 15, 0, tzinfo=_SHANGHAI)
_DEFAULT_PROVIDER_TIMESTAMP = object()


def _timestamp(value: datetime) -> int:
    return int(value.timestamp())


def _freeze_market_clock(monkeypatch, value: datetime) -> None:
    from app.services import market_data

    local = value.astimezone(_SHANGHAI).replace(tzinfo=None)
    monkeypatch.setattr(market_data, "_shanghai_now_naive", lambda: local)


def _install_full_market_quotes(
    monkeypatch,
    *,
    timestamp_for_index,
) -> None:
    """Install one complete, stable 4,000-code provider payload."""

    from app.services import market_data

    rows = [
        _row(
            f"{index:06d}",
            volume_ratio=1,
            provider_timestamp=timestamp_for_index(index),
        )
        for index in range(4_000)
    ]

    def fake_get(_path, params, timeout=8):
        del timeout
        page = int(params.get("pn") or 1)
        return {
            "data": {
                "total": 4_000,
                "diff": rows if page == 1 else [],
            },
        }, "https://push2.eastmoney.com"

    monkeypatch.setattr(market_data, "_get_json_from_hosts", fake_get)


def _row(
    code: str,
    *,
    price: float = 10.5,
    change_pct: float = 4.5,
    volume_lots: float = 1_000,
    intraday_average: float = 10.2,
    turnover_rate: float = 5,
    volume_ratio: float = 4,
    provider_timestamp: int | None | object = _DEFAULT_PROVIDER_TIMESTAMP,
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
        "f124": (
            _timestamp(_FRIDAY_INTRADAY)
            if provider_timestamp is _DEFAULT_PROVIDER_TIMESTAMP
            else provider_timestamp
        ),
    }


def test_limit_up_catcher_applies_all_real_quote_conditions(monkeypatch):
    provider = MarketDataProvider()
    _freeze_market_clock(monkeypatch, _FRIDAY_INTRADAY)
    updated_at = _FRIDAY_INTRADAY.replace(tzinfo=None)
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
    assert "分页覆盖不完整" in result.notes[0]
    assert "ValueError" not in result.notes[0]


def test_limit_up_catcher_uses_stable_code_order_for_full_market_pagination(monkeypatch):
    """Live volume-ratio ordering must not move securities between pages."""

    from app.services import market_data

    provider = MarketDataProvider()
    _freeze_market_clock(monkeypatch, _FRIDAY_INTRADAY)
    page_size = 100
    total = 4_000
    seen_pages: list[int] = []

    def fake_get(_path, params, timeout=8):
        del timeout
        # f10 changes continuously during trading and previously caused rank
        # drift, duplicate rows and a false incomplete-pagination ValueError.
        assert params.get("fid") == "f12"
        page = int(params.get("pn") or 1)
        seen_pages.append(page)
        start = (page - 1) * page_size
        rows = [_row(f"{index:06d}") for index in range(start, min(start + page_size, total))]
        return {"data": {"total": total, "diff": rows}}, "https://push2.eastmoney.com"

    monkeypatch.setattr(market_data, "_get_json_from_hosts", fake_get)

    rows, source, _updated_at, total_scanned = provider._fetch_limit_up_catcher_rows()

    assert len(rows) == total
    assert total_scanned == total
    assert source == "eastmoney-all-a@push2.eastmoney.com"
    assert sorted(seen_pages) == list(range(1, 41))


@pytest.mark.parametrize("valid_timestamp_count", [0, 3_799])
def test_limit_up_catcher_rejects_missing_or_insufficient_f124_coverage_without_caching(
    monkeypatch,
    valid_timestamp_count,
):
    from app.services import market_data

    provider = MarketDataProvider()
    writes: list[object] = []
    _freeze_market_clock(monkeypatch, _FRIDAY_INTRADAY)
    valid_timestamp = _timestamp(_FRIDAY_INTRADAY)
    _install_full_market_quotes(
        monkeypatch,
        timestamp_for_index=lambda index: (
            valid_timestamp if index < valid_timestamp_count else None
        ),
    )
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "data_gap"
    assert result.items == []
    assert writes == []
    assert "时间戳覆盖不足" in result.notes[0]
    assert "f124" in result.notes[0]
    assert any("已有最后成功快照不被本次失败覆盖" in note for note in result.notes)


@pytest.mark.parametrize(
    ("now", "provider_time", "expected_trade_date"),
    [
        (
            datetime(2026, 7, 20, 10, 30, tzinfo=_SHANGHAI),
            _FRIDAY_CLOSE,
            "2026-07-20",
        ),
        (
            datetime(2026, 7, 19, 10, 30, tzinfo=_SHANGHAI),
            datetime(2026, 7, 19, 9, 30, tzinfo=_SHANGHAI),
            "2026-07-17",
        ),
    ],
)
def test_limit_up_catcher_rejects_stale_session_or_holiday_provider_date(
    monkeypatch,
    now,
    provider_time,
    expected_trade_date,
):
    from app.services import market_data

    provider = MarketDataProvider()
    writes: list[object] = []
    _freeze_market_clock(monkeypatch, now)
    provider_timestamp = _timestamp(provider_time)
    _install_full_market_quotes(
        monkeypatch,
        timestamp_for_index=lambda _index: provider_timestamp,
    )
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "data_gap"
    assert result.items == []
    assert writes == []
    assert "行情日期覆盖不足" in result.notes[0]
    assert expected_trade_date in result.notes[0]


@pytest.mark.parametrize(
    ("now", "provider_time", "trade_date"),
    [
        # A completed current-day snapshot remains valid after the close.
        (
            datetime(2026, 7, 20, 18, 0, tzinfo=_SHANGHAI),
            datetime(2026, 7, 20, 15, 0, tzinfo=_SHANGHAI),
            "2026-07-20",
        ),
        # On a closed day, the most recent completed trading day remains valid.
        (
            datetime(2026, 7, 19, 10, 30, tzinfo=_SHANGHAI),
            _FRIDAY_CLOSE,
            "2026-07-17",
        ),
        # Before Monday's auction, Friday is still the latest valid session.
        (
            datetime(2026, 7, 20, 8, 30, tzinfo=_SHANGHAI),
            _FRIDAY_CLOSE,
            "2026-07-17",
        ),
    ],
)
def test_limit_up_catcher_preserves_post_close_and_latest_trading_day_semantics(
    monkeypatch,
    now,
    provider_time,
    trade_date,
):
    from app.services import market_data

    provider = MarketDataProvider()
    writes: list[object] = []
    _freeze_market_clock(monkeypatch, now)
    provider_timestamp = _timestamp(provider_time)
    _install_full_market_quotes(
        monkeypatch,
        timestamp_for_index=lambda _index: provider_timestamp,
    )
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    result = provider.limit_up_catcher(force_refresh=True)

    assert result.data_status == "ok"
    assert result.trade_date == trade_date
    assert result.updated_at == provider_time.replace(tzinfo=None)
    assert result.items == []
    assert len(writes) == 1


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
