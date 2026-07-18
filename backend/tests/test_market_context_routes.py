from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models.trading import MarketRegimeSnapshot


def test_sector_temperature_route_keeps_margin_as_t_plus_one(monkeypatch):
    from app.api.routes import market

    def panel(_board_type, period, _force):
        change = {"今日": -1.2, "5日": 8.0, "10日": 15.0}[period]
        net = {"今日": -12.0, "5日": 50.0, "10日": 90.0}[period]
        item = SimpleNamespace(
            name="测试行业",
            display_name="测试行业",
            raw_name="测试行业",
            board_code="BK0001",
            change_pct=change,
            net_inflow=net,
            flow_speed=-0.8 if period == "今日" else None,
            flow_acceleration=-0.1 if period == "今日" else None,
            flow_turning="OUTFLOW_ACCELERATING" if period == "今日" else None,
        )
        return SimpleNamespace(
            source="eastmoney+cached",
            updated_at=datetime(2026, 7, 17, 10, 30),
            inflow=[item] if net > 0 else [],
            outflow=[item] if net < 0 else [],
        )

    monkeypatch.setattr(market.market_provider, "board_flow_panel", panel)
    monkeypatch.setattr(market.market_provider, "hot_themes", lambda _force: SimpleNamespace(items=[]))
    def cached_flow(key):
        period = key.split("|")[-1]
        change = {"今日": -1.2, "5日": 8.0, "10日": 15.0}[period]
        net = {"今日": -12.0, "5日": 50.0, "10日": 90.0}[period]
        return ([{
            "name": "测试行业",
            "board_code": "BK0001",
            "change_pct": change,
            "net_inflow": net,
            "provider_trade_date": "2026-07-17",
            "provider_updated_at": "2026-07-17T10:29:00+08:00",
        }], "eastmoney", "2026-07-17")

    monkeypatch.setattr(market, "_get_cached_flow", cached_flow)
    monkeypatch.setattr(market, "fetch_sector_margin", lambda _board_type, _force: {
        "items": {
            "测试行业": {
                "as_of": "2026-07-16",
                "financing_balance": 120.0,
                "financing_net_buy": 4.0,
                "financing_balance_ratio": 3.5,
                "net_buy_5d": 12.0,
                "net_buy_10d": 20.0,
            }
        },
        "notes": ["T+1测试口径"],
    })

    result = market.sector_temperature.__wrapped__(request=None, board_type="行业", force_refresh=True)

    assert result.items[0].name == "测试行业"
    assert result.items[0].margin_realtime is False
    assert result.items[0].margin_as_of == "2026-07-16"
    assert result.items[0].flow_speed == -0.8
    assert result.items[0].flow_turning == "OUTFLOW_ACCELERATING"
    assert "东方财富两融T+1" in result.source
    assert "板块订单流算法缓存" in result.source
    assert result.updated_at.isoformat() == "2026-07-17T10:29:00+08:00"
    assert any("使用 eastmoney 缓存" in note and "10:29:00" in note for note in result.notes)


def test_capital_rotation_uses_shanghai_generated_at(db_session, monkeypatch):
    from types import SimpleNamespace
    from app.api.routes import market

    expected = datetime(2026, 7, 16, 10, 30)
    monkeypatch.setattr(market, "shanghai_now_naive", lambda: expected)
    monkeypatch.setattr(
        market,
        "_market_seesaw_monitor",
        lambda holdings, force_refresh=False: SimpleNamespace(holding_alerts=[]),
    )

    result = market.capital_rotation.__wrapped__(request=None, db=db_session)

    assert result.generated_at == expected


def test_global_cues_route_preserves_unavailable_quotes(client, monkeypatch):
    from app.api.routes import market

    now = datetime.now().isoformat()
    payload = {
        "generated_at": now,
        "as_of": now,
        "quality": "degraded",
        "data_quality": "degraded",
        "sources": ["测试真实源"],
        "source": ["测试真实源"],
        "notes": ["韩国个股授权行情不可用。"],
        "kis": {"configured": False},
        "korea_indices": [],
        "korea_equities": [{
            "symbol": "005930",
            "name": "三星电子",
            "market": "韩国",
            "status": "unavailable",
            "source": "KIS Open API未配置",
            "note": "禁止用指数推算个股价格。",
        }],
        "us_indices": [],
        "us_sector_rank": [],
        "items": [{
            "group": "korea_equity",
            "symbol": "005930",
            "name": "三星电子",
            "market": "韩国",
            "status": "unavailable",
            "source": "KIS Open API未配置",
            "note": "禁止用指数推算个股价格。",
        }],
    }
    monkeypatch.setattr(market.global_market_service, "snapshot", lambda **_kwargs: payload)

    response = client.get("/api/market/global-cues")

    assert response.status_code == 200
    data = response.json()
    assert data["data_quality"] == "degraded"
    assert data["korea_equities"][0]["status"] == "unavailable"
    assert data["korea_equities"][0]["price"] is None


def test_opportunity_radar_route_never_turns_news_into_buy_signal(client, monkeypatch):
    from app.api.routes import checks

    now = datetime.now().isoformat()
    monkeypatch.setattr(checks.market_provider, "information_differential", lambda **_kwargs: {"items": []})
    monkeypatch.setattr(checks.market_provider, "sector_flow", lambda **_kwargs: {"inflow": [], "outflow": []})
    monkeypatch.setattr(checks.market_provider, "sector_opening_breadth", lambda **_kwargs: {
        "trade_date": datetime.now().date().isoformat(), "data_quality": "missing", "sample_count": 0,
    })
    monkeypatch.setattr(checks.market_provider, "limit_up_ladder", lambda *_args, **_kwargs: {"trade_date": datetime.now().date().isoformat(), "groups": []})
    monkeypatch.setattr(checks.sector_expansion_service, "assess", lambda *_args, **_kwargs: {
        "updated_at": now,
        "as_of": now,
        "window_minutes": 15,
        "data_quality": "missing",
        "source": [],
        "items": [],
        "counts": {"增量已确认": 0, "增量待确认": 0},
        "notes": ["无增量证据"],
    })
    monkeypatch.setattr(checks.opportunity_radar_service, "assess", lambda *_args, **_kwargs: {
        "updated_at": now,
        "as_of": now,
        "source": ["测试资讯源", "测试资金源"],
        "data_quality": "ok",
        "items": [{
            "id": "news-1",
            "title": "商业航天突发消息",
            "source": "测试资讯源",
            "published_at": now,
            "sectors": ["商业航天"],
            "related_stocks": [],
            "status": "已确认",
            "confirmation_score": 85,
            "primary_sector": "商业航天",
            "evidence": ["板块资金、价格和VWAP共同确认"],
            "counter_evidence": [],
            "missing": [],
            "sector_assessments": [],
            "action": "加入机会观察池，等待个股量价买点。",
            "trade_constraint": "资讯不得单独触发买入。",
            "buy_signal": False,
        }],
        "counts": {"已确认": 1},
        "discipline": "新闻只生成观察假设。",
        "notes": [],
        "available_sector_evidence": 1,
    })

    response = client.get("/api/intel/opportunity-radar")

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["status"] == "已确认"
    assert item["buy_signal"] is False
    assert "不得单独触发买入" in item["trade_constraint"]
    assert response.json()["data_quality"] == "degraded"
    assert response.json()["intraday_expansion"]["items"] == []
    assert response.json()["intraday_expansion"]["counts"]["增量已确认"] == 0
    assert response.json()["consensus_high_open_fade"]["status"] == "DATA_GAP"
    assert any("同交易日市场环境快照" in note for note in response.json()["notes"])

    persisted: list[object] = []
    monkeypatch.setattr(
        checks,
        "persist_unified_market_events",
        lambda *_args, **_kwargs: persisted.append(object()),
    )
    historical = client.get("/api/intel/opportunity-radar?date=2000-01-04")
    assert historical.status_code == 200
    assert persisted == []
    assert "历史日期雷达仅供回看，不写入今日盘中事件流。" in historical.json()["notes"]


def test_opportunity_radar_route_serializes_confirmed_intraday_expansion(client, monkeypatch):
    from app.api.routes import checks

    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    monkeypatch.setattr(checks.market_provider, "information_differential", lambda **_kwargs: {"items": []})
    monkeypatch.setattr(checks.market_provider, "sector_flow", lambda **_kwargs: {"inflow": [], "outflow": []})
    monkeypatch.setattr(checks.market_provider, "sector_opening_breadth", lambda **_kwargs: {
        "trade_date": datetime.now().date().isoformat(), "data_quality": "missing", "sample_count": 0,
    })
    monkeypatch.setattr(
        checks.market_provider,
        "limit_up_ladder",
        lambda *_args, **_kwargs: {"source": "东方财富涨停池", "trade_date": now[:10], "groups": []},
    )
    monkeypatch.setattr(checks.opportunity_radar_service, "assess", lambda *_args, **_kwargs: {
        "updated_at": now,
        "as_of": now,
        "source": [],
        "data_quality": "missing",
        "items": [],
        "counts": {},
        "discipline": "资讯不得单独触发买入。",
        "notes": [],
        "available_sector_evidence": 0,
    })
    monkeypatch.setattr(checks.sector_expansion_service, "assess", lambda *_args, **_kwargs: {
        "updated_at": now,
        "as_of": now,
        "window_minutes": 15,
        "data_quality": "ok",
        "source": ["东方财富涨停池", "eastmoney"],
        "items": [{
            "sector": "半导体",
            "status": "增量已确认",
            "confirmation_score": 90,
            "window_minutes": 15,
            "total_limit_up_count": 5,
            "new_limit_up_count": 3,
            "highest_board": 2,
            "change_pct": 2.6,
            "net_inflow": 18.5,
            "flow_speed": 2.4,
            "flow_acceleration": 0.35,
            "flow_turning": "TURN_TO_INFLOW",
            "leaders": ["芯片一号", "芯片二号"],
            "evidence": ["最近15个交易分钟新增3只涨停。"],
            "counter_evidence": [],
            "missing": [],
            "risk": ["禁止追后排。"],
            "action": "仅加入观察，等待回踩确认，禁止追后排。",
            "invalidation": ["资金重新拐为流出。"],
            "source": ["eastmoney"],
            "as_of": now,
            "buy_signal": False,
        }],
        "counts": {"增量已确认": 1, "增量待确认": 0},
        "notes": ["只生成观察结论。"],
    })

    response = client.get("/api/intel/opportunity-radar")

    assert response.status_code == 200
    expansion = response.json()["intraday_expansion"]
    assert expansion["items"][0]["status"] == "增量已确认"
    assert expansion["items"][0]["buy_signal"] is False
    assert expansion["items"][0]["new_limit_up_count"] == 3
    assert expansion["source"] == ["东方财富涨停池", "eastmoney"]


def test_opportunity_radar_route_degrades_cleanly_when_ladder_fails(client, monkeypatch):
    from app.api.routes import checks

    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    monkeypatch.setattr(checks.market_provider, "information_differential", lambda **_kwargs: {"items": []})
    monkeypatch.setattr(checks.market_provider, "sector_flow", lambda **_kwargs: {"inflow": [], "outflow": []})
    monkeypatch.setattr(checks.market_provider, "sector_opening_breadth", lambda **_kwargs: {
        "trade_date": datetime.now().date().isoformat(), "data_quality": "missing", "sample_count": 0,
    })
    monkeypatch.setattr(
        checks.market_provider,
        "limit_up_ladder",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )
    monkeypatch.setattr(checks.opportunity_radar_service, "assess", lambda *_args, **_kwargs: {
        "updated_at": now,
        "as_of": now,
        "source": [],
        "data_quality": "missing",
        "items": [],
        "counts": {},
        "discipline": "资讯不得单独触发买入。",
        "notes": [],
        "available_sector_evidence": 0,
    })

    response = client.get("/api/intel/opportunity-radar")

    assert response.status_code == 200
    expansion = response.json()["intraday_expansion"]
    assert expansion["data_quality"] == "missing"
    assert expansion["items"] == []
    assert expansion["window_minutes"] == 0
    assert "暂不可用" in expansion["notes"][0]


def test_opportunity_radar_rejects_cross_day_and_stale_market_snapshots(db_session):
    from app.api.routes import checks

    now = datetime(2026, 7, 14, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    db_session.add_all([
        MarketRegimeSnapshot(
            trade_date="2026-07-13",
            captured_at=now.replace(tzinfo=None) - timedelta(minutes=1),
            index_composite_change_pct=1.2,
        ),
        MarketRegimeSnapshot(
            trade_date="2026-07-14",
            captured_at=now.replace(tzinfo=None) - timedelta(minutes=16),
            index_composite_change_pct=-2.0,
        ),
    ])
    db_session.commit()

    snapshot, note = checks._fresh_market_snapshot(
        db_session,
        trade_date="2026-07-14",
        now=now,
    )

    assert snapshot is None
    assert note is not None and "已过期" in note


def test_opportunity_radar_accepts_a_fresh_same_day_market_snapshot(db_session):
    from app.api.routes import checks

    now = datetime(2026, 7, 14, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
    expected = MarketRegimeSnapshot(
        trade_date="2026-07-14",
        captured_at=now.replace(tzinfo=None) - timedelta(minutes=10),
        index_composite_change_pct=-0.8,
    )
    db_session.add(expected)
    db_session.commit()

    snapshot, note = checks._fresh_market_snapshot(
        db_session,
        trade_date="2026-07-14",
        now=now,
    )

    assert snapshot is not None and snapshot.id == expected.id
    assert note is None
