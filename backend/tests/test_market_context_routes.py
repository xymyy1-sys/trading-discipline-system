from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.trading import MarketRegimeSnapshot


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
    assert any("同交易日市场环境快照" in note for note in response.json()["notes"])


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
