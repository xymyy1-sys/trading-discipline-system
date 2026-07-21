from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import sector_margin


class _Response:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_daily_history_uses_exact_report_filter_and_reads_every_page(monkeypatch):
    calls: list[dict] = []

    def fake_get(_url, *, params, headers, timeout):
        calls.append(dict(params))
        return _Response({
            "result": {
                "pages": 2,
                "data": [{
                    "BOARD_CODE": "BK0001",
                    "TRADE_DATE": f"2026-07-{21 - int(params['pageNumber']):02d}",
                    "FIN_NETBUY_AMT": 1,
                }],
            },
        })

    monkeypatch.setattr(sector_margin.requests, "get", fake_get)
    rows = sector_margin._fetch_daily_history(
        ["BK0001", "BK0002"],
        cutoff=date(2026, 1, 1),
    )

    assert len(rows) == 2
    assert rows[0]["BOARD_CODE"] == "BK0001"
    assert calls[0]["reportName"] == "RPTA_WEB_BKJYMX"
    assert calls[0]["pageNumber"] == 1
    assert calls[1]["pageNumber"] == 2
    assert calls[0]["pageSize"] == 500
    assert "BOARD_CODE in (\"BK0001\",\"BK0002\")" in calls[0]["filter"]
    assert "TRADE_DATE>='2026-01-01'" in calls[0]["filter"]

    monkeypatch.setattr(
        sector_margin.requests,
        "get",
        lambda *_args, **_kwargs: _Response({"result": {"pages": 41, "data": [{}]}}),
    )
    with pytest.raises(ValueError, match="安全上限"):
        sector_margin._fetch_daily_history(["BK0001"], cutoff=date(2026, 1, 1))


def test_summary_report_refuses_to_return_an_incomplete_page_set(monkeypatch):
    calls = 0

    def fake_get(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _Response({"result": {"pages": 21, "data": [{"row": calls}]}})

    monkeypatch.setattr(sector_margin.requests, "get", fake_get)
    with pytest.raises(ValueError, match="安全分页上限"):
        sector_margin._fetch_report("RPTA_WEB_BKQJYMXN")
    assert calls == 20


def test_candidate_history_is_batched_and_sorted_by_trade_date(monkeypatch):
    monkeypatch.setattr(
        sector_margin,
        "_history_candidate_codes",
        lambda _rows, _code: ["BK1", "BK2", "BK3", "BK4"],
    )
    batches: list[list[str]] = []

    def fake_history(codes, *, cutoff):
        batches.append(list(codes))
        assert cutoff <= date.today()
        return [
            {"BOARD_CODE": code, "TRADE_DATE": "2026-07-20"}
            for code in reversed(codes)
        ] + [
            {"BOARD_CODE": code, "TRADE_DATE": "2026-07-18"}
            for code in codes
        ]

    monkeypatch.setattr(sector_margin, "_fetch_daily_history", fake_history)
    grouped, error = sector_margin._load_candidate_history([], board_code="005")

    assert error is None
    assert batches == [["BK1", "BK2", "BK3", "BK4"]]
    assert [row["TRADE_DATE"] for row in grouped["BK1"]] == [
        "2026-07-18",
        "2026-07-20",
    ]


def test_history_metrics_use_true_daily_values_for_slopes_and_percentiles(monkeypatch):
    monkeypatch.setattr(sector_margin, "is_a_share_trading_day", lambda _value: True)
    start = date(2026, 1, 1)
    rows = [
        {
            "TRADE_DATE": (start + timedelta(days=index)).isoformat(),
            "FIN_NETBUY_AMT": index * 1e8,
            "FIN_BALANCE_RATIO": float(index),
        }
        for index in range(120)
    ]

    metrics = sector_margin._history_metrics(rows)

    assert metrics["financing_net_buy_slope_5d"] == 1.0
    assert metrics["financing_net_buy_slope_10d"] == 1.0
    assert metrics["financing_net_buy_slope_20d"] == 1.0
    assert metrics["financing_balance_ratio_percentile_60d"] == 100.0
    assert metrics["financing_balance_ratio_percentile_120d"] == 100.0
    assert metrics["margin_history_sample_count"] == 120


def test_board_with_missing_history_keeps_fields_null_without_fake_percentile(monkeypatch):
    daily_rows = [
        {
            "BOARD_TYPE_CODE": "005",
            "BOARD_CODE": "BK-A",
            "BOARD_NAME": "风险候选",
            "TRADE_DATE": "2026-07-20",
            "FIN_BALANCE": 100e8,
            "FIN_BUY_AMT": 3e8,
            "FIN_REPAY_AMT": 2e8,
            "FIN_NETBUY_AMT": 1e8,
            "FIN_BALANCE_RATIO": 8.0,
        },
        {
            "BOARD_TYPE_CODE": "005",
            "BOARD_CODE": "BK-B",
            "BOARD_NAME": "未覆盖板块",
            "TRADE_DATE": "2026-07-20",
            "FIN_BALANCE": 20e8,
            "FIN_BUY_AMT": 1e8,
            "FIN_REPAY_AMT": 1e8,
            "FIN_NETBUY_AMT": 0,
            "FIN_BALANCE_RATIO": 1.0,
        },
    ]

    monkeypatch.setattr(sector_margin, "_get_response_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sector_margin, "_set_response_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sector_margin,
        "_fetch_report",
        lambda report_name: daily_rows if report_name == "RPTA_WEB_BKJYMXN" else [],
    )
    monkeypatch.setattr(
        sector_margin,
        "_load_candidate_history",
        lambda _rows, *, board_code: ({
            "BK-A": [
                {
                    "FIN_NETBUY_AMT": index * 1e8,
                    "FIN_BALANCE_RATIO": float(index),
                }
                for index in range(120)
            ],
        }, None),
    )

    payload = sector_margin.fetch_sector_margin("行业", force_refresh=True)
    missing = payload["items"]["未覆盖板块"]

    assert missing["financing_net_buy_slope_5d"] is None
    assert missing["financing_balance_ratio_percentile_60d"] is None
    assert missing["financing_balance_ratio_percentile_120d"] is None
    assert missing["margin_history_sample_count"] == 0
    assert "不输出伪历史分位" in missing["margin_history_method"]
    assert any("单板块历史缺失" in note for note in payload["notes"])


def test_history_universe_includes_every_disclosed_board():
    rows = [
        {"BOARD_TYPE_CODE": "005", "BOARD_CODE": "BK-A"},
        {"BOARD_TYPE_CODE": "005", "BOARD_CODE": "BK-B"},
        {"BOARD_TYPE_CODE": "006", "BOARD_CODE": "BK-C"},
    ]

    assert sector_margin._history_candidate_codes(rows, "005") == ["BK-A", "BK-B"]


def test_refresh_failure_returns_last_successful_margin_snapshot(monkeypatch):
    cached = {
        "source": "东方财富融资融券板块榜",
        "updated_at": "2026-07-20T16:00:00",
        "as_of": "2026-07-20",
        "realtime": False,
        "items": {"半导体": {"financing_net_buy": 1.0}},
        "notes": [],
    }
    monkeypatch.setattr(
        sector_margin,
        "_get_response_cache",
        lambda *_args, **_kwargs: cached,
    )
    monkeypatch.setattr(
        sector_margin,
        "_fetch_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()),
    )

    payload = sector_margin.fetch_sector_margin("行业", force_refresh=True)

    assert payload["items"] == cached["items"]
    assert "上次成功缓存" in payload["source"]
    assert any("本轮板块融资采集失败" in note for note in payload["notes"])


def test_history_metrics_degrade_only_the_windows_crossing_a_missing_day(monkeypatch):
    start = date(2026, 7, 1)
    monkeypatch.setattr(sector_margin, "is_a_share_trading_day", lambda _value: True)
    rows = [
        {
            "TRADE_DATE": (start + timedelta(days=index)).isoformat(),
            "FIN_NETBUY_AMT": index * 1e8,
            "FIN_BALANCE_RATIO": float(index),
        }
        for index in range(65)
        if index != 40
    ]

    metrics = sector_margin._history_metrics(rows)

    assert metrics["financing_net_buy_slope_5d"] == 1.0
    assert metrics["financing_net_buy_slope_10d"] == 1.0
    assert metrics["financing_net_buy_slope_20d"] == 1.0
    assert metrics["financing_balance_ratio_percentile_60d"] is None
    assert metrics["financing_balance_ratio_percentile_120d"] is None
    assert metrics["margin_history_degraded"] is True
    assert "60日分位" in metrics["margin_history_method"]


def test_old_history_gap_does_not_erase_complete_latest_windows(monkeypatch):
    start = date(2026, 1, 1)
    monkeypatch.setattr(sector_margin, "is_a_share_trading_day", lambda _value: True)
    rows = [
        {
            "TRADE_DATE": (start + timedelta(days=index)).isoformat(),
            "FIN_NETBUY_AMT": index * 1e8,
            "FIN_BALANCE_RATIO": float(index),
        }
        for index in range(150)
        if index != 5
    ]

    metrics = sector_margin._history_metrics(rows)

    assert metrics["financing_net_buy_slope_5d"] == 1.0
    assert metrics["financing_net_buy_slope_10d"] == 1.0
    assert metrics["financing_net_buy_slope_20d"] == 1.0
    assert metrics["financing_balance_ratio_percentile_60d"] == 100.0
    assert metrics["financing_balance_ratio_percentile_120d"] == 100.0
    assert metrics["margin_history_degraded"] is False
