from app.services.market_data import MarketDataProvider, SectorFlowPoint, _sanitize_flow_timeline


def test_sector_flow_outflow_is_negative_and_snapshot_only(monkeypatch):
    provider = MarketDataProvider()

    def fake_direct(*args, **kwargs):
        return [
            {
                "name": "半导体",
                "provider": "eastmoney",
                "net_inflow": -248.78,
                "main_inflow": -248.79,
                "change_pct": -1.5,
                "strength": 38,
                "leaders": [],
            },
            {
                "name": "IT服务",
                "provider": "eastmoney",
                "net_inflow": 52.3,
                "main_inflow": 44.1,
                "change_pct": 2.1,
                "strength": 72,
                "leaders": [],
            },
        ]

    monkeypatch.setattr(provider, "_fetch_direct_eastmoney_sector_flow_raw", fake_direct)
    monkeypatch.setattr("app.services.market_data._is_trading_time", lambda: False)
    monkeypatch.setattr("app.services.market_data._get_snapshots", lambda _flow_type: [])

    flow = provider.sector_flow(flow_type="行业资金流", period="今日", force_refresh=True)

    assert [item.name for item in flow.inflow] == ["IT服务"]
    assert [item.name for item in flow.outflow] == ["半导体"]
    assert flow.outflow[0].timeline == [SectorFlowPoint(time="当前", value=-248.78)]
    assert flow.outflow[0].timeline_reliable is False
    assert flow.outflow[0].flow_peak is None
    assert flow.outflow[0].flow_event is None


def test_sector_flow_derives_peak_pullback_rank_and_reversal_from_real_curve(monkeypatch):
    provider = MarketDataProvider()
    monkeypatch.setattr(provider, "_fetch_direct_eastmoney_sector_flow_raw", lambda **_kwargs: [{
        "name": "半导体", "board_code": "BK1036", "provider": "eastmoney",
        "net_inflow": 20.0, "main_inflow": 18.0, "change_pct": 1.0,
        "strength": 70, "leaders": [],
    }])
    monkeypatch.setattr(provider, "_fetch_eastmoney_board_intraday_flow", lambda _code: [
        SectorFlowPoint(time="10:00", value=30.0),
        SectorFlowPoint(time="11:00", value=100.0),
        SectorFlowPoint(time="14:00", value=35.0),
    ])
    monkeypatch.setattr(provider, "_fetch_eastmoney_board_intraday_index", lambda _code: [
        {"time": "10:00", "price": 1200.0, "vwap": 1198.0},
        {"time": "11:00", "price": 1197.0, "vwap": 1199.0},
        {"time": "14:00", "price": 1195.0, "vwap": 1198.5},
    ])
    monkeypatch.setattr("app.services.market_data._is_trading_time", lambda: False)
    monkeypatch.setattr("app.services.market_data._get_snapshots", lambda _flow_type: [{
        "time": "14:00", "items": [
            {"name": "人工智能", "net_inflow": 50},
            {"name": "半导体", "net_inflow": 40},
        ],
    }])

    item = provider.sector_flow(flow_type="行业资金流", period="今日", force_refresh=True).inflow[0]

    assert item.rank == 1
    assert item.rank_change == 1
    assert item.timeline_reliable is True
    assert item.flow_peak == 100.0
    assert item.flow_peak_time == "11:00"
    assert item.flow_pullback == -80.0
    assert item.flow_pullback_pct == -80.0
    assert item.flow_event == "FLOW_PEAK_REVERSAL"
    assert item.sector_vwap_reliable is True
    assert item.sector_below_vwap is True
    assert item.sector_price == 1195.0
    assert item.sector_vwap == 1198.5


def test_eastmoney_board_index_parses_provider_average_price_as_vwap(monkeypatch):
    provider = MarketDataProvider()

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"trends": [
                "2026-07-10 09:30,1000,1002,1003,999,100,1000000,1001.5",
                "2026-07-10 09:31,1002,999,1002,998,120,1200000,1000.4",
                "2026-07-10 09:32,999,998,1000,997,130,1300000,999.6",
            ]}}

    monkeypatch.setattr("app.services.market_data.requests.get", lambda *_args, **_kwargs: FakeResponse())

    points = provider._fetch_eastmoney_board_intraday_index("BK1036")

    assert points[-1] == {"time": "09:32", "price": 998.0, "vwap": 999.6}


def test_sanitize_flow_timeline_last_point_matches_current_net_flow():
    points = [
        SectorFlowPoint(time="10:00", value=28.0),
        SectorFlowPoint(time="11:00", value=12.0),
    ]

    sanitized = _sanitize_flow_timeline(points, -8.5)

    assert sanitized[-1] == SectorFlowPoint(time="当前", value=-8.5)


def test_eastmoney_sector_flow_keeps_order_size_breakdown(monkeypatch):
    provider = MarketDataProvider()

    def fake_direct(*args, **kwargs):
        return [
            {
                "name": "互联网服务",
                "provider": "eastmoney",
                "net_inflow": 21.4,
                "main_inflow": 29.9,
                "change_pct": 1.2,
                "strength": 76,
                "leaders": [],
                "flow_breakdown": [
                    {"name": "超大单", "net": 29.88, "ratio": 6.9},
                    {"name": "大单", "net": -8.49, "ratio": -1.96},
                    {"name": "中单", "net": -20.0, "ratio": -4.61},
                    {"name": "小单", "net": -1.36, "ratio": -0.31},
                ],
            },
        ]

    monkeypatch.setattr(provider, "_fetch_direct_eastmoney_sector_flow_raw", fake_direct)
    monkeypatch.setattr("app.services.market_data._is_trading_time", lambda: False)
    monkeypatch.setattr("app.services.market_data._get_snapshots", lambda _flow_type: [])

    flow = provider.sector_flow(flow_type="行业资金流", period="今日", force_refresh=True)

    assert flow.inflow[0].flow_breakdown[0].name == "超大单"
    assert flow.inflow[0].flow_breakdown[0].net == 29.88
    assert flow.inflow[0].flow_breakdown[1].net == -8.49


def test_eastmoney_sector_flow_fetches_all_pages(monkeypatch):
    provider = MarketDataProvider()

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(_url, params=None, **_kwargs):
        page = int((params or {}).get("pn") or 1)
        if page == 1:
            rows = [
                {
                    "f12": f"BK{i:04d}",
                    "f14": f"板块{i}",
                    "f3": 1,
                    "f62": 1_000_000_000,
                    "f66": 600_000_000,
                    "f72": 400_000_000,
                    "f78": -500_000_000,
                    "f84": -500_000_000,
                    "f204": "领涨股",
                }
                for i in range(100)
            ]
        else:
            rows = [
                {
                    "f12": "BK1036",
                    "f14": "半导体",
                    "f3": -5.38,
                    "f62": -24_878_000_000,
                    "f66": -12_000_000_000,
                    "f72": -12_878_000_000,
                    "f78": 10_000_000_000,
                    "f84": 14_878_000_000,
                    "f204": "N托伦斯",
                }
            ]
        return FakeResponse({"data": {"total": 101, "diff": rows}})

    monkeypatch.setattr("app.services.market_data.requests.get", fake_get)

    rows = provider._fetch_direct_eastmoney_sector_flow_raw("行业资金流", "今日")

    assert len(rows) == 101
    assert rows[-1]["name"] == "半导体"
    assert rows[-1]["net_inflow"] == -248.78


def test_dark_trade_maps_eastmoney_fields_to_yi(monkeypatch):
    provider = MarketDataProvider()

    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_dark_trade_raw",
        lambda scope, date_text: ([
            {
                "3": 90,
                "4": "BK1216",
                "6": 9497014878,
                "7": 4079387618,
                "8": 13576402496,
                "9": 461,
                "10": 46,
                "11": 0.06227,
                "12": 0.90927,
                "13": 4380399,
                "14": 0.02821,
                "15": "哈药股份",
                "16": "医药生物",
                "20": "600664",
                "21": 1,
            }
        ], date_text),
    )

    result = provider.dark_trade(scope="行业", trade_date="20260710", force_refresh=True)
    item = result.items[0]

    assert result.source == "eastmoney-darktrade"
    assert item.name == "医药生物"
    assert item.dark_amount == 94.97
    assert item.lit_amount == 40.79
    assert item.main_net_inflow_with_dark == 135.76
    assert item.dark_activity == 6.23
    assert item.inflow_stock_ratio == 90.93
    assert item.leading_stock == "哈药股份"


def test_hot_themes_uses_hot_market_rows_and_flow_lookup(monkeypatch):
    provider = MarketDataProvider()

    monkeypatch.setattr(
        provider,
        "_fetch_eastmoney_hot_market_raw",
        lambda: [
            {
                "period": "今日",
                "rank": 1,
                "name": "创新药",
                "board_code": "BK1106",
                "change_pct": 3.24,
            }
        ],
    )
    monkeypatch.setattr(
        provider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        lambda flow_type, period: [
            {
                "name": "创新药",
                "board_code": "BK1106",
                "net_inflow": 57.49,
                "main_inflow": 42.3,
                "leaders": ["立方制药"],
            }
        ],
    )

    result = provider.hot_themes(force_refresh=True)
    item = result.items[0]

    assert item.name == "创新药"
    assert item.period == "今日"
    assert item.net_inflow == 57.49
    assert item.main_inflow == 42.3
    assert item.leaders == ["立方制药"]
