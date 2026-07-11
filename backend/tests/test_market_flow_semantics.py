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
