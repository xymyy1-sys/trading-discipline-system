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
