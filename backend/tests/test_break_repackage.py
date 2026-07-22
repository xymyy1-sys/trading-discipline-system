from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.schemas.trading import BreakRepackageOut
from app.services.market_data import (
    MarketDataProvider,
    _break_repackage_completed_trade_dates,
)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_DATES = [
    "2026-07-13",
    "2026-07-14",
    "2026-07-15",
    "2026-07-16",
    "2026-07-17",
]


def _pool_row(
    code: str,
    name: str | None = None,
    *,
    latest_price: float = 11,
    change_pct: float = 1,
) -> dict:
    return {
        "代码": code,
        "名称": name or f"测试{code}",
        "最新价": latest_price,
        "涨跌幅": change_pct,
    }


def _bar(
    trade_date: str,
    *,
    open_price: float,
    close: float,
    high: float,
    low: float,
    amount: float = 100_000_000,
    change_pct: float = 1,
) -> dict:
    return {
        "trade_date": trade_date,
        "open": open_price,
        "close": close,
        "high": high,
        "low": low,
        "volume": 1_000_000,
        "amount": amount,
        "change_pct": change_pct,
        "turnover_rate": 5,
    }


def _freeze_window(monkeypatch) -> None:
    from app.services import market_data

    monkeypatch.setattr(
        market_data,
        "_break_repackage_completed_trade_dates",
        lambda *_args, **_kwargs: list(_DATES),
    )
    monkeypatch.setattr(
        market_data,
        "_shanghai_now_naive",
        lambda: datetime(2026, 7, 20, 10, 0),
    )
    monkeypatch.setattr(market_data, "_get_response_cache", lambda *_args, **_kwargs: None)


def test_completed_window_never_uses_unfinished_current_session():
    intraday = datetime(2026, 7, 20, 10, 30, tzinfo=_SHANGHAI).replace(tzinfo=None)
    after_close = datetime(2026, 7, 20, 15, 10, tzinfo=_SHANGHAI).replace(tzinfo=None)

    assert _break_repackage_completed_trade_dates(intraday)[-1] == "2026-07-17"
    assert _break_repackage_completed_trade_dates(after_close)[-1] == "2026-07-20"


def test_break_repackage_uses_latest_anchor_and_excludes_broken_or_current_limit_up(
    monkeypatch,
):
    from app.services import market_data

    provider = MarketDataProvider()
    _freeze_window(monkeypatch)
    writes: list[object] = []
    monkeypatch.setattr(market_data, "_set_response_cache", lambda *_args: writes.append(_args))

    pools = {
        _DATES[0]: [
            _pool_row("600001", "量价确认"),
            _pool_row("600002", "已经破位", latest_price=22),
            _pool_row("600004", "当日再板"),
        ],
        # A second limit-up must replace 600001's older anchor.
        _DATES[1]: [_pool_row("600001", "量价确认", latest_price=10.8)],
        _DATES[2]: [],
        _DATES[3]: [_pool_row("600003", "承接候选", latest_price=8.8)],
        _DATES[4]: [_pool_row("600004", "当日再板")],
    }
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_limit_up_pool",
        lambda trade_date: pools[trade_date],
    )

    histories = {
        "600001": [
            _bar(_DATES[1], open_price=10, close=10.8, high=11, low=9.8),
            _bar(_DATES[2], open_price=10.4, close=10.3, high=10.5, low=10),
            _bar(_DATES[3], open_price=10.3, close=10.6, high=10.8, low=10.1, amount=90_000_000),
            _bar(_DATES[4], open_price=10.5, close=11, high=11.1, low=10.2, amount=120_000_000, change_pct=4),
        ],
        "600002": [
            _bar(_DATES[0], open_price=20, close=22, high=22, low=19.5),
            _bar(_DATES[1], open_price=20, close=20.2, high=20.5, low=19.99),
            _bar(_DATES[2], open_price=20.2, close=20.3, high=20.6, low=20.1),
            _bar(_DATES[3], open_price=20.3, close=20.4, high=20.7, low=20.2),
            _bar(_DATES[4], open_price=20.4, close=20.5, high=20.8, low=20.3),
        ],
        "600003": [
            _bar(_DATES[3], open_price=8, close=8.8, high=8.8, low=7.9),
            _bar(_DATES[4], open_price=8.2, close=8.4, high=8.6, low=8),
        ],
    }
    requested: list[str] = []

    def daily(code: str, _begin: str, _end: str):
        requested.append(code)
        return histories[code], "eastmoney-test"

    monkeypatch.setattr(provider, "_fetch_break_repackage_daily_bars", daily)

    result = provider.break_repackage(force_refresh=True)

    assert result.data_status == "ok"
    assert result.evaluation_date == _DATES[-1]
    assert result.candidate_count == 3
    assert result.history_checked_count == 3
    assert result.history_gap_count == 0
    assert set(requested) == {"600001", "600002", "600003"}
    assert [item.code for item in result.items] == ["600001", "600003"]

    confirmed = result.items[0]
    assert confirmed.limit_up_date == _DATES[1]
    assert confirmed.limit_up_open == 10
    assert confirmed.support_low == 10
    assert confirmed.state == "量价反包确认"
    assert confirmed.trigger_price == 10.8
    assert confirmed.daily_evidence[0].trade_date == _DATES[2]

    candidate = result.items[1]
    assert candidate.state == "承接候选"
    assert candidate.trigger_price == 8.8
    # A successful complete screen is cached under its dated key.
    assert len(writes) == 1
    assert "break-repackage|2026-07-17|v1|5" in str(writes[0][0])


def test_break_repackage_partial_history_is_explicit_and_cached_without_complete_snapshot(monkeypatch):
    from app.services import market_data

    provider = MarketDataProvider()
    _freeze_window(monkeypatch)
    writes: list[object] = []
    monkeypatch.setattr(
        market_data,
        "_set_response_cache_unless_data_status",
        lambda *_args: writes.append(_args) or True,
    )
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_limit_up_pool",
        lambda trade_date: (
            [_pool_row("600001"), _pool_row("600002")]
            if trade_date == _DATES[0]
            else []
        ),
    )

    valid = [
        _bar(_DATES[0], open_price=10, close=11, high=11, low=9.8),
        *[
            _bar(trade_date, open_price=10.2, close=10.4, high=10.5, low=10)
            for trade_date in _DATES[1:]
        ],
    ]

    def daily(code: str, _begin: str, _end: str):
        if code == "600002":
            raise ValueError("缺少评价日日线")
        return valid, "eastmoney-test"

    monkeypatch.setattr(provider, "_fetch_break_repackage_daily_bars", daily)

    result = provider.break_repackage(force_refresh=True)

    assert result.data_status == "partial"
    assert result.history_checked_count == 1
    assert result.history_gap_count == 1
    assert result.matched_count == 1
    assert any("1只候选" in note for note in result.notes)
    assert len(writes) == 1
    assert writes[0][-1] == "ok"


def test_partial_refresh_cannot_overwrite_new_complete_snapshot():
    from app.services import cache

    key = "test-break-repackage-atomic-cache"
    complete = BreakRepackageOut(
        source="complete",
        updated_at=datetime(2026, 7, 20, 10, 0),
        evaluation_date=_DATES[-1],
        data_status="ok",
        lookback_trade_dates=list(_DATES),
    )
    partial = complete.model_copy(update={"source": "partial", "data_status": "partial"})
    try:
        cache._set_response_cache(key, complete)
        written = cache._set_response_cache_unless_data_status(key, partial, "ok")

        assert written is False
        assert cache._get_response_cache(key, allow_stale=True).source == "complete"
    finally:
        with cache._CACHE_LOCK:
            cache._response_cache.pop(key, None)


def test_missing_consolidation_amount_cannot_upgrade_price_confirmation(monkeypatch):
    provider = MarketDataProvider()
    _freeze_window(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_limit_up_pool",
        lambda trade_date: [_pool_row("600001")] if trade_date == _DATES[0] else [],
    )
    bars = [
        _bar(_DATES[0], open_price=10, close=11, high=11, low=9.9),
        _bar(_DATES[1], open_price=10.4, close=10.5, high=10.7, low=10.1),
        _bar(_DATES[2], open_price=10.5, close=10.6, high=10.8, low=10.2, amount=0),
        _bar(_DATES[3], open_price=10.6, close=10.7, high=10.9, low=10.3),
        _bar(
            _DATES[4],
            open_price=11,
            close=12,
            high=12.1,
            low=10.8,
            amount=200_000_000,
            change_pct=8,
        ),
    ]
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_daily_bars",
        lambda *_args: (bars, "eastmoney-test"),
    )

    result = provider.break_repackage(force_refresh=True)

    assert result.data_status == "ok"
    assert result.items[0].state == "价格反包确认"
    assert result.items[0].amount_ratio is None


def test_break_repackage_pool_gap_and_all_history_gap_are_not_zero_matches(monkeypatch):
    provider = MarketDataProvider()
    _freeze_window(monkeypatch)

    def pool_gap(trade_date: str):
        if trade_date == _DATES[2]:
            raise ValueError("涨停池日期不一致")
        return []

    monkeypatch.setattr(provider, "_fetch_break_repackage_limit_up_pool", pool_gap)
    result = provider.break_repackage(force_refresh=True)
    assert result.data_status == "data_gap"
    assert result.matched_count == 0
    assert any("不能解释" in note for note in result.notes)

    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_limit_up_pool",
        lambda trade_date: [_pool_row("600001")] if trade_date == _DATES[0] else [],
    )
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_daily_bars",
        lambda *_args: (_ for _ in ()).throw(ValueError("日线缺失")),
    )
    result = provider.break_repackage(force_refresh=True)
    assert result.data_status == "data_gap"
    assert result.candidate_count == 1
    assert result.history_gap_count == 1
    assert any("不能解释" in note for note in result.notes)


def test_dated_limit_up_pool_accepts_only_provider_certified_zero(monkeypatch):
    from app.services import market_data

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"qdate": "20260717", "tc": 0, "pool": []}}

    monkeypatch.setattr(market_data.requests, "get", lambda *_args, **_kwargs: Response())

    provider = MarketDataProvider()
    assert provider._fetch_direct_limit_up_pool_raw("20260717", allow_empty=True) == []


def test_historical_pool_cross_checks_rows_when_qdate_is_latest_service_date(monkeypatch):
    from app.services import market_data

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "qdate": "20260722",
                    "tc": 1,
                    "pool": [{
                        "c": "002632",
                        "n": "道明光学",
                        "p": 9630,
                        "zdp": 10.057,
                    }],
                },
            }

    monkeypatch.setattr(market_data.requests, "get", lambda *_args, **_kwargs: Response())
    provider = MarketDataProvider()
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_daily_bars",
        lambda *_args: ([
            _bar(
                "2026-07-16",
                open_price=9.63,
                close=9.63,
                high=9.63,
                low=9.63,
                change_pct=10.06,
            ),
        ], "eastmoney-test"),
    )

    rows = provider._fetch_break_repackage_limit_up_pool("2026-07-16")
    assert rows[0]["代码"] == "002632"

    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_daily_bars",
        lambda *_args: ([
            _bar(
                "2026-07-16",
                open_price=8,
                close=8,
                high=8,
                low=8,
                change_pct=1,
            ),
        ], "eastmoney-test"),
    )
    with pytest.raises(ValueError, match="未复权日线不一致"):
        provider._fetch_break_repackage_limit_up_pool("2026-07-16")


def test_direct_pool_rejects_missing_date_or_malformed_rows(monkeypatch):
    from app.services import market_data

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    provider = MarketDataProvider()
    monkeypatch.setattr(
        market_data.requests,
        "get",
        lambda *_args, **_kwargs: Response({"data": {"tc": 0, "pool": []}}),
    )
    with pytest.raises(ValueError, match="no dated payload"):
        provider._fetch_direct_limit_up_pool_raw("20260717", allow_empty=True)

    monkeypatch.setattr(
        market_data.requests,
        "get",
        lambda *_args, **_kwargs: Response({
            "data": {"qdate": "20260717", "tc": 1, "pool": {}},
        }),
    )
    with pytest.raises(ValueError, match="malformed rows"):
        provider._fetch_direct_limit_up_pool_raw("20260717", allow_empty=True)

    monkeypatch.setattr(
        market_data.requests,
        "get",
        lambda *_args, **_kwargs: Response({
            "data": {"qdate": "20260722", "tc": 0, "pool": []},
        }),
    )
    with pytest.raises(ValueError, match="历史涨停池零结果"):
        provider._fetch_direct_limit_up_pool_raw(
            "20260717",
            allow_empty=True,
            require_query_date_match=False,
        )

    monkeypatch.setattr(
        market_data.requests,
        "get",
        lambda *_args, **_kwargs: Response({
            "data": {
                "qdate": "20260717",
                "tc": 1,
                "pool": [{"c": "600001", "n": "异常行", "p": None, "zdp": 10}],
            },
        }),
    )
    with pytest.raises(ValueError, match="有效价格或涨跌幅"):
        provider._fetch_direct_limit_up_pool_raw("20260717", allow_empty=True)


def test_invalid_ohlc_candle_is_a_data_gap_not_a_false_confirmation(monkeypatch):
    provider = MarketDataProvider()
    _freeze_window(monkeypatch)
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_limit_up_pool",
        lambda trade_date: [_pool_row("600001")] if trade_date == _DATES[0] else [],
    )
    bars = [
        _bar(_DATES[0], open_price=10, close=11, high=11, low=9.9),
        _bar(_DATES[1], open_price=10.4, close=10.5, high=10.7, low=10.1),
        _bar(_DATES[2], open_price=10.5, close=10.6, high=10.8, low=10.2),
        _bar(_DATES[3], open_price=10.6, close=10.7, high=10.9, low=10.3),
        # The close is above the reported high, so this candle must never
        # become a 100%+ close-position confirmation.
        _bar(
            _DATES[4],
            open_price=11,
            close=12,
            high=11.5,
            low=10.8,
            amount=200_000_000,
            change_pct=8,
        ),
    ]
    monkeypatch.setattr(
        provider,
        "_fetch_break_repackage_daily_bars",
        lambda *_args: (bars, "eastmoney-test"),
    )

    result = provider.break_repackage(force_refresh=True)

    assert result.data_status == "data_gap"
    assert result.items == []
    assert result.history_checked_count == 0


def test_break_repackage_daily_bar_maps_920_to_beijing_market(monkeypatch):
    from app.services import market_data

    seen_params: list[dict] = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "code": "920001",
                    "klines": ["2026-07-17,10,11,11,10,100,1000,10,10,0,5"],
                },
            }

    def request(*_args, **kwargs):
        seen_params.append(kwargs["params"])
        return Response()

    monkeypatch.setattr(market_data.requests, "get", request)
    bars, _ = MarketDataProvider()._fetch_break_repackage_daily_bars(
        "920001",
        "2026-07-17",
        "2026-07-17",
    )

    assert bars[0]["trade_date"] == "2026-07-17"
    assert seen_params[0]["secid"] == "0.920001"


def test_break_repackage_daily_bars_reject_duplicate_trade_dates(monkeypatch):
    from app.services import market_data

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            row = "2026-07-17,10,11,11,10,100,1000,10,10,0,5"
            return {"data": {"code": "600001", "klines": [row, row]}}

    monkeypatch.setattr(market_data.requests, "get", lambda *_args, **_kwargs: Response())

    with pytest.raises(ValueError, match="重复交易日期"):
        MarketDataProvider()._fetch_break_repackage_daily_bars(
            "600001",
            "2026-07-17",
            "2026-07-17",
        )


def test_break_repackage_get_is_cache_only_and_post_is_explicit(client, monkeypatch):
    from app.api.routes import market as market_routes

    monkeypatch.setattr(
        market_routes,
        "_break_repackage_completed_trade_dates",
        lambda *_args, **_kwargs: list(_DATES),
    )
    monkeypatch.setattr(market_routes, "_get_response_cache", lambda *_args, **_kwargs: None)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET不得联网调用提供者")

    monkeypatch.setattr(market_routes.market_provider, "break_repackage", forbidden)
    response = client.get("/api/market/break-repackage")
    assert response.status_code == 200
    assert response.json()["data_status"] == "data_gap"
    assert response.json()["evaluation_date"] == _DATES[-1]

    calls: list[bool] = []
    payload = BreakRepackageOut(
        source="eastmoney-test",
        updated_at=datetime(2026, 7, 20, 10, 0),
        evaluation_date=_DATES[-1],
        data_status="ok",
        lookback_trade_dates=list(_DATES),
        notes=["真实零候选"],
    )

    def refreshed(*, force_refresh: bool = False):
        calls.append(force_refresh)
        return payload

    monkeypatch.setattr(market_routes.market_provider, "break_repackage", refreshed)
    response = client.post("/api/market/break-repackage/refresh")
    assert response.status_code == 200
    assert response.json()["data_status"] == "ok"
    assert calls == [True]
