from datetime import datetime, timedelta


def test_close_baseline_is_pending_until_real_market_evidence(db_session, monkeypatch):
    from app.api.routes import stocks
    from app.models.trading import ExpectationRevision, ExpectationSnapshot, Holding
    from app.services.next_day_expectations import generate_next_day_expectations

    monkeypatch.setattr(stocks, "watchlist_recommendations", lambda _db: [])
    db_session.add(Holding(
        code="600901",
        name="待验证基线",
        quantity=100,
        cost_price=10,
        current_price=10,
        total_asset=100_000,
        position_type="趋势持仓",
    ))
    db_session.commit()

    assert generate_next_day_expectations(db_session) == 1

    snapshot = db_session.query(ExpectationSnapshot).filter_by(code="600901").one()
    revision = db_session.query(ExpectationRevision).filter_by(code="600901").one()
    assert snapshot.expectation_result == "UNKNOWN"
    assert snapshot.state_transition == "WAITING_VALIDATION"
    assert snapshot.confidence == 0
    assert "没有有效现价/收盘价" in snapshot.counter_evidence_json
    assert revision.expectation_result == "UNKNOWN"
    assert revision.state_transition == "WAITING_VALIDATION"


def test_environment_effectiveness_uses_latest_regime_by_trade_date(client, db_session):
    from app.models.trading import ExpectationSnapshot, MarketRegimeSnapshot, VolumePriceSnapshot

    # captured_at deliberately falls on another calendar day.  The persisted
    # trade_date is the authoritative join key.
    db_session.add(MarketRegimeSnapshot(
        trade_date="2026-07-10",
        captured_at=datetime(2026, 7, 11, 0, 1),
        regime_code="NEUTRAL",
        regime_name="中性震荡",
        data_quality="degraded",
    ))
    db_session.add(MarketRegimeSnapshot(
        trade_date="2026-07-10",
        captured_at=datetime(2026, 7, 11, 0, 2),
        regime_code="VOLUME_SELL_OFF",
        regime_name="放量杀跌",
        data_quality="complete",
    ))
    db_session.add(ExpectationSnapshot(
        trade_date="2026-07-10",
        code="600902",
        name="环境样本",
        stage="竞价确认",
        expectation_result="MATCHED",
        created_at=datetime(2026, 7, 10, 9, 25),
    ))
    db_session.add(ExpectationSnapshot(
        trade_date="2026-07-10",
        code="600902",
        name="环境样本",
        stage="五分钟确认",
        expectation_result="WEAKER",
        created_at=datetime(2026, 7, 10, 9, 40),
    ))
    for minute, drawdown in ((30, 1.0), (35, 6.5), (40, 2.0)):
        db_session.add(VolumePriceSnapshot(
            trade_date="2026-07-10",
            code="600902",
            name="环境样本",
            captured_at=datetime(2026, 7, 10, 14, minute),
            high_drawdown=drawdown,
            data_quality="realtime",
        ))
    db_session.add(VolumePriceSnapshot(
        trade_date="2026-07-10",
        code="600903",
        name="另一采样密度",
        captured_at=datetime(2026, 7, 10, 14, 55),
        high_drawdown=1.5,
        data_quality="realtime",
    ))
    db_session.commit()

    response = client.get("/api/reviews/environment-effectiveness")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["market_grade"] == "放量杀跌"
    assert payload[0]["expectation_samples"] == 1
    assert payload[0]["expectation_hit_rate"] == 0
    assert payload[0]["average_adverse_move"] == 4
    assert payload[0]["data_quality"] == "complete"


def test_replay_has_no_stock_specific_acceptance_checkpoints(db_session):
    from app.models.trading import ActionRecommendation
    from app.services.replay_engine import ReplayEngine

    db_session.add(ActionRecommendation(
        trade_date="2026-07-10",
        code="600584",
        name="通用回放",
        created_at=datetime(2026, 7, 10, 9, 35),
        level="INFO",
        state="OBSERVE",
        action="观察",
    ))
    db_session.commit()

    report = ReplayEngine(db_session).replay("600584", "2026-07-10")

    assert report.complete is True
    assert report.checkpoints == []
    assert len(report.frames) == 1


def test_calibration_proposal_requires_real_forward_outcomes(client, db_session):
    from app.models.trading import ExpectationSnapshot

    for index in range(20):
        db_session.add(ExpectationSnapshot(
            trade_date=f"2026-06-{index + 1:02d}",
            code=f"60{index:04d}",
            stage="盘中",
            base_expectation="STRONG",
            expectation_result="WEAKER" if index < 10 else "MATCHED",
        ))
    db_session.commit()

    response = client.get("/api/reviews/calibration-proposal")

    assert response.status_code == 200
    proposal = response.json()
    assert proposal["eligible"] is False
    assert proposal["sample_count"] == 0
    assert proposal["minimum_samples"] == 30
    assert proposal["changes"] == []
    assert "没有真实结果闭环，禁止校准" in proposal["rationale"]

    apply_response = client.post(
        "/api/reviews/calibration-apply",
        json={"confirmation": "APPLY_CALIBRATION"},
    )
    assert apply_response.status_code == 409
    assert "没有真实结果闭环，禁止校准" in apply_response.json()["detail"]


def test_expectation_confidence_is_explainable_evidence_coverage(db_session):
    from app.api.helpers.decision import _today, build_expectation_snapshot
    from app.models.trading import VolumePriceSnapshot

    db_session.add(VolumePriceSnapshot(
        trade_date=_today(),
        code="600904",
        name="完整证据",
        captured_at=datetime.now(),
        price=10.4,
        open_price=10.2,
        prev_close=10,
        vwap=10.25,
        vwap_reliable=True,
        pattern="量价健康",
        data_quality="realtime",
    ))
    db_session.commit()

    complete = build_expectation_snapshot(
        db_session,
        "600904",
        name="完整证据",
        stage="五分钟确认",
        quote={"price": 10.4, "open": 10.2, "prev_close": 10, "change_pct": 4},
        persist=False,
    )
    assert complete.confidence == 1
    assert sum("证据完整度·" in item for item in complete.evidence) == 5
    assert not any("证据完整度缺口" in item for item in complete.counter_evidence)

    partial = build_expectation_snapshot(
        db_session,
        "600905",
        name="仅有行情",
        stage="五分钟确认",
        quote={"price": 10.4, "open": 10.2, "prev_close": 10, "change_pct": 4},
        persist=False,
    )
    assert partial.confidence == 0.4
    assert any("没有同日/最近量价快照" in item for item in partial.counter_evidence)
    assert any("真实分时VWAP不可用" in item for item in partial.counter_evidence)


def test_next_day_baseline_coverage_uses_real_volume_fields(db_session, monkeypatch):
    from app.api.helpers.decision import _today
    from app.api.routes import stocks
    from app.models.trading import ExpectationSnapshot, Holding, VolumePriceSnapshot
    from app.services.next_day_expectations import generate_next_day_expectations

    monkeypatch.setattr(stocks, "watchlist_recommendations", lambda _db: [])
    db_session.add(Holding(
        code="600906", name="收盘完整证据", quantity=100, cost_price=10,
        current_price=10.5, total_asset=100_000, position_type="趋势持仓",
    ))
    db_session.add(VolumePriceSnapshot(
        trade_date=_today(),
        code="600906",
        name="收盘完整证据",
        captured_at=datetime.now(),
        price=10.5,
        open_price=10.1,
        prev_close=10,
        vwap=10.3,
        vwap_reliable=True,
        pattern="量价健康",
        data_quality="realtime",
    ))
    db_session.commit()

    assert generate_next_day_expectations(db_session) == 1
    snapshot = db_session.query(ExpectationSnapshot).filter_by(code="600906").one()
    assert snapshot.confidence == 1
    assert snapshot.counter_evidence_json == "[]"
    assert snapshot.evidence_json.count("证据完整度·") == 5
