from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config

from app.api.helpers import execution
from app.core.config import get_settings
from app.models.trading import (
    AccountState,
    ActionRecommendation,
    ActionRecommendationRevision,
    ExpectationRule,
    ExpectationSnapshot,
    Holding,
    IntradayEvidenceEvent,
    MarketRegimeSnapshot,
    PositionExecutionState,
    PositionStateHistory,
    ProfitProtectionSnapshot,
    RecommendationFeedback,
    RecommendationOutcome,
    TradeLog,
    VolumePriceSnapshot,
)
from app.services.recommendation_outcomes import (
    recommendation_outcome_summary,
    refresh_recommendation_outcomes,
)
from app.services.recommendation_feedback import recommendation_trade_side
from app.services import intraday_collector


def _holding(db_session, code: str = "600901") -> Holding:
    row = Holding(
        code=code,
        name="版本闭环测试",
        quantity=1000,
        cost_price=10,
        current_price=9.2,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="硬止损 9.60，跌破后退出",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _recommendation_with_revisions(db_session, *, revision_count: int = 2):
    now = datetime(2026, 7, 17, 10, 0)
    recommendation = ActionRecommendation(
        trade_date="2026-07-17",
        target_key="code:600902",
        code="600902",
        name="反馈版本测试",
        created_at=now,
        updated_at=now,
        level="WARNING",
        state="REDUCE_REQUIRED",
        action="减仓25%",
        recommended_ratio=0.25,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(recommendation)
    db_session.flush()
    revisions = []
    previous = None
    for version in range(1, revision_count + 1):
        created_at = now + timedelta(minutes=(version - 1) * 10)
        revision = ActionRecommendationRevision(
            recommendation_id=recommendation.id,
            previous_revision_id=previous.id if previous else None,
            version=version,
            decision_hash=f"hash-{version}",
            level="WARNING",
            state="REDUCE_REQUIRED",
            action="减仓25%",
            recommended_ratio=0.25,
            trigger_events_json='["VWAP_BROKEN"]',
            evidence_json="[]",
            counter_evidence_json="[]",
            invalid_conditions_json="[]",
            recovery_conditions_json="[]",
            decision_context_json=(
                '{"current_quantity":1000,"sellable_quantity":1000,'
                '"recommended_sell_quantity":250}'
            ),
            rule_version="execution-v2",
            created_at=created_at,
            effective_until=(created_at + timedelta(minutes=10)) if version < revision_count else None,
        )
        db_session.add(revision)
        db_session.flush()
        revisions.append(revision)
        previous = revision
    recommendation.current_revision_id = revisions[-1].id
    recommendation.current_decision_hash = revisions[-1].decision_hash
    db_session.commit()
    return recommendation, revisions


def test_execution_state_get_is_read_only_and_weekend_uses_latest_snapshot(
    client,
    db_session,
    monkeypatch,
):
    holding = _holding(db_session)
    state_time = datetime(2026, 7, 17, 14, 30)
    state = PositionExecutionState(
        holding_id=holding.id,
        code=holding.code,
        name=holding.name,
        trade_date="2026-07-17",
        state="NORMAL_HOLD",
        expectation_state="MATCHED",
        volume_price_state="VWAP_STRONG",
        sector_state="NEUTRAL",
        current_quantity=1000,
        sellable_quantity=1000,
        today_buy_quantity=0,
        yesterday_quantity=1000,
        current_position_ratio=0.1,
        recommended_position_ratio=0.1,
        recommended_action="继续持有",
        recommended_reduce_ratio=0,
        structure_stop_price=9.6,
        hard_stop_price=9.4,
        trailing_stop_price=9.8,
        profit_protection_price=9.9,
        evidence_json='["周五已采样"]',
        counter_evidence_json="[]",
        invalid_conditions_json="[]",
        recovery_conditions_json="[]",
        data_quality="realtime",
        data_time="2026-07-17 14:30",
        updated_at=state_time,
    )
    db_session.add(state)
    db_session.flush()
    recommendation = ActionRecommendation(
        trade_date="2026-07-17",
        target_key=f"holding:{holding.id}",
        holding_id=holding.id,
        code=holding.code,
        name=holding.name,
        created_at=state_time,
        updated_at=state_time,
        level="INFO",
        state="NORMAL_HOLD",
        action="继续持有",
        current_decision_hash="stable",
        expires_at=state_time + timedelta(minutes=15),
    )
    db_session.add(recommendation)
    db_session.flush()
    revision = ActionRecommendationRevision(
        recommendation_id=recommendation.id,
        version=1,
        decision_hash="stable",
        level="INFO",
        state="NORMAL_HOLD",
        action="继续持有",
        created_at=state_time,
    )
    db_session.add(revision)
    db_session.flush()
    recommendation.current_revision_id = revision.id
    db_session.add(ProfitProtectionSnapshot(
        holding_id=holding.id,
        code=holding.code,
        captured_at=datetime(2026, 7, 17, 6, 30),  # UTC-naive storage
        current_profit_pct=-8,
        maximum_profit_pct=2,
        profit_drawdown_pct=10,
        maximum_price=10.2,
        recommended_action="继续持有",
    ))
    db_session.add(ProfitProtectionSnapshot(
        holding_id=holding.id,
        code=holding.code,
        captured_at=datetime(2026, 7, 17, 7, 0),  # 15:00 CST: after the state
        current_profit_pct=99,
        maximum_profit_pct=99,
        profit_drawdown_pct=0,
        maximum_price=99,
        recommended_action="不得串入的未来快照",
    ))
    db_session.add(IntradayEvidenceEvent(
        trade_date="2026-07-17",
        captured_at=state_time,
        scope="stock",
        target_code=holding.code,
        target_name=holding.name,
        event_type="SUPPORT_HELD",
        evidence_json='["支撑有效"]',
        recommendation_id=recommendation.id,
    ))
    db_session.commit()

    monkeypatch.setattr(execution, "_trade_date", lambda: "2026-07-19")
    tracked_models = (
        PositionExecutionState,
        ProfitProtectionSnapshot,
        ActionRecommendation,
        ActionRecommendationRevision,
        IntradayEvidenceEvent,
        PositionStateHistory,
        RecommendationFeedback,
    )
    before_counts = {model: db_session.query(model).count() for model in tracked_models}
    before_times = (state.updated_at, recommendation.created_at, recommendation.updated_at)

    first = client.get("/api/holdings/execution-states?force_refresh=true")
    second = client.get("/api/holdings/execution-states")

    assert first.status_code == second.status_code == 200
    assert len(first.json()) == 1
    assert first.json()[0]["trade_date"] == "2026-07-17"
    assert first.json()[0]["data_quality"] == "stale"
    assert "历史快照" in first.json()[0]["data_time"]
    assert first.json()[0]["profit_snapshot"]["current_profit_pct"] == -8
    assert {model: db_session.query(model).count() for model in tracked_models} == before_counts
    db_session.refresh(state)
    db_session.refresh(recommendation)
    assert (state.updated_at, recommendation.created_at, recommendation.updated_at) == before_times


def test_decision_card_and_direct_snapshot_gets_are_side_effect_free(
    client,
    db_session,
    monkeypatch,
):
    from app.api.helpers import decision, quotes

    holding = _holding(db_session, "600904")
    now = datetime(2026, 7, 17, 10, 10)
    quote = {
        "name": holding.name,
        "price": 9.4,
        "change_pct": -1.05,
        "open": 9.5,
        "open_pct": 0.0,
        "prev_close": 9.5,
        "high": 9.6,
        "low": 9.2,
        "amount": 1.2,
        "volume": 12_000_000,
        "turnover": 1.5,
        "note": "东方财富实时行情",
        "minute_bar_trade_date": "2026-07-17",
        "minute_bars": [
            {"time": "10:08", "price": 9.3, "volume": 1000, "amount": 9300},
            {"time": "10:09", "price": 9.35, "volume": 1100, "amount": 10285},
            {"time": "10:10", "price": 9.4, "volume": 1200, "amount": 11280},
        ],
    }
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: now)
    monkeypatch.setattr(decision, "quote_for_code", lambda _code: quote)
    monkeypatch.setattr(quotes, "_daily_history_metrics", lambda _code: {})
    monkeypatch.setattr(
        decision,
        "_market_entry_context",
        lambda *_args, **_kwargs: (
            {
                "entry_gate": "BLOCK",
                "risk_level": "UNKNOWN",
                "regime": "MISSING",
                "data_quality": "missing",
                "expansion_frozen": True,
            },
            {"status": "数据不足"},
        ),
    )

    tracked_models = (
        AccountState,
        ExpectationRule,
        ExpectationSnapshot,
        VolumePriceSnapshot,
        PositionExecutionState,
        ProfitProtectionSnapshot,
        ActionRecommendation,
        ActionRecommendationRevision,
        IntradayEvidenceEvent,
        PositionStateHistory,
    )
    before_counts = {model: db_session.query(model).count() for model in tracked_models}

    card = client.get(f"/api/stocks/{holding.code}/decision-card")
    expectation = client.get(f"/api/stocks/{holding.code}/expectation")
    volume_price = client.get(f"/api/stocks/{holding.code}/volume-price")
    rules = client.get("/api/expectation-rules")

    assert card.status_code == expectation.status_code == volume_price.status_code == rules.status_code == 200
    assert card.json()["code"] == holding.code
    assert {model: db_session.query(model).count() for model in tracked_models} == before_counts


def test_same_semantic_decision_reuses_revision_and_keeps_created_at(db_session):
    holding = _holding(db_session, "600903")
    quote = {"price": 9.2, "high": 9.5, "low": 9.1, "open": 9.4, "note": "实时行情"}

    first = execution.build_position_execution_state(db_session, holding, quote=quote)
    recommendation = db_session.get(ActionRecommendation, first.recommendation.id)
    recommendation.created_at = datetime(2026, 1, 1, 9, 30)
    db_session.commit()
    second = execution.build_position_execution_state(db_session, holding, quote=quote)

    assert first.recommendation.decision_hash == second.recommendation.decision_hash
    assert db_session.query(ActionRecommendation).filter_by(holding_id=holding.id).count() == 1
    assert db_session.query(ActionRecommendationRevision).filter_by(
        recommendation_id=recommendation.id,
    ).count() == 1
    db_session.refresh(recommendation)
    assert recommendation.created_at == datetime(2026, 1, 1, 9, 30)


def test_explicit_add_position_action_matches_buy_side_even_with_positive_ratio(db_session):
    _, revisions = _recommendation_with_revisions(db_session, revision_count=1)
    revision = revisions[0]
    revision.action = "加仓25%"
    revision.recommended_ratio = 0.25

    assert recommendation_trade_side(revision) == "BUY"


def test_feedback_is_revision_scoped_idempotent_and_sell_direction_only(client, db_session):
    recommendation, revisions = _recommendation_with_revisions(db_session)
    current = revisions[-1]
    # TradeLog's normal model default is UTC-naive.  02:12 UTC is 10:12 CST.
    db_session.add_all([
        TradeLog(
            code=recommendation.code,
            name=recommendation.name,
            traded_at=datetime(2026, 7, 17, 2, 11),
            side="买入",
            price=10.1,
            quantity=500,
            amount=5050,
            total_asset=100000,
            position_ratio=0.05,
            cost_price=10,
            stop_loss_price=9.6,
            reason="反方向成交",
            mode="标准短线模式",
            compliant=True,
        ),
        TradeLog(
            code=recommendation.code,
            name=recommendation.name,
            traded_at=datetime(2026, 7, 17, 2, 12),
            side="卖出",
            price=10.2,
            quantity=300,
            amount=3060,
            total_asset=100000,
            position_ratio=0.03,
            cost_price=10,
            stop_loss_price=9.6,
            reason="按建议减仓",
            mode="标准短线模式",
            compliant=True,
        ),
    ])
    db_session.commit()
    payload = {
        "status": "已执行",
        "reason": "确认执行",
        "revision_id": current.id,
        "client_event_id": "feedback-event-1",
    }

    first = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json=payload,
    )
    retry = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json=payload,
    )

    assert first.status_code == retry.status_code == 200
    assert first.json()["id"] == retry.json()["id"]
    assert first.json()["recommendation_revision_id"] == current.id
    assert first.json()["executed_quantity"] == 300
    matched = db_session.get(TradeLog, first.json()["trade_id"])
    assert matched.side == "卖出"
    assert db_session.query(RecommendationFeedback).count() == 1

    # Feedback for V1 is auditable but must never appear as V2's status.
    old_feedback = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json={
            "status": "不同意",
            "revision_id": revisions[0].id,
            "client_event_id": "feedback-event-v1",
        },
    )
    assert old_feedback.status_code == 200
    # Delete current-version feedback to make leakage observable.
    db_session.query(RecommendationFeedback).filter(
        RecommendationFeedback.recommendation_revision_id == current.id,
    ).delete()
    recommendation.expires_at = datetime.now() + timedelta(days=1)
    db_session.commit()
    alerts = client.get("/api/alerts/active?include_acknowledged=true")
    assert alerts.status_code == 200
    current_alert = next(item for item in alerts.json() if item["id"] == recommendation.id)
    assert current_alert["revision_id"] == current.id
    assert current_alert["feedback_status"] == ""


def test_not_filled_never_matches_existing_trade(client, db_session):
    recommendation, revisions = _recommendation_with_revisions(db_session, revision_count=1)
    revision = revisions[0]
    db_session.add(TradeLog(
        code=recommendation.code,
        name=recommendation.name,
        traded_at=datetime(2026, 7, 17, 2, 5),
        side="卖出",
        price=10,
        quantity=300,
        amount=3000,
        total_asset=100000,
        position_ratio=0.03,
        cost_price=10,
        stop_loss_price=9.6,
        reason="窗口内已有成交",
        mode="标准短线模式",
        compliant=True,
    ))
    db_session.commit()

    response = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json={
            "status": "未成交",
            "revision_id": revision.id,
            "client_event_id": "not-filled-event",
        },
    )

    assert response.status_code == 200
    assert response.json()["trade_id"] is None
    assert response.json()["executed_quantity"] == 0
    assert response.json()["result"] == "明确未成交"


def test_feedback_submitted_before_trade_is_rematched_on_trade_create_and_update(client, db_session):
    recommendation, revisions = _recommendation_with_revisions(db_session, revision_count=1)
    revision = revisions[0]
    utc_now = datetime.now(timezone.utc).replace(tzinfo=None)
    local_now = utc_now + timedelta(hours=8)
    recommendation.trade_date = local_now.date().isoformat()
    recommendation.created_at = local_now - timedelta(minutes=2)
    recommendation.updated_at = local_now - timedelta(minutes=2)
    recommendation.expires_at = local_now + timedelta(minutes=20)
    revision.created_at = local_now - timedelta(minutes=2)
    revision.effective_until = local_now + timedelta(minutes=20)
    db_session.commit()

    feedback_response = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json={
            "status": "已执行",
            "revision_id": revision.id,
            "client_event_id": "feedback-before-trade",
        },
    )
    assert feedback_response.status_code == 200
    assert feedback_response.json()["trade_id"] is None
    assert feedback_response.json()["executed_quantity"] == 0
    assert feedback_response.json()["result"] == "待匹配成交"

    trade_response = client.post(
        "/api/trades",
        json={
            "code": recommendation.code,
            "name": recommendation.name,
            "side": "卖出",
            "price": 10.25,
            "quantity": 300,
            "total_asset": 100000,
            "cost_price": 10,
            "reason": "按反馈后补录成交",
            "mode": "标准短线模式",
            "compliant": True,
        },
    )
    assert trade_response.status_code == 200
    feedback = db_session.get(RecommendationFeedback, feedback_response.json()["id"])
    db_session.refresh(feedback)
    assert feedback.trade_id == trade_response.json()["id"]
    assert feedback.executed_quantity == 300
    assert feedback.executed_ratio == 0.3
    assert feedback.executed_price == 10.25

    update_response = client.put(
        f"/api/trades/{trade_response.json()['id']}",
        json={"quantity": 400, "price": 10.5},
    )
    assert update_response.status_code == 200
    db_session.refresh(feedback)
    assert feedback.executed_quantity == 400
    assert feedback.executed_ratio == 0.4
    assert feedback.executed_price == 10.5


def test_feedback_idempotency_distinguishes_explicit_zero_from_omitted_value(client, db_session):
    recommendation, revisions = _recommendation_with_revisions(db_session, revision_count=1)
    revision = revisions[0]
    db_session.add(TradeLog(
        code=recommendation.code,
        name=recommendation.name,
        traded_at=datetime(2026, 7, 17, 2, 5),
        side="卖出",
        price=10,
        quantity=300,
        amount=3000,
        total_asset=100000,
        position_ratio=0.03,
        cost_price=10,
        stop_loss_price=9.6,
        reason="窗口内成交",
        mode="标准短线模式",
        compliant=True,
    ))
    db_session.commit()
    payload = {
        "status": "已执行",
        "revision_id": revision.id,
        "client_event_id": "zero-versus-missing",
    }
    first = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json=payload,
    )
    omitted_retry = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json=payload,
    )
    explicit_zero_retry = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json={**payload, "executed_quantity": 0},
    )

    assert first.status_code == omitted_retry.status_code == 200
    assert first.json()["executed_quantity"] == 300
    assert explicit_zero_retry.status_code == 409


def test_feedback_requires_revision_id_after_recommendation_changes(client, db_session):
    recommendation, _ = _recommendation_with_revisions(db_session, revision_count=2)

    response = client.post(
        f"/api/recommendations/{recommendation.id}/execution-feedback",
        json={"status": "不同意", "client_event_id": "ambiguous-legacy-event"},
    )

    assert response.status_code == 422
    assert "revision_id" in response.json()["detail"]


def test_latest_feedback_per_revision_drives_statistics(client, db_session):
    recommendation, revisions = _recommendation_with_revisions(db_session, revision_count=1)
    revision = revisions[0]
    db_session.add(MarketRegimeSnapshot(
        trade_date=recommendation.trade_date,
        captured_at=datetime(2026, 7, 17, 14, 55),
        regime_code="NEUTRAL",
        regime_name="中性震荡",
        data_quality="complete",
    ))
    db_session.add_all([
        RecommendationFeedback(
            recommendation_id=recommendation.id,
            recommendation_revision_id=revision.id,
            status="已执行",
            status_code="executed",
            client_event_id="stats-old",
            created_at=datetime(2026, 7, 17, 10, 1),
        ),
        RecommendationFeedback(
            recommendation_id=recommendation.id,
            recommendation_revision_id=revision.id,
            status="不同意",
            status_code="rejected",
            client_event_id="stats-latest",
            created_at=datetime(2026, 7, 17, 10, 2),
        ),
    ])
    db_session.commit()

    environment = client.get("/api/reviews/environment-effectiveness")
    calibration = client.get("/api/review-calibration/summary")

    assert environment.status_code == calibration.status_code == 200
    bucket = next(item for item in environment.json() if item["market_grade"] == "中性震荡")
    assert bucket["execution_adoption_rate"] == 0
    assert calibration.json()["execution_feedback_count"] == 1
    assert calibration.json()["feedback_summary"] == [{"status": "不同意", "count": 1}]


def test_legacy_base_outcome_is_superseded_after_revision_exists(db_session):
    recommendation, _ = _recommendation_with_revisions(db_session, revision_count=1)
    base = RecommendationOutcome(
        source_key=f"recommendation:{recommendation.id}:base",
        recommendation_id=recommendation.id,
        recommendation_revision_id=None,
        trade_date=recommendation.trade_date,
        code=recommendation.code,
        name=recommendation.name,
        signal_at=recommendation.created_at,
        status="complete",
        data_quality="reliable",
        created_at=recommendation.created_at,
        updated_at=recommendation.created_at,
    )
    db_session.add(base)
    db_session.commit()

    refresh_recommendation_outcomes(
        db_session,
        now=datetime(2026, 7, 17, 15, 10),
        commit=False,
    )

    db_session.refresh(base)
    assert base.status == "invalid"
    assert base.data_quality == "superseded"
    assert "不可变建议版本" in base.invalid_reason
    summary = recommendation_outcome_summary(db_session)
    assert summary["eligible_sample_count"] == 0
    assert summary["minimum_calibration_samples"] == 30


def test_alembic_upgrades_empty_database_to_head(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-empty.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    try:
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()

    assert database_path.exists()


def test_alembic_backfills_duplicate_recommendations_and_revision_chain(tmp_path, monkeypatch):
    database_path = tmp_path / "migration-legacy.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    get_settings.cache_clear()
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    try:
        command.upgrade(config, "u1e5f6a7b8c9")
        connection = sqlite3.connect(database_path)
        recommendation_sql = (
            "INSERT INTO action_recommendations "
            "(trade_date,holding_id,code,name,created_at,level,state,action,recommended_ratio,"
            "trigger_events_json,evidence_json,counter_evidence_json,invalid_conditions_json,recovery_conditions_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        )
        values = (
            "2026-07-17", 1, "600999", "迁移重复样本", "2026-07-17 10:00:00",
            "WARNING", "REDUCE", "减仓25%", 0.25, "[]", "[]", "[]", "[]", "[]",
        )
        connection.execute(recommendation_sql, values)
        first_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            recommendation_sql,
            values[:4] + ("2026-07-17 10:05:00",) + values[5:],
        )
        second_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        # A row inserted later is not necessarily the latest decision.  The
        # migration must select the canonical row by decision time first and
        # only use the id as a deterministic tie-breaker.
        connection.execute(
            recommendation_sql,
            values[:4] + ("2026-07-17 09:55:00",) + values[5:],
        )
        late_inserted_old_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        revision_sql = (
            "INSERT INTO action_recommendation_revisions "
            "(recommendation_id,version,level,state,action,recommended_ratio,evidence_json,"
            "counter_evidence_json,invalid_conditions_json,recovery_conditions_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        )
        connection.execute(
            revision_sql,
            (second_id, 1, "WARNING", "REDUCE", "减仓25%", 0.25, "[]", "[]", "[]", "[]", "2026-07-17 10:05:00"),
        )
        first_revision_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            revision_sql,
            (second_id, 1, "CRITICAL", "EXIT", "全部退出", 1.0, "[]", "[]", "[]", "[]", "2026-07-17 10:10:00"),
        )
        second_revision_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO recommendation_feedback (recommendation_id,status,reason,created_at,result) "
            "VALUES (?,?,?,?,?)",
            (second_id, "已执行", "迁移反馈", "2026-07-17 10:06:00", "待匹配成交"),
        )
        connection.execute(
            "INSERT INTO recommendation_outcomes "
            "(source_key,recommendation_id,trade_date,code,name,signal_at,status,data_quality,"
            "missing_horizons_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"recommendation:{second_id}:base", second_id, "2026-07-17", "600999",
                "迁移重复样本", "2026-07-17 10:05:00", "complete", "reliable", "[]",
                "2026-07-17 15:00:00", "2026-07-17 15:00:00",
            ),
        )
        connection.commit()
        connection.close()

        command.upgrade(config, "head")
        connection = sqlite3.connect(database_path)
        recommendations = connection.execute(
            "SELECT id,target_key,current_revision_id FROM action_recommendations ORDER BY id"
        ).fetchall()
        revisions = connection.execute(
            "SELECT id,version,previous_revision_id,effective_until "
            "FROM action_recommendation_revisions ORDER BY id"
        ).fetchall()
        feedback = connection.execute(
            "SELECT recommendation_revision_id,status_code FROM recommendation_feedback"
        ).fetchone()
        outcome = connection.execute(
            "SELECT status,data_quality FROM recommendation_outcomes"
        ).fetchone()
        connection.close()
    finally:
        get_settings.cache_clear()

    assert recommendations == [
        (first_id, f"legacy:{first_id}", None),
        (second_id, "holding:1", second_revision_id),
        (late_inserted_old_id, f"legacy:{late_inserted_old_id}", None),
    ]
    assert revisions == [
        (first_revision_id, 1, None, "2026-07-17 10:10:00"),
        (second_revision_id, 2, first_revision_id, None),
    ]
    assert feedback == (first_revision_id, "executed")
    assert outcome == ("invalid", "superseded")


def test_concurrent_collector_request_is_skipped_without_entering_writer(db_session, monkeypatch):
    from app.models.trading import IntradayCollectionRun

    monkeypatch.setattr(intraday_collector, "SessionLocal", lambda: db_session)
    before_count = db_session.query(IntradayCollectionRun).count()
    assert intraday_collector._collector_guard.acquire(blocking=False)
    try:
        row = intraday_collector.run_intraday_collection_once("concurrent-test")
    finally:
        intraday_collector._collector_guard.release()

    assert row.status == "skipped"
    assert row.error_message == "already_running"
    assert "未重复写入" in row.notes_json
    assert row.id is None
    assert db_session.query(IntradayCollectionRun).count() == before_count
