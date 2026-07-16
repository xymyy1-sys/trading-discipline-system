from datetime import datetime, timedelta
import json

from app.models.trading import (
    ExpectationSnapshot,
    NextDayPlan,
    PositionExecutionState,
    SimulationOrder,
    SimulationPosition,
    SimulationShadowDecision,
    VolumePriceSnapshot,
)
from app.schemas.simulation import SimulationAccountCreate
from app.services.simulation import create_account, process_open_orders
from app.services.simulation_shadow import (
    RULE_VERSION,
    mark_shadow_equity_after_close,
    run_shadow_experiments,
)


def _quote(price: float, when: datetime, **extra):
    payload = {
        "name": "测试股份",
        "price": price,
        "prev_close": 10,
        "open": 10,
        "high": max(price, 10),
        "low": min(price, 10),
        "note": "东方财富实时行情",
        "provider_event_at": when,
    }
    payload.update(extra)
    return payload


def _create_shadow_account(db_session, *, name: str):
    account = create_account(
        db_session,
        SimulationAccountCreate(name=name, initial_cash=100000),
    )
    account.account_type = "shadow"
    account.automation_key = f"test-shadow-{account.id}"
    db_session.commit()
    return account


def _positive_pair(db_session, now: datetime, code: str = "600001"):
    expectation = ExpectationSnapshot(
        trade_date=now.date().isoformat(),
        code=code,
        name="测试股份",
        stage="第一阶段确认",
        expectation_gap_score=12,
        expectation_result="STRONGER_THAN_EXPECTED",
        state_transition="MATCHED_TO_STRONGER",
        evidence_json='["竞价超预期"]',
        created_at=now - timedelta(minutes=2),
    )
    volume = VolumePriceSnapshot(
        trade_date=now.date().isoformat(),
        code=code,
        name="测试股份",
        captured_at=now - timedelta(minutes=1),
        price=10,
        vwap=9.9,
        vwap_reliable=True,
        price_vs_vwap=1.01,
        volume_acceleration=20,
        attack_efficiency=0.6,
        pattern="放量上涨突破VWAP",
        data_quality="realtime",
        data_source="test",
        evidence_json='["放量站稳分时均价"]',
    )
    db_session.add_all([expectation, volume])
    db_session.commit()
    return expectation, volume


def test_shadow_positive_expectation_creates_one_idempotent_order_and_freezes_versions(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    expectation, volume = _positive_pair(db_session, now)

    first = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(10, now),
    )
    assert len(first.order_ids) == 1
    order = db_session.get(SimulationOrder, first.order_ids[0])
    assert order.status == "OPEN"
    assert order.strategy_source == "expectation_volume_price"
    assert order.quantity == 1000
    assert "shadow:" in order.client_note

    decision = db_session.query(SimulationShadowDecision).one()
    assert decision.status == "ORDER_CREATED"
    assert decision.rule_version == RULE_VERSION
    assert decision.source_version == f"e{expectation.id}:v{volume.id}"
    assert decision.order_id == order.id
    assert "竞价超预期" in json.loads(decision.evidence_json)

    second = run_shadow_experiments(
        db_session,
        account,
        now=now + timedelta(seconds=20),
        quote_loader=lambda _: _quote(10.01, now + timedelta(seconds=20)),
    )
    assert second.order_ids == []
    assert len(second.duplicate_signal_keys) == 1
    assert db_session.query(SimulationOrder).count() == 1
    assert db_session.query(SimulationShadowDecision).count() == 1

    process_open_orders(
        db_session,
        account,
        now=now + timedelta(minutes=1),
        quote_loader=lambda _: _quote(10.1, now + timedelta(minutes=1)),
    )
    db_session.refresh(order)
    assert order.status == "FILLED"
    # The decision snapshot remains the one frozen when the signal fired.
    assert order.decision_evidence_snapshot_id is not None


def test_shadow_skips_unconfirmed_or_stale_signal_without_creating_order(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    expectation = ExpectationSnapshot(
        trade_date=now.date().isoformat(),
        code="600001",
        name="测试股份",
        stage="盘中确认",
        expectation_gap_score=10,
        expectation_result="STRONGER",
        state_transition="STRONGER",
        created_at=now - timedelta(minutes=1),
    )
    volume = VolumePriceSnapshot(
        trade_date=now.date().isoformat(),
        code="600001",
        name="测试股份",
        captured_at=now - timedelta(minutes=19),
        pattern="量价中性",
        data_quality="manual",
        data_source="manual",
    )
    db_session.add_all([expectation, volume])
    db_session.commit()

    result = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(10, now),
    )
    assert result.order_ids == []
    assert result.skipped
    assert db_session.query(SimulationOrder).count() == 0
    decision = db_session.query(SimulationShadowDecision).one()
    assert decision.status == "SKIPPED"
    assert "量价快照已陈旧" in decision.reason


def test_shadow_execution_exit_uses_sellable_quantity_and_is_idempotent(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    position = SimulationPosition(
        account_id=account.id,
        code="600002",
        name="退出样本",
        quantity=300,
        available_quantity=300,
        average_cost=10,
        last_rollover_date=now.date().isoformat(),
    )
    state = PositionExecutionState(
        holding_id=1,
        code="600002",
        name="退出样本",
        trade_date=now.date().isoformat(),
        state="REDUCE_REQUIRED",
        recommended_action="减仓50%",
        recommended_reduce_ratio=0.5,
        evidence_json='["跌破VWAP 9.80", "板块转弱 -2.00%"]',
        data_quality="realtime",
        data_time="10:04",
        updated_at=now - timedelta(minutes=1),
    )
    db_session.add_all([position, state])
    db_session.commit()

    first = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(9.8, now),
    )
    assert len(first.order_ids) == 1
    order = db_session.get(SimulationOrder, first.order_ids[0])
    # A-share partial exits are rounded down to whole lots.
    assert order.side == "SELL"
    assert order.quantity == 100

    second = run_shadow_experiments(
        db_session,
        account,
        now=now + timedelta(seconds=30),
        quote_loader=lambda _: _quote(9.8, now + timedelta(seconds=30)),
    )
    assert second.order_ids == []
    assert db_session.query(SimulationOrder).count() == 1

    # The collector refreshes updated_at every minute even when the semantic
    # state is unchanged.  That must not create a second sell decision.
    state.updated_at = now + timedelta(seconds=40)
    state.trailing_stop_price = 9.72
    state.evidence_json = '["跌破VWAP 9.72", "板块转弱 -2.35%"]'
    db_session.add(state)
    db_session.commit()
    third = run_shadow_experiments(
        db_session,
        account,
        now=now + timedelta(seconds=50),
        quote_loader=lambda _: _quote(9.8, now + timedelta(seconds=50)),
    )
    assert third.order_ids == []
    assert len(third.duplicate_signal_keys) == 1
    assert db_session.query(SimulationShadowDecision).count() == 1


def test_shadow_execution_quality_recovery_creates_one_new_executable_version(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    position = SimulationPosition(
        account_id=account.id,
        code="600003",
        name="quality-recovery",
        quantity=300,
        available_quantity=300,
        average_cost=10,
        last_rollover_date=now.date().isoformat(),
    )
    state = PositionExecutionState(
        holding_id=2,
        code="600003",
        name="quality-recovery",
        trade_date=now.date().isoformat(),
        state="REDUCE_REQUIRED",
        recommended_action="REDUCE 50%",
        recommended_reduce_ratio=0.5,
        evidence_json='["VWAP_BROKEN"]',
        data_quality="manual",
        data_time="10:04",
        updated_at=now - timedelta(minutes=1),
    )
    db_session.add_all([position, state])
    db_session.commit()

    skipped = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(9.8, now),
    )
    assert skipped.order_ids == []
    first_decision = db_session.query(SimulationShadowDecision).one()
    assert first_decision.status == "SKIPPED"

    state.data_quality = "realtime"
    state.updated_at = now + timedelta(seconds=10)
    db_session.add(state)
    db_session.commit()
    recovered = run_shadow_experiments(
        db_session,
        account,
        now=now + timedelta(seconds=20),
        quote_loader=lambda _: _quote(9.8, now + timedelta(seconds=20)),
    )
    assert len(recovered.order_ids) == 1
    decisions = db_session.query(SimulationShadowDecision).order_by(
        SimulationShadowDecision.id.asc()
    ).all()
    assert len(decisions) == 2
    assert decisions[1].status == "ORDER_CREATED"
    assert decisions[1].source_version != decisions[0].source_version

    # A later collector heartbeat with unchanged real-time semantics remains a
    # duplicate, even though updated_at changes.
    state.updated_at = now + timedelta(seconds=40)
    db_session.add(state)
    db_session.commit()
    repeated = run_shadow_experiments(
        db_session,
        account,
        now=now + timedelta(seconds=50),
        quote_loader=lambda _: _quote(9.8, now + timedelta(seconds=50)),
    )
    assert repeated.order_ids == []
    assert len(repeated.duplicate_signal_keys) == 1
    assert db_session.query(SimulationShadowDecision).count() == 2


def test_shadow_severe_expectation_invalidation_and_volume_breakdown_exit_all(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    db_session.add_all(
        [
            SimulationPosition(
                account_id=account.id,
                code="600004",
                name="证伪样本",
                quantity=200,
                available_quantity=200,
                average_cost=10,
                last_rollover_date=now.date().isoformat(),
            ),
            ExpectationSnapshot(
                trade_date=now.date().isoformat(),
                code="600004",
                name="证伪样本",
                stage="第一阶段确认",
                expectation_gap_score=-20,
                expectation_result="SEVERE_UNDERPERFORM",
                state_transition="EXPECTATION_INVALIDATED",
                created_at=now - timedelta(minutes=2),
            ),
            VolumePriceSnapshot(
                trade_date=now.date().isoformat(),
                code="600004",
                name="证伪样本",
                captured_at=now - timedelta(minutes=1),
                price=9.5,
                vwap=10,
                vwap_reliable=True,
                price_vs_vwap=-5,
                active_buy_amount=10,
                active_sell_amount=30,
                pattern="放量下跌跌破VWAP",
                data_quality="realtime",
                data_source="test",
            ),
        ]
    )
    db_session.commit()

    result = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(9.5, now),
    )
    assert len(result.order_ids) == 1
    order = db_session.get(SimulationOrder, result.order_ids[0])
    assert order.side == "SELL"
    assert order.quantity == 200
    decision = db_session.query(SimulationShadowDecision).one()
    assert decision.intent == "EXIT"
    assert "失效" in decision.reason


def test_shadow_limit_up_requires_touch_and_volume_confirmation(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    plan = NextDayPlan(
        plan_date=now.date().isoformat(),
        plan_type="limit_up_auction",
        code="600003",
        name="打板样本",
        limit_up_price=11,
        auction_plan='{"max_position_ratio": 0.05}',
        updated_at=now - timedelta(minutes=3),
    )
    volume = VolumePriceSnapshot(
        trade_date=now.date().isoformat(),
        code="600003",
        name="打板样本",
        captured_at=now - timedelta(seconds=30),
        price=11,
        vwap=10.5,
        vwap_reliable=True,
        price_vs_vwap=4.76,
        volume_acceleration=30,
        pattern="放量上涨突破",
        data_quality="realtime",
        data_source="test",
    )
    db_session.add_all([plan, volume])
    db_session.commit()

    result = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(11, now, limit_up_price=11, ask1_volume=0),
    )
    assert len(result.order_ids) == 1
    order = db_session.get(SimulationOrder, result.order_ids[0])
    assert order.strategy_source == "limit_up"
    assert order.quantity == 400
    # No same-bar fill is produced; later matching remains conservative at a sealed limit.
    assert order.status == "OPEN"


def test_shadow_limit_up_rejects_stale_plan_even_with_fresh_quote_and_volume(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 10, 5)
    db_session.add_all(
        [
            NextDayPlan(
                plan_date=now.date().isoformat(),
                plan_type="limit_up_auction",
                code="600005",
                name="陈旧预案",
                limit_up_price=11,
                auction_plan='{"max_position_ratio": 0.05}',
                updated_at=now - timedelta(hours=37),
            ),
            VolumePriceSnapshot(
                trade_date=now.date().isoformat(),
                code="600005",
                name="陈旧预案",
                captured_at=now - timedelta(seconds=30),
                price=11,
                vwap=10.5,
                vwap_reliable=True,
                price_vs_vwap=4.76,
                volume_acceleration=30,
                pattern="放量上涨突破",
                data_quality="realtime",
                data_source="test",
            ),
        ]
    )
    db_session.commit()

    result = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(11, now, limit_up_price=11),
    )
    assert result.order_ids == []
    assert "当日打板预案已陈旧" in result.skipped[0]["reason"]
    assert db_session.query(SimulationOrder).count() == 0


def test_shadow_after_close_equity_is_upserted_and_never_uses_previous_day_quote(db_session):
    first = _create_shadow_account(db_session, name="有效账户")
    second = _create_shadow_account(db_session, name="缺数账户")
    now = datetime(2026, 7, 16, 15, 5)
    db_session.add_all(
        [
            SimulationPosition(
                account_id=first.id,
                code="600001",
                name="测试股份",
                quantity=100,
                available_quantity=100,
                average_cost=10,
                last_rollover_date=now.date().isoformat(),
            ),
            SimulationPosition(
                account_id=second.id,
                code="600002",
                name="缺数股份",
                quantity=100,
                available_quantity=100,
                average_cost=10,
                last_rollover_date=now.date().isoformat(),
            ),
        ]
    )
    db_session.commit()

    def quote_loader(code: str):
        if code == "600001":
            return _quote(10.5, now.replace(hour=15, minute=0))
        return _quote(9.5, now - timedelta(days=1))

    first_run = mark_shadow_equity_after_close(db_session, now=now, quote_loader=quote_loader)
    assert len(first_run.equity_ids) == 1
    assert first_run.skipped[0]["account_id"] == str(second.id)

    second_run = mark_shadow_equity_after_close(
        db_session,
        now=now + timedelta(minutes=5),
        quote_loader=quote_loader,
    )
    assert second_run.equity_ids == first_run.equity_ids
    assert second_run.skipped[0]["account_id"] == str(second.id)
    assert db_session.query(SimulationShadowDecision).count() == 0


def test_shadow_close_equity_requires_near_close_non_future_quote(db_session):
    account = _create_shadow_account(db_session, name="收盘行情校验账户")
    now = datetime(2026, 7, 16, 15, 5)
    db_session.add(
        SimulationPosition(
            account_id=account.id,
            code="600001",
            name="测试股份",
            quantity=100,
            available_quantity=100,
            average_cost=10,
            last_rollover_date=now.date().isoformat(),
        )
    )
    db_session.commit()

    too_early = mark_shadow_equity_after_close(
        db_session,
        now=now,
        quote_loader=lambda _: _quote(10.5, now.replace(hour=14, minute=54)),
    )
    assert too_early.equity_ids == []
    assert "不回填历史净值" in too_early.skipped[0]["reason"]

    future = mark_shadow_equity_after_close(
        db_session,
        now=now,
        quote_loader=lambda _: _quote(10.5, now + timedelta(seconds=1)),
    )
    assert future.equity_ids == []
    assert "不回填历史净值" in future.skipped[0]["reason"]


def test_manual_account_missing_quote_does_not_block_shadow_close(db_session):
    manual = create_account(
        db_session,
        SimulationAccountCreate(name="手工模拟账户", initial_cash=100000),
    )
    shadow = _create_shadow_account(db_session, name="系统影子账户")
    now = datetime(2026, 7, 16, 15, 5)
    db_session.add_all(
        [
            SimulationPosition(
                account_id=manual.id,
                code="600001",
                name="手工账户缺数持仓",
                quantity=100,
                available_quantity=100,
                average_cost=10,
                last_rollover_date=now.date().isoformat(),
            ),
            SimulationPosition(
                account_id=shadow.id,
                code="600002",
                name="影子账户有效持仓",
                quantity=100,
                available_quantity=100,
                average_cost=10,
                last_rollover_date=now.date().isoformat(),
            ),
        ]
    )
    db_session.commit()

    def quote_loader(code: str):
        if code == "600001":
            raise AssertionError("后台影子封账不应读取手工账户行情")
        return _quote(10.5, now.replace(hour=15, minute=0))

    result = mark_shadow_equity_after_close(
        db_session,
        now=now,
        quote_loader=quote_loader,
    )

    assert len(result.equity_ids) == 1
    assert result.skipped == []


def test_shadow_never_creates_orders_outside_continuous_auction(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    now = datetime(2026, 7, 16, 9, 20)
    _positive_pair(db_session, now)
    result = run_shadow_experiments(
        db_session,
        account,
        now=now,
        quote_loader=lambda _: _quote(10, now),
    )
    assert result.order_ids == []
    assert db_session.query(SimulationOrder).count() == 0
    assert "连续竞价" in result.skipped[0]["reason"]


def test_shadow_decision_audit_endpoint_is_read_only_and_filterable(client, db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=100000))
    row = SimulationShadowDecision(
        account_id=account.id,
        signal_key="audit-test-key",
        strategy_source="holding_execution",
        source_kind="position_execution_state",
        source_id=7,
        rule_version=RULE_VERSION,
        source_version="7:2026-07-16T10:00:00",
        trade_date="2026-07-16",
        source_at=datetime(2026, 7, 16, 10, 0),
        evaluated_at=datetime(2026, 7, 16, 10, 1),
        code="600001",
        name="测试股份",
        intent="EXIT",
        side="SELL",
        quantity=100,
        status="SKIPPED",
        reason="T+1下当前没有可卖数量",
        evidence_json='["预期证伪"]',
    )
    db_session.add(row)
    db_session.commit()

    response = client.get(
        f"/api/simulation/accounts/{account.id}/shadow-decisions",
        params={"status": "SKIPPED"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["signal_key"] == "audit-test-key"
    assert payload[0]["reason"] == "T+1下当前没有可卖数量"
