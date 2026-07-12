def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"

def test_market_sector_flow(client):
    response = client.get("/api/market/sector-flow")
    assert response.status_code == 200
    data = response.json()
    assert "source" in data
    assert "inflow" in data
    assert "outflow" in data
    assert isinstance(data["inflow"], list)
    assert isinstance(data["outflow"], list)


def test_intraday_collector_status(client):
    response = client.get("/api/intraday-collector/status")

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["interval_seconds"] >= 1
    assert "running" in data


def test_time_stop_rules_can_be_listed_and_updated(client):
    response = client.get("/api/time-stop-rules")

    assert response.status_code == 200
    rules = response.json()
    assert any(rule["script_type"] == "breakout" for rule in rules)

    update = client.put("/api/time-stop-rules/breakout", json={
        "confirmation_deadline": "09:50",
        "below_vwap_minutes": 4,
        "below_vwap_min_bars": 4,
        "recent_window_minutes": 12,
        "failed_limit_reseal_pct": 0.992,
    })

    assert update.status_code == 200
    data = update.json()
    assert data["confirmation_deadline"] == "09:50"
    assert data["below_vwap_minutes"] == 4
    assert data["failed_limit_reseal_pct"] == 0.992


def test_intraday_review_route_returns_current_evidence(client, db_session):
    from app.models.trading import Holding

    holding = Holding(
        code="600018",
        name="复盘接口",
        quantity=1000,
        cost_price=10,
        current_price=10.6,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="按计划观察",
    )
    db_session.add(holding)
    db_session.commit()

    response = client.get("/api/stocks/600018/intraday-review")

    assert response.status_code == 200
    data = response.json()
    assert data["code"] == "600018"
    assert data["latest_action"]
    assert "timeline" in data
    assert isinstance(data["evidence"], list)


def test_review_calibration_summary(client, db_session):
    from app.models.trading import (
        ActionRecommendation,
        ExpectationSnapshot,
        NextDayPlan,
        RecommendationFeedback,
        TTradePlan,
        TradeLog,
        TradeReview,
        VolumePriceSnapshot,
    )

    trade = TradeLog(
        code="600010",
        name="校准样本",
        side="买入",
        price=10,
        quantity=1000,
        amount=10000,
        total_asset=100000,
        position_ratio=0.1,
        cost_price=10,
        stop_loss_price=9.6,
        reason="无计划追高",
        mode="标准短线模式",
        compliant=False,
        human_tags="冲动",
    )
    db_session.add(trade)
    db_session.flush()
    db_session.add(TradeReview(
        trade_id=trade.id,
        code=trade.code,
        name=trade.name,
        verdict="明显偏离",
        status="done",
        discipline_score=45,
        summary="买入校准样本复盘：明显偏离。",
        stock_context="个股证据不足",
        sector_context="板块证据不足",
        market_context="市场弱",
        mistakes='["无计划交易"]',
        avoid_actions='["下次先写计划"]',
        weakness_tags='["冲动"]',
    ))
    db_session.add(NextDayPlan(
        plan_date="2026-07-13",
        plan_type="holding",
        code="600011",
        name="未复盘计划",
    ))
    db_session.add(NextDayPlan(
        plan_date="2026-07-13",
        plan_type="holding",
        code="600012",
        name="已复盘计划",
        review_expectation="弱于预期",
        review_execution="未执行减仓",
        review_deviation="幻想回拉",
    ))
    for index in range(5):
        db_session.add(ExpectationSnapshot(
            trade_date="2026-07-13",
            code=f"60002{index}",
            name=f"预期样本{index}",
            stage="五分钟确认",
            base_expectation="STRONG",
            expectation_result="WEAKER" if index < 3 else "MATCHED",
            expectation_gap_score=-30 if index < 3 else 5,
        ))
    for index in range(8):
        db_session.add(VolumePriceSnapshot(
            trade_date="2026-07-13",
            code=f"60003{index}",
            name=f"量价样本{index}",
            stage="五分钟确认",
            price=10,
            vwap=10.2,
            pattern="跌破VWAP" if index < 5 else "量价中性",
        ))
    for index in range(3):
        db_session.add(TTradePlan(
            holding_id=index + 1,
            trade_date="2026-07-13",
            code=f"60004{index}",
            name=f"做T样本{index}",
            status="done",
            cost_reduction=0.02 if index == 0 else -0.01,
        ))
    recommendation = ActionRecommendation(
        trade_date="2026-07-13",
        code="600050",
        name="执行样本",
        level="WARN",
        state="VWAP_BREAKDOWN",
        action="减仓25%",
    )
    db_session.add(recommendation)
    db_session.flush()
    db_session.add(RecommendationFeedback(
        recommendation_id=recommendation.id,
        status="暂不执行",
        reason="主观等待回拉",
    ))
    db_session.commit()

    response = client.get("/api/review-calibration/summary")

    assert response.status_code == 200
    data = response.json()
    assert data["avg_discipline_score"] == 45
    assert data["missing_plan_review_count"] == 1
    assert data["plan_review_count"] == 1
    assert any(item["title"] == "纪律评分低于 60" for item in data["issues"])
    assert data["recent_plan_deviations"][0]["severity"] == "高"
    assert {item["key"] for item in data["model_metrics"]} >= {
        "expectation_hit",
        "volume_price_risk",
        "t_trade_effect",
        "execution_adoption",
        "plan_execution_drift",
    }
    assert any(item["target"] == "预期阈值" for item in data["calibration_suggestions"])
def test_login_rejects_invalid_password(client):
    response = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401


def test_protected_api_requires_session(client):
    from app.core.security import require_auth
    from app.main import app

    override = app.dependency_overrides.pop(require_auth)
    try:
        response = client.get("/api/holdings")
        assert response.status_code == 401
    finally:
        app.dependency_overrides[require_auth] = override


def test_active_alert_can_be_acknowledged(client, db_session):
    from datetime import datetime, timedelta
    from app.models.trading import ActionRecommendation

    row = ActionRecommendation(
        trade_date="2026-07-12",
        holding_id=88,
        code="600888",
        name="alert test",
        created_at=datetime.now(),
        level="REDUCE",
        state="REDUCE_REQUIRED",
        action="reduce 25%",
        evidence_json='["risk"]',
        counter_evidence_json="[]",
        invalid_conditions_json='["breakdown"]',
        recovery_conditions_json='["recover"]',
        expires_at=datetime.now() + timedelta(minutes=15),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)

    active = client.get("/api/alerts/active")
    assert active.status_code == 200
    assert active.json()[0]["code"] == "600888"
    acknowledged = client.post(f"/api/alerts/{row.id}/acknowledge")
    assert acknowledged.status_code == 200
    assert acknowledged.json()["acknowledged_at"] is not None
    assert client.get("/api/alerts/active").json() == []


def test_candidate_pool_excludes_invalid_execution(client, db_session):
    from datetime import datetime
    from app.models.trading import ExpectationSnapshot, Holding, PositionExecutionState, VolumePriceSnapshot

    holding = Holding(code="600777", name="candidate", quantity=1000, cost_price=10, current_price=9, total_asset=100000)
    db_session.add(holding)
    db_session.flush()
    db_session.add(ExpectationSnapshot(trade_date="2026-07-12", code="600777", name="candidate", stage="intraday", base_expectation="STRONG", expectation_result="INVALID"))
    db_session.add(VolumePriceSnapshot(trade_date="2026-07-12", code="600777", name="candidate", stage="intraday", captured_at=datetime.now(), vwap_reliable=True, data_quality="realtime", pattern="VWAP_BREAKDOWN"))
    db_session.add(PositionExecutionState(holding_id=holding.id, code="600777", name="candidate", trade_date="2026-07-12", state="EXIT_REQUIRED"))
    db_session.commit()

    response = client.get("/api/candidates")
    assert response.status_code == 200
    candidate = response.json()[0]
    assert candidate["pool"] == "D"
    assert candidate["score"] < 35
    assert candidate["exclusions"]


def test_strategy_templates_seed_and_version(client):
    response = client.get("/api/strategies/templates")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) >= 12
    template = rows[0]
    template["position_limit"] = 0.3
    updated = client.put(f"/api/strategies/templates/{template['id']}", json=template)
    assert updated.status_code == 200
    assert updated.json()["version"] == template["version"] + 1
    assert updated.json()["position_limit"] == 0.3


def test_historical_replay_orders_evidence_frames(client, db_session):
    from datetime import datetime, timedelta
    from app.models.trading import ActionRecommendation, IntradayEvidenceEvent

    start = datetime(2026, 7, 10, 9, 30)
    db_session.add(IntradayEvidenceEvent(trade_date="2026-07-10", captured_at=start, target_code="600123", target_name="replay", event_type="VWAP_BROKEN", evidence_json='["broken"]'))
    db_session.add(ActionRecommendation(trade_date="2026-07-10", code="600123", name="replay", created_at=start + timedelta(minutes=5), level="REDUCE", state="REDUCE_REQUIRED", action="reduce 25%"))
    db_session.commit()
    response = client.get("/api/replay/600123?trade_date=2026-07-10")
    assert response.status_code == 200
    report = response.json()
    assert report["complete"] is True
    assert [frame["frame_type"] for frame in report["frames"]] == ["event", "recommendation"]
