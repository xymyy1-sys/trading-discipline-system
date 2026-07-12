from types import SimpleNamespace

from app.api.helpers.decision import build_expectation_snapshot, build_t_eligibility, create_t_plan
from app.api.helpers.plan_calc import _default_next_day_plan, refresh_limit_expectation_stage
from app.api.helpers.volume_price import build_volume_price_snapshot
from app.models.trading import ExpectationRule, Holding, TTradePlan
from app.schemas.trading import TTradePlanUpdate
from app.services.t_trading_engine import update_t_plan


def _execution_plan(db_session):
    plan = TTradePlan(
        holding_id=1,
        trade_date="2026-07-12",
        code="600999",
        name="T execution",
        t_type="POSITIVE_T",
        planned_sell_price=10.5,
        planned_sell_quantity=500,
        status="planned",
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def test_expectation_snapshot_uses_editable_threshold_rule(db_session):
    db_session.add(ExpectationRule(
        script_type="default",
        stage="*",
        base_expectation="STRONG",
        display_name="custom strong",
        expected_open_low=3,
        expected_open_high=6,
        outperform_threshold=7,
        underperform_threshold=2,
        severe_underperform_threshold=0,
        enabled=True,
    ))
    db_session.commit()
    snapshot = build_expectation_snapshot(
        db_session,
        "600101",
        quote={"price": 10.3, "prev_close": 10, "open": 10.3, "change_pct": 3},
        base_hint="强预期 主线前排",
    )
    assert snapshot.expected_open_low == 3
    assert snapshot.outperform_threshold == 7


def test_t_execution_feedback_enforces_quantity_guardrail(db_session):
    plan = _execution_plan(db_session)
    try:
        update_t_plan(db_session, plan, TTradePlanUpdate(actual_sell_price=10.5, actual_sell_quantity=600))
    except ValueError as exc:
        assert "exceeds" in str(exc)
    else:
        raise AssertionError("selling beyond the guarded quantity must fail")


def test_t_execution_feedback_tracks_sell_and_buyback_lifecycle(db_session):
    plan = _execution_plan(db_session)
    sold = update_t_plan(db_session, plan, TTradePlanUpdate(actual_sell_price=10.8, actual_sell_quantity=500))
    assert sold.status == "sold_wait_buyback"
    partial = update_t_plan(
        db_session,
        plan,
        TTradePlanUpdate(actual_buyback_price=10.2, actual_buyback_quantity=200),
    )
    assert partial.status == "partially_bought_back"
    completed = update_t_plan(
        db_session,
        plan,
        TTradePlanUpdate(actual_buyback_price=10.1, actual_buyback_quantity=500),
    )
    assert completed.status == "completed"
    assert completed.cost_reduction == 350


def test_expectation_snapshot_marks_underperform(db_session):
    snapshot = build_expectation_snapshot(
        db_session,
        "600000",
        name="预期测试",
        quote={"price": 9.5, "prev_close": 10, "open": 9.4, "change_pct": -5},
        base_hint="强预期 主线前排",
    )

    assert snapshot.base_expectation == "STRONG"
    assert snapshot.expectation_result in {"WEAKER", "INVALID"}
    assert snapshot.expectation_gap_score < 0
    assert "禁止补仓" in snapshot.suggestion or "降风险" in snapshot.suggestion


def test_t_eligibility_forbids_invalid_trade(db_session):
    holding = Holding(
        code="600001",
        name="禁T测试",
        quantity=1000,
        cost_price=10,
        current_price=9.2,
        total_asset=100000,
        position_type="打板仓",
        next_discipline="证伪退出",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    eligibility = build_t_eligibility(db_session, holding)

    assert eligibility.eligible is False
    assert eligibility.t_type == "NO_T"
    assert eligibility.forbidden_reasons


def test_create_positive_t_plan_when_eligible(db_session):
    holding = Holding(
        code="600002",
        name="正T测试",
        quantity=2000,
        cost_price=10,
        current_price=10.8,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="允许小比例正T",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    plan = create_t_plan(db_session, holding)

    assert plan.t_type in {"POSITIVE_T", "REVERSE_T", "NO_T"}
    if plan.t_type in {"POSITIVE_T", "REVERSE_T"}:
        assert plan.planned_sell_quantity > 0
        assert plan.buyback_conditions
    else:
        assert plan.status == "forbidden"


def test_inverse_t_requires_profit_protection_and_reversal_setup(db_session, monkeypatch):
    holding = Holding(
        code="600016",
        name="倒T测试",
        quantity=2000,
        cost_price=10,
        current_price=11,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="利润保护后允许倒T",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    execution = SimpleNamespace(
        sellable_quantity=2000,
        today_buy_quantity=0,
        yesterday_quantity=2000,
        state="PROFIT_PROTECTION",
        t_eligible=True,
        profit_snapshot=SimpleNamespace(current_profit_pct=10, protection_level="LEVEL_4"),
        data_quality="realtime",
        volume_price_state="HIGH_DRAWDOWN",
        evidence=["高点回撤 3.20%，利润回撤进入保护区。"],
        recommended_action="继续持有",
        structure_stop_price=10.4,
    )
    monkeypatch.setattr("app.services.t_trading_engine.build_position_execution_state", lambda db, row, **kwargs: execution)

    eligibility = build_t_eligibility(db_session, holding)

    assert eligibility.eligible is True
    assert eligibility.t_type == "REVERSE_T"
    assert eligibility.suggested_sell_price == 11
    assert any("先卖出计划数量" in item for item in eligibility.buyback_conditions)


def test_inverse_t_payload_is_normalized_to_reverse_t(db_session, monkeypatch):
    holding = Holding(
        code="600018",
        name="兼容倒T",
        quantity=2000,
        cost_price=10,
        current_price=11,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="兼容旧命名",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    execution = SimpleNamespace(
        sellable_quantity=2000,
        today_buy_quantity=0,
        yesterday_quantity=2000,
        state="PROFIT_PROTECTION",
        t_eligible=True,
        profit_snapshot=SimpleNamespace(current_profit_pct=10, protection_level="LEVEL_4"),
        data_quality="realtime",
        volume_price_state="HIGH_DRAWDOWN",
        evidence=["高点回撤 3.20%，利润回撤进入保护区。"],
        recommended_action="继续持有",
        structure_stop_price=10.4,
    )
    monkeypatch.setattr("app.services.t_trading_engine.build_position_execution_state", lambda db, row, **kwargs: execution)

    plan = create_t_plan(db_session, holding)

    assert plan.t_type == "REVERSE_T"


def test_expectation_snapshot_supports_extreme_and_ebb_vocab(db_session):
    extreme = build_expectation_snapshot(
        db_session,
        "600024",
        name="极强预期",
        quote={"price": 10.8, "prev_close": 10, "open": 10.7, "change_pct": 9},
        base_hint="极强 核心总龙",
    )
    ebb = build_expectation_snapshot(
        db_session,
        "600025",
        name="退潮预期",
        quote={"price": 9.4, "prev_close": 10, "open": 9.5, "change_pct": -6},
        base_hint="退潮 禁止接力",
    )

    assert extreme.base_expectation == "EXTREME_STRONG"
    assert extreme.expectation_result in {"MATCHED", "STRONGER"}
    assert ebb.base_expectation == "EBB"
    assert ebb.expectation_result in {"MATCHED", "WEAKER", "INVALID", "STRONGER"}


def test_volume_price_snapshot_detects_vwap_breakdown(db_session):
    snapshot = build_volume_price_snapshot(
        db_session,
        "600003",
        name="量价测试",
        quote={
            "price": 9.5,
            "change_pct": -4.0,
            "open": 10.1,
            "prev_close": 10.0,
            "high": 10.8,
            "low": 9.4,
            "amount": 12.0,
            "volume": 100_000_000,
            "minute_bars": [
                {"price": 10.8, "volume": 1000, "amount": 10800},
                {"price": 10.6, "volume": 1000, "amount": 10600},
                {"price": 10.75, "volume": 1000, "amount": 10750},
            ],
            "turnover": 8.2,
            "turnover_source": "eastmoney_f8_free_float",
            "turnover_reliable": True,
            "float_cap": 128.6,
            "note": "东方财富实时行情",
        },
    )

    assert snapshot.pattern in {"冲高回落跌破VWAP", "跌破VWAP"}
    assert snapshot.price_vs_vwap < 0
    assert snapshot.high_drawdown > 10
    assert snapshot.data_quality == "realtime"
    assert snapshot.vwap_source == "minute"
    assert snapshot.vwap_reliable is True
    assert snapshot.turnover_reliable is True
    assert snapshot.turnover_source == "eastmoney_f8_free_float"
    assert snapshot.float_cap == 128.6
    assert any("流通盘口径" in item for item in snapshot.evidence)
    assert snapshot.evidence


def test_volume_price_snapshot_calculates_minute_flow_metrics(db_session):
    snapshot = build_volume_price_snapshot(
        db_session,
        "600017",
        name="分钟量能",
        quote={
            "price": 10.6,
            "change_pct": 6,
            "open": 10.0,
            "prev_close": 10.0,
            "high": 10.9,
            "low": 9.9,
            "amount": 6.0,
            "volume": 60_000_000,
            "minute_bars": [
                {"price": 10.1, "volume": 10_000_000, "amount": 101_000_000, "active_buy_amount": 80_000_000, "active_sell_amount": 21_000_000},
                {"price": 10.4, "volume": 13_000_000, "amount": 135_200_000, "active_buy_amount": 90_000_000, "active_sell_amount": 45_200_000},
                {"price": 10.8, "volume": 18_000_000, "amount": 194_400_000, "active_buy_amount": 150_000_000, "active_sell_amount": 44_400_000},
                {"price": 10.6, "volume": 9_000_000, "amount": 95_400_000, "active_buy_amount": 25_000_000, "active_sell_amount": 70_400_000},
            ],
            "turnover": 4.5,
            "note": "东方财富实时行情",
        },
    )

    assert snapshot.active_buy_amount > snapshot.active_sell_amount
    assert snapshot.volume_acceleration > 0
    assert snapshot.attack_amount > 0
    assert snapshot.pullback_amount > 0
    assert snapshot.attack_amount < 10
    assert snapshot.pullback_amount < 10
    assert snapshot.pullback_amount_ratio > 0
    assert snapshot.pullback_sell_ratio > 70
    assert any("分钟主动买卖额" in item for item in snapshot.evidence)
    assert any("上攻段成交额" in item for item in snapshot.evidence)


def test_decision_card_includes_volume_price(client, monkeypatch):
    quote = {
        "price": 10.5,
        "change_pct": 2.0,
        "open": 10.1,
        "prev_close": 10.0,
        "high": 10.8,
        "low": 10.0,
        "amount": 8.0,
        "volume": 80_000_000,
        "turnover": 6.5,
        "note": "东方财富实时行情",
    }

    monkeypatch.setattr("app.api.helpers.decision.quote_for_code", lambda code: quote)

    response = client.get("/api/stocks/600004/decision-card")

    assert response.status_code == 200
    payload = response.json()
    assert payload["volume_price"]["code"] == "600004"
    assert payload["volume_price"]["pattern"]


def test_expectation_create_and_update_routes(client, monkeypatch):
    quote = {
        "price": 9.8,
        "change_pct": -2.0,
        "open": 9.7,
        "prev_close": 10.0,
        "high": 10.0,
        "low": 9.6,
        "amount": 5.0,
        "note": "东方财富实时行情",
    }

    monkeypatch.setattr("app.api.helpers.decision.quote_for_code", lambda code: quote)

    created = client.post(
        "/api/expectations",
        json={"code": "600005", "name": "预期路由", "base_hint": "强预期 主线前排", "stage": "开盘确认"},
    )

    assert created.status_code == 200
    snapshot_id = created.json()["id"]

    updated = client.put(
        f"/api/expectations/{snapshot_id}",
        json={"stage": "五分钟确认", "suggestion": "人工校准后先降风险。", "evidence": ["五分钟承接不足。"]},
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["stage"] == "五分钟确认"
    assert payload["suggestion"] == "人工校准后先降风险。"
    assert payload["evidence"] == ["五分钟承接不足。"]


def test_stage_refresh_writes_auction_checks(db_session, monkeypatch):
    holding = Holding(
        code="600008",
        name="阶段验收",
        quantity=1000,
        cost_price=10,
        current_price=10.6,
        total_asset=100000,
        position_type="打板仓 强预期",
        next_discipline="按竞价开盘量价确认",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    plan = _default_next_day_plan(
        holding,
        "2026-07-13",
        100000,
        {
            "price": 9.8,
            "change_pct": -2.0,
            "open": 9.7,
            "prev_close": 10.0,
            "high": 10.9,
            "low": 9.6,
            "amount": 7.0,
            "volume": 70_000_000,
            "turnover": 6.2,
            "note": "东方财富实时行情",
        },
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    quote = {
        "price": 9.8,
        "change_pct": -2.0,
        "open": 9.7,
        "prev_close": 10.0,
        "high": 10.9,
        "low": 9.6,
        "amount": 7.0,
        "volume": 70_000_000,
        "turnover": 6.2,
        "note": "东方财富实时行情",
    }

    monkeypatch.setattr("app.api.helpers.decision.quote_for_code", lambda code: quote)

    refreshed = refresh_limit_expectation_stage(plan, db_session)

    assert refreshed.auction_plan.current_stage
    assert refreshed.auction_plan.stage_decision
    assert len(refreshed.auction_plan.stage_checks) == 6
    assert any(item.stage == "五分钟量价确认" for item in refreshed.auction_plan.stage_checks)
    assert refreshed.auction_plan.action_ladder


def test_stage_refresh_route(client, monkeypatch):
    quote = {
        "price": 10.9,
        "change_pct": 9.0,
        "open": 10.3,
        "prev_close": 10.0,
        "high": 11.0,
        "low": 10.2,
        "amount": 9.0,
        "volume": 90_000_000,
        "turnover": 8.2,
        "note": "东方财富实时行情",
    }
    monkeypatch.setattr("app.api.helpers.decision.quote_for_code", lambda code: quote)
    created = client.post(
        "/api/next-day-plans",
        json={
            "plan_date": "2026-07-13",
            "plan_type": "limit_up_auction",
            "code": "600009",
            "name": "阶段路由",
            "cost_price": 10,
            "current_price": 10.9,
            "holding_category": "强预期",
            "confirm_price": 10.5,
            "limit_up_price": 11,
        },
    )
    assert created.status_code == 200

    response = client.post(f"/api/next-day-plans/{created.json()['id']}/stage-refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["auction_plan"]["stage_checks"]
    assert payload["auction_plan"]["stage_decision"]
