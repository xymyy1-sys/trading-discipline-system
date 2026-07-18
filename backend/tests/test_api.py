def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_holding_summary_includes_today_closed_position_profit(client, db_session):
    from datetime import datetime, timezone
    from app.models.trading import AccountState, Holding, TradeLog

    db_session.add(AccountState(id=1, total_asset=100000))
    db_session.add(Holding(code="600001", name="仍持仓", quantity=100, cost_price=10, current_price=11, total_asset=100000))
    db_session.add(TradeLog(
        code="600002", name="今日清仓",
        traded_at=datetime.now(timezone.utc).replace(tzinfo=None), side="卖出",
        price=12, quantity=100, amount=1200, total_asset=100000,
        position_ratio=0.012, cost_price=10, stop_loss_price=9.6,
        reason="按计划清仓", mode="标准短线模式", compliant=True, human_tags="",
    ))
    db_session.commit()

    response = client.get("/api/holdings/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["today_realized_profit_amount"] == 200
    assert data["today_profit_amount"] == data["today_open_profit_amount"] + 200


def test_expectation_chain_is_append_only_and_has_scenarios(client, monkeypatch):
    from app.api.helpers import decision

    monkeypatch.setattr(decision, "quote_for_code", lambda code: {
        "name": "预期链测试", "price": 10.5, "prev_close": 10, "open": 10.2,
        "open_pct": 2.0, "change_pct": 5.0, "note": "实时行情",
    })
    first = client.get("/api/stocks/600003/expectation")
    assert first.status_code == 200
    second = client.post("/api/expectations", json={
        "code": "600003", "name": "预期链测试", "stage": "午后确认",
        "base_hint": "强预期", "actual_open_pct": -5, "actual_change_pct": -6,
    })
    assert second.status_code == 200
    chain = client.get(f"/api/stocks/600003/expectation-chain?trade_date={second.json()['trade_date']}")
    assert chain.status_code == 200
    data = chain.json()
    assert len(data["revisions"]) == 2
    assert [item["version"] for item in data["revisions"]] == [1, 2]
    assert len(data["revisions"][-1]["scenarios"]) == 5


def test_expectation_chain_ignores_refresh_noise_but_keeps_reversal_transition(db_session):
    from app.api.helpers.decision import build_expectation_snapshot
    from app.models.trading import ExpectationRevision, VolumePriceSnapshot

    first = build_expectation_snapshot(
        db_session, "600103", name="版本去重", stage="五分钟确认", base_hint="强预期",
        quote={"price": 10.2, "prev_close": 10, "open": 10.2, "change_pct": 2, "note": "实时行情"},
    )
    second = build_expectation_snapshot(
        db_session, "600103", name="版本去重", stage="五分钟确认", base_hint="强预期",
        quote={"price": 10.4, "prev_close": 10, "open": 10.2, "change_pct": 4, "note": "实时行情"},
    )
    assert first.actual_open_pct == second.actual_open_pct == 2
    assert db_session.query(ExpectationRevision).filter(ExpectationRevision.code == "600103").count() == 1

    db_session.add(VolumePriceSnapshot(
        trade_date=second.trade_date, code="600103", name="版本去重", stage="五分钟确认",
        price=10.5, change_pct=5, prev_close=10, vwap=9.8, vwap_source="minute",
        minute_bar_count=8, vwap_reliable=True, price_vs_vwap=7.14,
        pattern="水下V形反转站回VWAP", data_quality="realtime",
    ))
    db_session.commit()
    third = build_expectation_snapshot(
        db_session, "600103", name="版本去重", stage="五分钟确认", base_hint="强预期",
        quote={"price": 10.5, "prev_close": 10, "open": 10.2, "change_pct": 5, "note": "实时行情"},
    )
    assert third.actual_open_pct == 2
    assert db_session.query(ExpectationRevision).filter(ExpectationRevision.code == "600103").count() == 2

    # A transient empty provider response must not overwrite the verified open
    # with 0% or append another revision.
    degraded = build_expectation_snapshot(
        db_session, "600103", name="版本去重", stage="五分钟确认", base_hint="强预期", quote={},
    )
    assert degraded.actual_open_pct == 2
    assert degraded.actual_change_pct == 5
    assert db_session.query(ExpectationRevision).filter(ExpectationRevision.code == "600103").count() == 2


def test_close_baseline_enters_expectation_revision_chain(db_session, monkeypatch):
    from app.api.routes import stocks
    from app.models.trading import ExpectationRevision, ExpectationScenario, Holding, VolumePriceSnapshot
    from app.services.next_day_expectations import generate_next_day_expectations

    monkeypatch.setattr(stocks, "watchlist_recommendations", lambda db: [])
    db_session.add(Holding(
        code="600006", name="收盘基线", quantity=100, cost_price=10,
        current_price=10.5, total_asset=100000, position_type="趋势持仓",
    ))
    db_session.add(VolumePriceSnapshot(
        trade_date="2026-07-13", code="600006", name="收盘基线", stage="收盘",
        price=10.5, change_pct=5, vwap=10.2, vwap_reliable=True,
        price_vs_vwap=2.94, high_drawdown=2, pattern="量价健康", data_quality="realtime",
    ))
    db_session.commit()

    assert generate_next_day_expectations(db_session) == 1
    revision = db_session.query(ExpectationRevision).filter(ExpectationRevision.code == "600006").one()
    assert revision.trigger == "close_baseline"
    assert revision.volume_price_state == "量价健康"
    assert db_session.query(ExpectationScenario).filter(ExpectationScenario.revision_id == revision.id).count() == 5

def test_market_sector_flow(client):
    response = client.get("/api/market/sector-flow")
    assert response.status_code == 200
    data = response.json()
    assert "source" in data
    assert "inflow" in data
    assert "outflow" in data
    assert isinstance(data["inflow"], list)
    assert isinstance(data["outflow"], list)


def test_watchlist_recommendations_combine_theme_and_limit_quality(client, monkeypatch):
    from datetime import datetime
    from app.schemas.trading import (
        LimitUpGroupOut, LimitUpLadderOut, LimitUpStockOut,
        ThemeRadarItem, ThemeRadarOut, ThemeStockRole,
    )
    from app.services.market_data import MarketDataProvider

    now = datetime.now()
    theme = ThemeRadarItem(
        name="机器人", theme_type="概念", stage="主升", stage_reason="资金共振",
        score=92, rank=1, change_pct=4.2, net_inflow=18.6, main_inflow=12.1,
        limit_up_count=8, stock_count=30, leader_names=["测试龙头"],
        core_stocks=[ThemeStockRole(code="600001", name="测试龙头", role="龙头", change_pct=10, reason="前排")],
        resonance_tags=["资金共振"], action="观察", risk="高位分歧",
    )
    radar = ThemeRadarOut(
        source="测试题材源", updated_at=now, market_temperature="强",
        strongest_theme=theme, resonance=[theme], themes=[theme], notes=[],
    )
    ladder = LimitUpLadderOut(
        source="测试涨停源", trade_date="2026-07-12", updated_at=now,
        groups=[LimitUpGroupOut(level=3, label="三连板", stocks=[LimitUpStockOut(
            code="600001", name="测试龙头", turnover=12, break_count=0,
            consecutive_limit_days=3, sealed_amount=1.5, price=10.0, concepts=["机器人"],
        )])], clusters=[], summary=[], notes=[],
    )
    monkeypatch.setattr(MarketDataProvider, "theme_radar", lambda self: radar)
    monkeypatch.setattr(MarketDataProvider, "limit_up_ladder", lambda self: ladder)

    response = client.get("/api/watchlist-recommendations")
    assert response.status_code == 200
    data = response.json()
    assert data[0]["code"] == "600001"
    assert data[0]["tier"] == "重点观察"
    assert data[0]["gate_passed"] is True
    assert data[0]["expectation_status"].startswith("系统推演")
    assert "封板量价确认" in data[0]["volume_price_status"]
    assert data[0]["limit_quality"] == "封板稳定、未炸板"
    assert any("题材排名" in reason for reason in data[0]["reasons"])


def test_intraday_collector_status(client):
    response = client.get("/api/intraday-collector/status")

    assert response.status_code == 200
    data = response.json()
    assert data["enabled"] is True
    assert data["interval_seconds"] >= 1
    assert "running" in data
    assert "last_success_at" in data
    assert "last_error" in data
    assert "opportunity_radar_running" in data
    assert "opportunity_radar_last_success_at" in data
    assert "opportunity_radar_last_error" in data


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
    assert any(item["target"] == "结果闭环" for item in data["calibration_suggestions"])
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


def test_effectiveness_endpoints_require_sample_gate(client):
    for path in ("expectation-effectiveness", "volume-price-effectiveness", "execution-effectiveness"):
        response = client.get(f"/api/reviews/{path}")
        assert response.status_code == 200
        assert response.json()["auto_calibration_allowed"] is False
        assert "sample_count" in response.json()["metric"]


def test_calibration_apply_requires_gate_and_explicit_confirmation(client):
    proposal = client.get("/api/reviews/calibration-proposal")
    assert proposal.status_code == 200
    assert proposal.json()["eligible"] is False

    response = client.post("/api/reviews/calibration-apply", json={"confirmation": "APPLY_CALIBRATION"})
    assert response.status_code == 409


def test_expectation_state_counts_cannot_apply_without_forward_outcomes(client, db_session):
    from app.models.trading import ExpectationRule, ExpectationSnapshot

    client.get("/api/expectation-rules")
    rule = db_session.query(ExpectationRule).order_by(ExpectationRule.id).first()
    original_under = rule.underperform_threshold
    original_outperform = rule.outperform_threshold
    for idx in range(20):
        db_session.add(ExpectationSnapshot(
            trade_date=f"2026-06-{idx + 1:02d}", code=f"60{idx:04d}", stage="盘中",
            base_expectation="STRONG", expectation_result="WEAKER" if idx < 10 else "MATCHED",
        ))
    db_session.commit()

    proposal = client.get("/api/reviews/calibration-proposal").json()
    assert proposal["eligible"] is False
    assert proposal["sample_count"] == 0
    assert proposal["minimum_samples"] == 30
    assert proposal["changes"] == []
    assert "没有真实结果闭环，禁止校准" in proposal["rationale"]

    denied = client.post("/api/reviews/calibration-apply", json={"confirmation": "yes"})
    assert denied.status_code == 409
    applied = client.post("/api/reviews/calibration-apply", json={"confirmation": "APPLY_CALIBRATION"})
    assert applied.status_code == 409
    db_session.expire_all()
    assert db_session.get(ExpectationRule, rule.id).underperform_threshold == original_under
    assert db_session.get(ExpectationRule, rule.id).outperform_threshold == original_outperform


def test_acceptance_report_contains_security_sse_and_t1(client):
    response = client.get("/api/acceptance/report")
    assert response.status_code == 200
    report = response.json()
    assert report["security"]["authentication_required"] is True
    assert report["sse"]["authenticated"] is True
    assert "t_plus_one_validations" in report


def test_data_quality_health_aggregates_provider_history(client, db_session):
    from datetime import datetime
    from app.models.trading import DataCaptureSnapshot
    db_session.add(DataCaptureSnapshot(trade_date="2026-07-12", captured_at=datetime.now(), source="provider-a", data_type="stock_minute", target_code="600001", quality="realtime", latency_ms=120, status="ok", is_complete=True, raw_payload_hash="a" * 64))
    db_session.add(DataCaptureSnapshot(trade_date="2026-07-12", captured_at=datetime.now(), source="provider-a", data_type="stock_minute", target_code="600002", quality="degraded", latency_ms=280, status="fetch_error", is_degraded=True, raw_payload_hash="b" * 64))
    db_session.commit()
    response = client.get("/api/data-quality/health")
    assert response.status_code == 200
    provider = response.json()["providers"][0]
    assert provider["sample_count"] == 2
    assert provider["degraded_count"] == 1
    assert provider["average_latency_ms"] == 200


def test_risk_position_uses_tightest_cap_and_board_lot(client):
    response = client.post("/api/checks/risk-position", json={
        "net_asset": 100000, "risk_ratio": 0.01, "entry_price": 10, "stop_price": 9,
        "script_limit": 0.3, "market_limit": 0.5, "single_stock_limit": 0.4,
        "sector_limit": 0.35, "liquidity_limit": 0.08, "lot_size": 100,
    })
    assert response.status_code == 200
    result = response.json()
    assert result["binding_limit"] == "liquidity_limit"
    assert result["quantity"] == 800
    assert result["final_position_value"] == 8000
