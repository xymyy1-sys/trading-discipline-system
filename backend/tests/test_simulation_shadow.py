from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from app.models.trading import (
    ExpectationSnapshot,
    MarketRegimeSnapshot,
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
    AI_TRADER_AUTOMATION_KEY,
    RULE_VERSION,
    _autonomous_position_ratio,
    _entry_quantity,
    _ai_entry_confirmation_gate,
    _is_ai_trader_supported_code,
    _profit_protection_candidate,
    _hard_stop_candidate,
    _risk_adjusted_entry_ratio,
    mark_shadow_equity_after_close,
    run_shadow_experiments,
)


def _enable_ai_account(db_session, account, now: datetime):
    account.account_type = "shadow"
    account.automation_key = AI_TRADER_AUTOMATION_KEY
    db_session.add_all([
        account,
        MarketRegimeSnapshot(
            trade_date=now.date().isoformat(), captured_at=now - timedelta(seconds=20),
            source="test", data_quality="realtime", coverage_ratio=1.0,
            up_count=3000, down_count=1800, advance_ratio=0.62,
            regime_code="HEALTHY_EXPANSION",
        ),
    ])
    db_session.commit()
    return account


def test_frozen_hard_stop_exits_full_position_without_waiting_for_repair(db_session, monkeypatch):
    position = SimulationPosition(
        account_id=1, code="002345", name="潮宏基", quantity=500,
        available_quantity=500, average_cost=11.1001,
    )
    volume = VolumePriceSnapshot(
        id=9, trade_date="2026-08-07", code="002345", name="潮宏基",
        captured_at=datetime(2026, 8, 7, 14, 12), price=10.48,
        high_price=10.77, vwap=10.4759, vwap_reliable=True,
        data_quality="realtime",
    )
    monkeypatch.setattr(
        "app.services.simulation_shadow._frozen_position_stop",
        lambda _db, _position: (10.90, "入场冻结失效价10.90"),
    )
    candidate = _hard_stop_candidate(db_session, position, volume)
    assert candidate is not None
    assert candidate.side == "SELL"
    assert candidate.ratio == 1.0
    assert candidate.priority == 10_000.0
    assert "全部退出" in candidate.reason


def test_ai_entry_gate_rejects_fading_vwap_reclaim(db_session):
    now = datetime(2026, 8, 6, 9, 51)
    row = VolumePriceSnapshot(
        trade_date=now.date().isoformat(), code="002345", name="潮宏基",
        captured_at=now - timedelta(seconds=20), price=11.09, vwap=11.01,
        vwap_reliable=True, price_vs_vwap=0.73, minute_bar_count=22,
        active_buy_amount=3.0, active_sell_amount=2.0,
        volume_acceleration=-50.59, pattern="重新站回VWAP且低点抬高",
        data_quality="realtime",
    )
    db_session.add(row)
    db_session.commit()
    candidate = SimpleNamespace(code="002345", source_kind="expectation_volume_pair")
    allowed, reason = _ai_entry_confirmation_gate(db_session, candidate, now)
    assert allowed is False
    assert "量能加速度" in reason


def test_dynamic_profit_protection_uses_peak_retreat_and_vwap_weakness():
    position = SimulationPosition(
        account_id=1, code="600961", name="株冶集团", quantity=100,
        available_quantity=100, average_cost=26.13,
    )
    volume = VolumePriceSnapshot(
        id=1, trade_date="2026-08-06", code="600961", name="株冶集团",
        captured_at=datetime(2026, 8, 6, 13, 34), price=26.47,
        high_price=28.12, vwap=27.34, vwap_reliable=True,
        price_vs_vwap=-3.18, active_buy_amount=12.04,
        active_sell_amount=9.71, volume_acceleration=-58.63,
        pattern="冲高回落跌破VWAP", data_quality="realtime",
    )
    candidate = _profit_protection_candidate(position, volume)
    assert candidate is not None
    assert candidate.side == "SELL"
    assert candidate.ratio == 0.5
    assert "移动利润保护" in candidate.reason


def test_ai_trader_scope_only_allows_shanghai_shenzhen_main_boards():
    assert _is_ai_trader_supported_code("600584") is True
    assert _is_ai_trader_supported_code("000725") is True
    assert _is_ai_trader_supported_code("002371") is True
    assert _is_ai_trader_supported_code("300697") is False
    assert _is_ai_trader_supported_code("688981") is False
    assert _is_ai_trader_supported_code("920001") is False
    assert _is_ai_trader_supported_code("588710") is False


def test_risk_budget_caps_high_conviction_position():
    ratio, distance, inferred = _risk_adjusted_entry_ratio(10.0, 0.70, 9.0, 0.03)
    assert round(ratio, 4) == 0.30
    assert round(distance, 4) == 0.10
    assert inferred is False


def test_missing_stop_uses_conservative_distance():
    ratio, distance, inferred = _risk_adjusted_entry_ratio(10.0, 0.70, 0.0, 0.03)
    assert round(ratio, 4) == 0.375
    assert distance == 0.08
    assert inferred is True


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


def test_conviction_sizing_uses_meaningful_capital_and_reserves_multi_name_capacity(db_session):
    account = create_account(db_session, SimulationAccountCreate(initial_cash=20_000))
    ratio, tier = _autonomous_position_ratio(91, 1)
    assert (ratio, tier) == (0.70, "主攻仓")
    assert _entry_quantity(account, 10, ratio) == 1400

    diversified_ratio, diversified_tier = _autonomous_position_ratio(91, 3)
    assert diversified_tier == "主攻仓"
    assert round(diversified_ratio, 4) == round(0.85 / 3, 4)
    assert _entry_quantity(account, 10, diversified_ratio) == 500


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
    assert order.quantity == 5000
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


def test_ai_limit_up_plan_can_enter_before_0950(db_session):
    now = datetime(2026, 8, 10, 9, 35)
    account = _enable_ai_account(
        db_session,
        create_account(db_session, SimulationAccountCreate(initial_cash=100000)),
        now,
    )
    db_session.add_all([
        NextDayPlan(
            plan_date=now.date().isoformat(), plan_type="limit_up_auction",
            code="600003", name="早盘打板", limit_up_price=11,
            auction_plan='{"max_position_ratio": 0.10, "auto_generated": true}',
            updated_at=now - timedelta(minutes=1),
        ),
        VolumePriceSnapshot(
            trade_date=now.date().isoformat(), code="600003", name="早盘打板",
            captured_at=now - timedelta(seconds=20), price=11, vwap=10.5,
            vwap_reliable=True, price_vs_vwap=4.76, volume_acceleration=30,
            pattern="放量上涨突破", data_quality="realtime", data_source="test",
        ),
    ])
    db_session.commit()
    result = run_shadow_experiments(
        db_session, account, now=now,
        quote_loader=lambda _: _quote(11, now, limit_up_price=11, ask1_volume=50000),
    )
    assert len(result.order_ids) == 1
    assert db_session.get(SimulationOrder, result.order_ids[0]).strategy_source == "limit_up"


def test_ai_pullback_reclaim_uses_two_stage_entry_and_blocks_third(db_session):
    now = datetime(2026, 8, 10, 10, 5)
    account = _enable_ai_account(
        db_session,
        create_account(db_session, SimulationAccountCreate(initial_cash=100000)),
        now,
    )
    expectation = ExpectationSnapshot(
        trade_date=now.date().isoformat(), code="600001", name="回踩样本",
        stage="第一阶段确认", expectation_gap_score=12,
        expectation_result="STRONGER_THAN_EXPECTED", state_transition="MATCHED_TO_STRONGER",
        created_at=now - timedelta(minutes=2),
    )

    def add_volume(moment: datetime, row_id: int | None = None):
        row = VolumePriceSnapshot(
            trade_date=now.date().isoformat(), code="600001", name="回踩样本",
            captured_at=moment, price=10, vwap=9.98, vwap_reliable=True,
            price_vs_vwap=0.20, minute_bar_count=30, active_buy_amount=12,
            active_sell_amount=10, volume_acceleration=5, attack_efficiency=0.5,
            pattern="回踩不破重新站回VWAP", data_quality="realtime", data_source="test",
        )
        if row_id is not None:
            row.id = row_id
        db_session.add(row)
        db_session.commit()

    db_session.add(expectation)
    db_session.commit()
    add_volume(now - timedelta(seconds=20))
    first = run_shadow_experiments(
        db_session, account, now=now, quote_loader=lambda _: _quote(10, now),
    )
    assert len(first.order_ids) == 1
    first_order = db_session.get(SimulationOrder, first.order_ids[0])
    assert first_order.quantity > 0
    assert "回踩试探仓" in first_order.client_note
    process_open_orders(
        db_session, account, now=now + timedelta(minutes=1),
        quote_loader=lambda _: _quote(10.01, now + timedelta(minutes=1)),
    )

    second_at = now + timedelta(minutes=2)
    add_volume(second_at - timedelta(seconds=10))
    second = run_shadow_experiments(
        db_session, account, now=second_at,
        quote_loader=lambda _: _quote(10.05, second_at),
    )
    assert len(second.order_ids) == 1
    second_order = db_session.get(SimulationOrder, second.order_ids[0])
    assert second_order.quantity > 0
    assert "第二段确认仓" in second_order.client_note
    process_open_orders(
        db_session, account, now=second_at + timedelta(minutes=1),
        quote_loader=lambda _: _quote(10.06, second_at + timedelta(minutes=1)),
    )

    third_at = now + timedelta(minutes=4)
    add_volume(third_at - timedelta(seconds=10))
    third = run_shadow_experiments(
        db_session, account, now=third_at,
        quote_loader=lambda _: _quote(10.08, third_at),
    )
    assert third.order_ids == []
    assert any("最多两段建仓" in item["reason"] for item in third.skipped)


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
