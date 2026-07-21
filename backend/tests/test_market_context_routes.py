from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.models.trading import DataCaptureSnapshot, GlobalEvidenceSnapshot, MarketRegimeSnapshot


def test_sector_temperature_route_keeps_margin_as_t_plus_one(db_session, monkeypatch):
    from app.api.routes import market
    from app.services import sector_temperature as sector_temperature_service

    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

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
    monkeypatch.setattr(
        market.market_provider,
        "limit_up_atmosphere",
        lambda trade_date=None, force_refresh=False: SimpleNamespace(
            trade_date=trade_date or "2026-07-17",
            theme_ladders=[SimpleNamespace(
                name="测试行业",
                limit_up_count=3,
                promotion_rate=25.0,
                break_rate=40.0,
            )],
        ),
    )
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
    monkeypatch.setattr(market, "load_sector_persistence_features", lambda _db, board_type=None: {
        "测试行业": {
            "board_code": "BK0001",
            "daily_turnover_by_trade_date": {
                "2026-07-16": 400.0,
                "2026-07-17": 500.0,
            },
        },
    })
    monkeypatch.setattr(market, "fetch_sector_audited_flow", lambda trade_date: {
        "status": "ok",
        "trade_date": trade_date,
        "merge_map": {
            "BK0001": {
                "trade_date": trade_date,
                "non_leveraged_net_inflow": 6.5,
                "non_leveraged_net_inflow_unit": "亿元",
                "methodology_id": "licensed-sector-flow-v1",
                "source_url": "https://licensed.example.test/BK0001",
                "published_at": "2026-07-17T10:29:00+08:00",
                "new_high_count": 8,
                "constituent_count": 40,
                "new_high_window": 20,
                "etf_share_net_change": 3200.0,
                "etf_share_change_pct": 1.25,
                "etf_id": "510300",
                "etf_share_unit": "份",
                "etf_share_base": 256000.0,
                "etf_methodology_id": "official-etf-shares-v1",
                "etf_flow_audited": True,
            },
        },
        "notes": [],
    })
    monkeypatch.setattr(market, "fetch_sector_margin", lambda _board_type, _force: {
        "items": {
            "测试行业": {
                "as_of": "2026-07-16",
                "financing_balance": 120.0,
                "financing_buy": 20.0,
                "financing_net_buy": 4.0,
                "financing_balance_ratio": 3.5,
                "net_buy_5d": 12.0,
                "net_buy_10d": 20.0,
            }
        },
        "notes": ["T+1测试口径"],
    })

    result = market.refresh_sector_temperature.__wrapped__(
        request=None,
        board_type="行业",
        db=db_session,
    )

    assert result.items[0].name == "测试行业"
    assert result.items[0].margin_realtime is False
    assert result.items[0].margin_as_of == "2026-07-16"
    assert result.items[0].financing_reference_turnover == 400.0
    assert result.items[0].financing_turnover_as_of == "2026-07-16"
    assert result.items[0].financing_turnover_date_aligned is True
    assert result.items[0].financing_buy_turnover_ratio == 5.0
    assert result.items[0].non_leveraged_flow_audited is True
    assert result.items[0].non_leveraged_net_inflow == 6.5
    assert result.items[0].non_leveraged_flow_source_url == (
        "https://licensed.example.test/BK0001"
    )
    assert result.items[0].non_leveraged_flow_published_at == (
        "2026-07-17T10:29:00+08:00"
    )
    assert result.items[0].new_high_count == 8
    assert result.items[0].constituent_count == 40
    assert result.items[0].new_high_ratio == 20.0
    assert result.items[0].etf_flow_audited is True
    assert result.items[0].etf_share_net_change == 3200.0
    assert result.items[0].etf_share_change_pct == 1.25
    assert result.items[0].non_leveraged_net_inflow_unit == "亿元"
    assert result.items[0].non_leveraged_methodology_id == "licensed-sector-flow-v1"
    assert result.items[0].etf_id == "510300"
    assert result.items[0].etf_share_unit == "份"
    assert result.items[0].etf_share_base == 256000.0
    assert result.items[0].etf_methodology_id == "official-etf-shares-v1"
    assert result.items[0].promotion_rate == 25.0
    assert result.items[0].break_rate == 40.0
    assert result.items[0].flow_speed == -0.8
    assert result.items[0].flow_turning == "OUTFLOW_ACCELERATING"
    assert "东方财富两融T+1" in result.source
    assert "板块订单流算法缓存" in result.source
    assert result.updated_at.isoformat() == "2026-07-17T10:29:00+08:00"
    assert any("使用 eastmoney 缓存" in note and "10:29:00" in note for note in result.notes)


def test_sector_temperature_second_distinct_sample_confirms_on_same_refresh(
    db_session,
    monkeypatch,
):
    from app.api.routes import market
    from app.services import sector_temperature as sector_temperature_service

    clock = {"now": datetime(2026, 7, 21, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))}
    current_net = {"value": -40.0}
    monkeypatch.setattr(sector_temperature_service, "_shanghai_now", lambda: clock["now"])

    def panel(_board_type, period, _force):
        change = {"今日": -2.0, "5日": 12.0, "10日": 22.0}[period]
        net = {
            "今日": current_net["value"],
            "5日": 150.0,
            "10日": 260.0,
        }[period]
        item = SimpleNamespace(
            name="持续性测试行业",
            board_code="BK-PERSIST",
            change_pct=change,
            net_inflow=net,
            flow_speed=-1.0 if period == "今日" else None,
            flow_acceleration=-0.2 if period == "今日" else None,
            flow_turning="INFLOW_FADING" if period == "今日" else None,
            provider_trade_date="2026-07-21" if period == "今日" else None,
            provider_updated_at=clock["now"].isoformat() if period == "今日" else None,
            limit_up_count=0,
        )
        return SimpleNamespace(
            source="eastmoney",
            updated_at=clock["now"],
            inflow=[item] if net > 0 else [],
            outflow=[item] if net < 0 else [],
        )

    monkeypatch.setattr(market.market_provider, "board_flow_panel", panel)
    monkeypatch.setattr(market.market_provider, "hot_themes", lambda _force: SimpleNamespace(items=[]))
    monkeypatch.setattr(market, "_get_cached_flow", lambda _key: None)
    monkeypatch.setattr(market, "fetch_sector_audited_flow", lambda trade_date: {
        "status": "unavailable",
        "trade_date": trade_date,
        "merge_map": {},
        "notes": [],
    })
    monkeypatch.setattr(market.market_provider, "limit_up_atmosphere", lambda **_kwargs: SimpleNamespace(
        trade_date="2026-07-21",
        theme_ladders=[],
    ))
    monkeypatch.setattr(market, "fetch_sector_margin", lambda *_args, **_kwargs: {
        "items": {
            "持续性测试行业": {
                "as_of": "2026-07-20",
                "financing_balance_ratio": 10.0,
                "financing_net_buy": 10.0,
                "net_buy_5d": 40.0,
                "net_buy_10d": 80.0,
                "net_buy_20d": 120.0,
            }
        },
        "notes": [],
    })

    first = market.refresh_sector_temperature.__wrapped__(
        request=None,
        board_type="行业",
        db=db_session,
    )
    assert first.items[0].instantaneous_distribution_state == "高位派发风险"
    assert first.items[0].distribution_state == "资金承载衰减"
    assert first.items[0].sample_confirmation_count == 1

    clock["now"] = clock["now"] + timedelta(minutes=5)
    current_net["value"] = -45.0
    second = market.refresh_sector_temperature.__wrapped__(
        request=None,
        board_type="行业",
        db=db_session,
    )

    assert second.items[0].sample_confirmation_count == 2
    assert second.items[0].persistence_confirmed is True
    assert second.items[0].distribution_state == "高位派发风险"


def test_sector_temperature_get_restores_latest_database_snapshot_after_restart(
    db_session,
    monkeypatch,
):
    from app.api.routes import market
    from app.services.sector_evidence_history import persist_sector_temperature_snapshot

    persist_sector_temperature_snapshot(db_session, {
        "source": "audited-test-provider",
        "updated_at": "2026-07-21T10:05:00+08:00",
        "board_type": "行业",
        "items": [{
            "name": "数据库恢复行业",
            "board_code": "BK-RESTORE",
            "provider_trade_date": "2026-07-21",
            "provider_updated_at": "2026-07-21T10:05:00+08:00",
            "data_quality": "high",
            "status": "偏热趋势健康",
            "distribution_state": "健康增量",
            "instantaneous_distribution_state": "健康增量",
            "strict_state": "健康增量",
            "change_pct": 1.2,
            "net_inflow": 8.0,
        }],
    })
    monkeypatch.setattr(market, "_get_response_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(market, "_set_response_cache", lambda *_args, **_kwargs: None)

    restored = market.sector_temperature.__wrapped__(
        request=None,
        board_type="行业",
        db=db_session,
    )

    assert restored.items[0].name == "数据库恢复行业"
    assert restored.source.startswith("数据库最近板块证据快照")
    assert any("进程缓存为空" in note and "2026-07-21" in note for note in restored.notes)


def test_sector_temperature_history_exposes_intraday_samples_and_state_evolution(
    client,
    db_session,
):
    from app.services.sector_evidence_history import persist_sector_temperature_snapshot

    def payload(observed_time: str, *, net_inflow: float) -> dict:
        observed_at = f"2026-07-21T{observed_time}:00+08:00"
        return {
            "source": "test-provider-order-flow + margin-T+1",
            "updated_at": observed_at,
            "board_type": "行业",
            "items": [{
                "name": "半导体",
                "board_code": "BK1036",
                "provider_trade_date": "2026-07-21",
                "provider_updated_at": observed_at,
                "data_quality": "high",
                "instantaneous_distribution_state": "资金承载衰减",
                "distribution_state": "资金承载衰减",
                "distribution_risk_level": "MEDIUM",
                "distribution_risk_score": 72,
                "change_pct": -1.2,
                "net_inflow": net_inflow,
                "flow_speed": -0.6,
                "flow_acceleration": -0.2,
                "flow_turning": "OUTFLOW_ACCELERATING",
                "margin_as_of": "2026-07-20",
                "distribution_evidence": ["资金进入后价格响应下降"],
                "distribution_counter_evidence": [],
                "distribution_actions": ["停止追高，等待价格重新响应"],
            }],
        }

    persist_sector_temperature_snapshot(
        db_session,
        payload("10:00", net_inflow=-8.0),
    )
    persist_sector_temperature_snapshot(
        db_session,
        payload("10:05", net_inflow=-10.0),
    )

    intraday = client.get(
        "/api/market/sector-temperature/history",
        params={"scope": "intraday", "board_code": "BK1036", "limit": 10},
    )
    evolution = client.get(
        "/api/market/sector-temperature/history",
        params={"scope": "evolution", "board_code": "BK1036", "limit": 10},
    )
    invalid = client.get(
        "/api/market/sector-temperature/history",
        params={"scope": "invalid"},
    )

    assert intraday.status_code == 200
    assert len(intraday.json()) == 2
    assert [item["net_inflow"] for item in intraday.json()] == [-8.0, -10.0]
    assert intraday.json()[0]["instantaneous_state"] == "资金承载衰减"
    assert intraday.json()[0]["resolved_state"] == "资金承载衰减"
    assert intraday.json()[0]["provider_updated_at"].startswith("2026-07-21T10:00")

    assert evolution.status_code == 200
    assert len(evolution.json()) == 1
    state_path = evolution.json()[0]
    assert state_path["board_code"] == "BK1036"
    assert state_path["strict_state"] == "资金承载衰减"
    assert state_path["sample_confirmation_count"] == 2
    assert state_path["sample_confirmation_min_interval_seconds"] == 300
    assert len(state_path["samples"]) == 2
    assert invalid.status_code == 422


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


def _global_refresh_payload() -> dict:
    now = datetime(2026, 7, 20, 8, 15, tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()
    quote = {
        "symbol": "SPX",
        "name": "标普500",
        "market": "美国",
        "status": "ok",
        "price": 6310.2,
        "change_pct": 0.62,
        "as_of": now,
        "source": "eastmoney",
    }
    return {
        "generated_at": now,
        "as_of": now,
        "quality": "ok",
        "data_quality": "ok",
        "sources": ["eastmoney"],
        "source": ["eastmoney"],
        "notes": [],
        "kis": {"configured": False},
        "korea_indices": [],
        "korea_equities": [],
        "us_indices": [quote],
        "us_sector_rank": [],
        "items": [{"group": "us_index", **quote}],
    }


def test_global_cues_manual_refresh_persists_current_snapshot(client, db_session, monkeypatch):
    from app.api.routes import market

    payload = _global_refresh_payload()
    monkeypatch.setattr(
        market.global_market_service,
        "snapshot",
        lambda **kwargs: payload if kwargs.get("force_refresh") is True else None,
    )

    response = client.post("/api/market/global-cues/refresh")

    assert response.status_code == 200
    rows = db_session.query(GlobalEvidenceSnapshot).all()
    assert len(rows) == 1
    assert rows[0].data_quality == "ok"
    persisted = json.loads(rows[0].payload_json)
    assert persisted["us_indices"][0]["symbol"] == "SPX"
    assert persisted["us_indices"][0]["change_pct"] == 0.62


def test_global_cues_manual_refresh_reports_persistence_failure_without_losing_result(
    client,
    monkeypatch,
):
    from app.api.routes import market

    payload = _global_refresh_payload()
    monkeypatch.setattr(
        market.global_market_service,
        "snapshot",
        lambda **kwargs: payload if kwargs.get("force_refresh") is True else None,
    )
    monkeypatch.setattr(
        market,
        "persist_global_evidence_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("db unavailable")),
    )

    response = client.post("/api/market/global-cues/refresh")

    assert response.status_code == 200
    data = response.json()
    assert data["us_indices"][0]["symbol"] == "SPX"
    assert any("持久化失败：RuntimeError" in note for note in data["notes"])


def test_global_cues_get_falls_back_to_latest_database_snapshot_after_restart(
    client,
    db_session,
    monkeypatch,
):
    from app.api.routes import market
    from app.services.sector_evidence_history import persist_global_evidence_snapshot

    payload = _global_refresh_payload()
    row = persist_global_evidence_snapshot(db_session, payload)
    empty_cache = {
        "generated_at": datetime.now().isoformat(),
        "as_of": datetime.now().isoformat(),
        "quality": "missing",
        "data_quality": "missing",
        "quote_quality": "missing",
        "institutional_flow_quality": "missing",
        "sources": [],
        "source": [],
        "notes": ["process restarted"],
    }
    monkeypatch.setattr(market.global_market_service, "read_cached_snapshot", lambda: empty_cache)
    monkeypatch.setattr(
        market.global_market_service,
        "snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET database fallback must not refresh providers")
        ),
    )

    response = client.get("/api/market/global-cues")

    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_origin"] == "database"
    assert data["snapshot_id"] == row.id
    assert data["persisted_at"]
    assert data["us_indices"][0]["symbol"] == "SPX"
    assert any("最近一次不可变" in note for note in data["notes"])


def test_global_cues_get_prefers_newer_database_snapshot_over_worker_cache(
    client,
    db_session,
    monkeypatch,
):
    from app.api.routes import market
    from app.services.sector_evidence_history import persist_global_evidence_snapshot

    persisted_payload = _global_refresh_payload()
    persisted_payload["generated_at"] = "2026-07-20T09:00:00+08:00"
    persisted_payload["as_of"] = "2026-07-20T09:00:00+08:00"
    persisted_payload["us_indices"][0]["change_pct"] = 1.25
    row = persist_global_evidence_snapshot(db_session, persisted_payload)

    stale_worker_cache = _global_refresh_payload()
    stale_worker_cache["generated_at"] = "2026-07-20T08:30:00+08:00"
    stale_worker_cache["as_of"] = "2026-07-20T08:30:00+08:00"
    stale_worker_cache["us_indices"][0]["change_pct"] = -2.0
    monkeypatch.setattr(
        market.global_market_service,
        "read_cached_snapshot",
        lambda: stale_worker_cache,
    )

    response = client.get("/api/market/global-cues")

    assert response.status_code == 200
    data = response.json()
    assert data["snapshot_origin"] == "database"
    assert data["snapshot_id"] == row.id
    assert data["us_indices"][0]["change_pct"] == 1.25
    assert any("比本进程缓存更新" in note for note in data["notes"])


def test_global_cues_history_exposes_snapshots_and_evolution(client, db_session):
    from app.services.sector_evidence_history import persist_global_evidence_snapshot

    first = _global_refresh_payload()
    first["quote_quality"] = "ok"
    first["institutional_flow_quality"] = "missing"
    persist_global_evidence_snapshot(db_session, first)
    second = _global_refresh_payload()
    second["quote_quality"] = "ok"
    second["institutional_flow_quality"] = "partial"
    second["us_indices"][0]["change_pct"] = -2.5
    second["etf_flows"] = [{
        "metric_id": "EWY_NET",
        "name": "EWY净申赎",
        "market": "美国",
        "status": "ok",
        "value": -100.0,
        "direction": "outflow",
        "source": "licensed",
        "source_url": "https://licensed.example.test/ewy",
        "published_at": second["as_of"],
        "metric_kind": "etf_share_creation_redemption",
        "data_quality": "ok",
    }]
    persist_global_evidence_snapshot(db_session, second)

    snapshots = client.get("/api/market/global-cues/history?scope=snapshots&limit=10")
    evolution = client.get("/api/market/global-cues/history?scope=evolution&limit=10")
    invalid = client.get("/api/market/global-cues/history?scope=invalid")

    assert snapshots.status_code == 200
    assert len(snapshots.json()) == 2
    assert snapshots.json()[0]["payload_hash"]
    assert snapshots.json()[0]["payload"]["snapshot_origin"] == "database"
    assert evolution.status_code == 200
    assert len(evolution.json()) == 2
    assert evolution.json()[-1]["weak_quote_count"] == 1
    assert any("机构资金质量" in item for item in evolution.json()[-1]["changes"])
    assert invalid.status_code == 422


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
