from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models.trading import DataCaptureSnapshot, MarketRegimeSnapshot


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

    result = market.refresh_sector_temperature.__wrapped__(request=None, board_type="行业")

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
        lambda holdings, **_kwargs: SimpleNamespace(holding_alerts=[]),
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
    monkeypatch.setattr(market.global_market_service, "read_cached_snapshot", lambda: payload)
    monkeypatch.setattr(
        market.global_market_service,
        "snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET global cues must not refresh the provider")
        ),
    )

    response = client.get("/api/market/global-cues")

    assert response.status_code == 200
    data = response.json()
    assert data["data_quality"] == "degraded"
    assert data["korea_equities"][0]["status"] == "unavailable"
    assert data["korea_equities"][0]["price"] is None


def _persist_opportunity_radar_snapshot(
    db_session,
    payload: dict,
    *,
    trade_date: str | None = None,
) -> DataCaptureSnapshot:
    row = DataCaptureSnapshot(
        trade_date=trade_date or str(payload["as_of"])[:10],
        captured_at=datetime.now(),
        source="test-opportunity-radar-collector",
        data_type="opportunity_radar",
        target_code="market",
        target_name="全市场",
        raw_value_json="{}",
        normalized_value_json=json.dumps(payload, ensure_ascii=False),
        quality=str(payload.get("data_quality") or "missing"),
        status="ok",
        is_complete=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _empty_expansion(now: str, *, note: str = "暂无增量证据") -> dict:
    return {
        "updated_at": now,
        "as_of": now,
        "window_minutes": 0,
        "data_quality": "missing",
        "source": [],
        "items": [],
        "counts": {"增量已确认": 0, "增量待确认": 0},
        "notes": [note],
    }


def _forbid_opportunity_radar_provider_calls(monkeypatch) -> None:
    from app.api.routes import checks

    def unexpected(*_args, **_kwargs):
        raise AssertionError("GET opportunity radar must not call a provider")

    for name in (
        "information_differential",
        "sector_flow",
        "sector_opening_breadth",
        "limit_up_ladder",
    ):
        monkeypatch.setattr(checks.market_provider, name, unexpected)


def test_opportunity_radar_route_never_turns_news_into_buy_signal(
    client, db_session, monkeypatch
):
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    payload = {
        "updated_at": now,
        "as_of": now,
        "source": ["测试资讯源", "测试资金源"],
        "data_quality": "degraded",
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
        "notes": ["缺少同交易日市场环境快照。"],
        "available_sector_evidence": 1,
        "intraday_expansion": _empty_expansion(now),
        "consensus_high_open_fade": {
            "code": "CONSENSUS_HIGH_OPEN_FADE",
            "label": "一致高开回落",
            "status": "DATA_GAP",
        },
    }
    _persist_opportunity_radar_snapshot(db_session, payload)
    _forbid_opportunity_radar_provider_calls(monkeypatch)
    before = db_session.query(DataCaptureSnapshot).count()

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
    assert db_session.query(DataCaptureSnapshot).count() == before

    historical = client.get("/api/intel/opportunity-radar?date=2000-01-04")
    assert historical.status_code == 200
    assert historical.json()["data_quality"] == "missing"
    assert historical.json()["items"] == []
    assert db_session.query(DataCaptureSnapshot).count() == before


def test_opportunity_radar_route_serializes_confirmed_intraday_expansion(
    client, db_session, monkeypatch
):
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    expansion = {
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
            "action": "仅加入观察，等待回踩确认。",
            "invalidation": ["资金重新拐为流出。"],
            "source": ["eastmoney"],
            "as_of": now,
            "buy_signal": False,
        }],
        "counts": {"增量已确认": 1, "增量待确认": 0},
        "notes": ["只生成观察结论。"],
    }
    payload = {
        "updated_at": now,
        "as_of": now,
        "source": ["collector"],
        "data_quality": "ok",
        "items": [],
        "counts": {},
        "discipline": "资讯不得单独触发买入。",
        "notes": [],
        "available_sector_evidence": 1,
        "intraday_expansion": expansion,
    }
    _persist_opportunity_radar_snapshot(db_session, payload)
    _forbid_opportunity_radar_provider_calls(monkeypatch)

    response = client.get("/api/intel/opportunity-radar")

    assert response.status_code == 200
    actual = response.json()["intraday_expansion"]
    assert actual["items"][0]["status"] == "增量已确认"
    assert actual["items"][0]["buy_signal"] is False
    assert actual["items"][0]["new_limit_up_count"] == 3
    assert actual["source"] == ["东方财富涨停池", "eastmoney"]


def test_opportunity_radar_route_serves_persisted_degradation_without_provider(
    client, db_session, monkeypatch
):
    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    payload = {
        "updated_at": now,
        "as_of": now,
        "source": [],
        "data_quality": "missing",
        "items": [],
        "counts": {},
        "discipline": "资讯不得单独触发买入。",
        "notes": ["采集器已记录上游不可用。"],
        "available_sector_evidence": 0,
        "intraday_expansion": _empty_expansion(now, note="涨停天梯暂不可用。"),
    }
    _persist_opportunity_radar_snapshot(db_session, payload)
    _forbid_opportunity_radar_provider_calls(monkeypatch)

    response = client.get("/api/intel/opportunity-radar")

    assert response.status_code == 200
    expansion = response.json()["intraday_expansion"]
    assert expansion["data_quality"] == "missing"
    assert expansion["items"] == []
    assert expansion["window_minutes"] == 0
    assert "暂不可用" in expansion["notes"][0]


def test_opportunity_radar_refresh_post_runs_collector_then_reads_snapshot(
    client, db_session, monkeypatch
):
    from app.services import intraday_collector

    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
    payload = {
        "updated_at": now,
        "as_of": now,
        "source": ["manual-refresh-fixture"],
        "data_quality": "ok",
        "items": [],
        "counts": {},
        "discipline": "资讯不得单独触发买入。",
        "notes": [],
        "available_sector_evidence": 0,
    }
    calls: list[tuple[str, bool]] = []

    def fake_collection(trigger="manual", force_refresh=False):
        calls.append((trigger, force_refresh))
        _persist_opportunity_radar_snapshot(db_session, payload)
        return {"status": "ok"}

    monkeypatch.setattr(
        intraday_collector,
        "run_opportunity_radar_collection_once",
        fake_collection,
    )

    response = client.post("/api/intel/opportunity-radar/refresh")

    assert response.status_code == 200
    assert response.json()["source"] == ["manual-refresh-fixture"]
    assert calls == [("manual", True)]


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
