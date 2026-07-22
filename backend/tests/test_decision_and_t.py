from datetime import datetime, timedelta
import json
from types import SimpleNamespace

from app.api.helpers.decision import (
    _entry_plan_context,
    _entry_plan_execution_context,
    _market_entry_context,
    build_expectation_snapshot,
    build_t_eligibility,
    create_t_plan,
    decision_market_data_status,
)
from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.api.helpers.plan_calc import _default_next_day_plan, refresh_limit_expectation_stage
from app.api.helpers.volume_price import _minute_reversal_signals, build_volume_price_snapshot
from app.models.trading import (
    DataCaptureSnapshot,
    ExpectationRule,
    Holding,
    MarketRegimeSnapshot,
    NextDayPlan,
    TTradePlan,
    VolumePriceSnapshot,
)
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


def test_entry_plan_context_accepts_mainline_limit_plan_max_position_ratio(db_session):
    plan = NextDayPlan(
        plan_date=shanghai_today().isoformat(),
        plan_type="limit_up_auction",
        code="600123",
        name="mainline plan",
        confirm_price=10.0,
        limit_up_price=11.0,
        final_risk_price=9.5,
        expected_condition="板块资金转强后 confirm retest",
        underperform_condition="cancel when support fails",
        auction_plan=json.dumps({
            "max_position_ratio": 0.05,
            "is_mainline": True,
            "keep_order_condition": "板块保持前排并 confirm retest support",
            "cancel_condition": "cancel when support fails",
        }),
    )
    db_session.add(plan)
    db_session.commit()

    has_plan, mode_match, loaded = _entry_plan_context(db_session, "600123", is_holding=False)

    assert loaded is not None
    assert has_plan is True
    assert mode_match is True

    condition_context = {
        "volume_price": {
            "vwap": 9.95,
            "vwap_reliable": True,
            "data_quality": "realtime",
            "pattern": "回踩不破后重新站回VWAP",
        },
        "sector_context": {
            "crowding_evaluated": True,
            "status": "健康趋势",
            "flow_turning": "INFLOW_ACCELERATING",
        },
        "market_context": {"entry_gate": "OPEN_WITH_DISCIPLINE", "expansion_frozen": False},
    }
    live = _entry_plan_execution_context(
        loaded, {"price": 10.0}, is_holding=False, **condition_context,
    )
    not_triggered = _entry_plan_execution_context(
        loaded, {"price": 9.8}, is_holding=False, **condition_context,
    )
    assert live["triggered"] is True
    assert live["risk_reward_passed"] is True
    assert live["risk_reward"] == 2.0
    assert live["position_cap_pct"] == 5.0
    assert not_triggered["triggered"] is False

    neutral_sector = dict(condition_context)
    neutral_sector["sector_context"] = {"crowding_evaluated": True, "status": "震荡中性"}
    condition_failed = _entry_plan_execution_context(
        loaded, {"price": 10.0}, is_holding=False, **neutral_sector,
    )
    assert condition_failed["triggered"] is False
    assert any("板块/题材" in item for item in condition_failed["evidence"])


def test_holding_buyback_quantity_is_converted_to_account_position_cap():
    plan = NextDayPlan(
        plan_date=shanghai_today().isoformat(),
        plan_type="holding",
        code="600124",
        name="holding plan",
        allow_buyback=True,
        buyback_price=10.0,
        max_buyback_quantity=100,
        trim_price=12.0,
        final_risk_price=9.0,
        expected_condition="板块未退潮，个股回踩分时均价有承接",
        underperform_condition="跌破确认位并弱于板块时取消",
        buyback_condition="全市场闸门开放、板块资金转强、V形低点抬高并站回真实VWAP、风险收益比不低于1.5",
        auction_plan=json.dumps({"cancel_condition": "板块转弱或跌破VWAP时取消买回"}),
    )
    context = _entry_plan_execution_context(
        plan,
        {"price": 10.0},
        is_holding=True,
        expectation={"expectation_gap_score": 0, "expectation_result": "MATCHED"},
        volume_price={
            "vwap": 9.95,
            "vwap_reliable": True,
            "data_quality": "realtime",
            "pattern": "V形低点抬高并重新站回VWAP",
        },
        sector_context={
            "crowding_evaluated": True,
            "status": "修复初步确认",
            "flow_turning": "INFLOW_ACCELERATING",
        },
        market_context={"entry_gate": "OPEN_WITH_DISCIPLINE", "expansion_frozen": False},
        account_total_asset=100_000,
    )

    assert context["triggered"] is True
    assert context["position_cap_pct"] == 1.0


def test_market_entry_context_blocks_same_day_expired_snapshot(db_session):
    db_session.add(MarketRegimeSnapshot(
        trade_date=shanghai_today().isoformat(),
        captured_at=shanghai_now_naive() - timedelta(minutes=16),
        data_quality="complete",
        regime_code="HEALTHY_ROTATION",
        regime_name="健康轮动",
        risk_level="LOW",
        forbidden_actions_json="[]",
        strongest_sectors_json="[]",
        weakest_sectors_json="[]",
    ))
    db_session.commit()

    market, _sector = _market_entry_context(db_session, {"industry": "", "concepts": []})

    assert market["entry_gate"] == "BLOCK"
    assert market["expansion_frozen"] is True
    assert market["data_quality"] == "stale"


def test_market_entry_context_does_not_build_temperature_on_cache_miss(db_session, monkeypatch):
    from app.api.helpers import decision
    from app.api.routes import market

    db_session.add(MarketRegimeSnapshot(
        trade_date=shanghai_today().isoformat(),
        captured_at=datetime.now(),
        data_quality="realtime",
        regime_code="HEALTHY_ROTATION",
        regime_name="健康轮动",
        risk_level="LOW",
        forbidden_actions_json="[]",
        strongest_sectors_json='[{"name":"半导体"}]',
        weakest_sectors_json="[]",
    ))
    db_session.commit()
    calls = []
    monkeypatch.setattr(decision, "_get_response_cache", lambda _key: None)
    monkeypatch.setattr(
        market,
        "_sector_temperature_snapshot",
        lambda board_type, force_refresh=False: calls.append(board_type) or {
            "items": [{
                "name": "半导体",
                "status": "过热分歧",
                "heat_score": 82,
                "flow_turning": "INFLOW_FADING",
                "data_quality": "good",
                "provider_trade_date": shanghai_today().isoformat(),
            }]
        },
    )

    _market, sector = _market_entry_context(
        db_session,
        {"industry": "半导体", "concepts": []},
    )

    assert calls == []
    assert sector["crowding_evaluated"] is False
    assert sector["temperature_data_quality"] == "missing"
    assert sector.get("overheated") is not True
    assert sector["flow_turning"] == "INFLOW_ACCELERATING"


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


def test_volume_price_snapshot_detects_underwater_v_reversal(db_session):
    prices = [9.8, 9.55, 9.4, 9.5, 9.7, 9.9, 10.05, 10.15]
    snapshot = build_volume_price_snapshot(
        db_session,
        "600104",
        name="水下V形",
        quote={
            "price": prices[-1], "change_pct": 1.5, "open": prices[0],
            "prev_close": 10, "high": 10.15, "low": 9.4,
            "amount": 8.0, "volume": 8_000_000, "note": "东方财富实时行情",
            "minute_bars": [
                {"time": f"09:{31 + index:02d}", "price": price, "low": price, "high": price,
                 "volume": 1_000_000, "amount": price * 1_000_000}
                for index, price in enumerate(prices)
            ],
        },
        daily_metrics={},
    )

    assert snapshot.pattern in {"水下V形反转站回VWAP", "水下V形修复站回VWAP"}
    assert snapshot.vwap_reliable is True
    assert snapshot.price > snapshot.vwap
    assert any("V形回升" in item for item in snapshot.evidence)
    assert any("重新站回均价线" in item for item in snapshot.evidence)


def test_volume_price_snapshot_detects_limit_down_unlock_support(db_session):
    prices = [9.3, 9.05, 9.0, 9.12, 9.3, 9.5, 9.65, 9.8]
    snapshot = build_volume_price_snapshot(
        db_session,
        "600105",
        name="跌停开板承接",
        quote={
            "price": prices[-1], "change_pct": -2, "open": prices[0], "prev_close": 10,
            "limit_down_price": 9.0, "high": 9.8, "low": 9.0,
            "amount": 10.0, "volume": 8_000_000, "note": "东方财富实时行情",
            "minute_bars": [
                {"time": f"10:{index:02d}", "price": price, "low": price, "high": price,
                 "volume": 1_000_000, "amount": price * 1_000_000}
                for index, price in enumerate(prices)
            ],
        },
        daily_metrics={},
    )

    assert snapshot.pattern == "跌停开板V形修复"
    assert any("开板承接成立" in item for item in snapshot.evidence)


def test_higher_low_without_reliable_vwap_is_only_pending_reversal():
    prices = [9.3, 9.0, 9.12, 9.35, 9.55, 9.42, 9.50, 9.62]
    pattern, evidence = _minute_reversal_signals(
        {
            "price": prices[-1],
            "prev_close": 10,
            "limit_down_price": 9.0,
            "minute_bar_trade_date": datetime.now().date().isoformat(),
            "minute_bars": [
                {
                    "time": f"10:{index:02d}",
                    "price": price,
                    "low": price,
                    "high": price,
                    "volume": 1_000,
                    "amount": 0,
                }
                for index, price in enumerate(prices)
            ],
        },
        0,
    )

    assert pattern == "深水V形反抽待确认"
    assert any("尚未同时通过VWAP" in item for item in evidence)


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


def _persist_quote_snapshot(
    db_session,
    code: str,
    quote: dict,
    *,
    captured_at: datetime | None = None,
) -> DataCaptureSnapshot:
    trade_date = str(
        quote.get("minute_bar_trade_date")
        or (captured_at or datetime.now()).date().isoformat()
    )
    row = DataCaptureSnapshot(
        trade_date=trade_date,
        captured_at=captured_at or datetime.now(),
        source="test-persisted-collector",
        data_type="stock_minute",
        target_code=code,
        target_name=str(quote.get("name") or code),
        raw_value_json=json.dumps(quote, ensure_ascii=False),
        normalized_value_json="{}",
        quality="realtime",
        status="ok",
        is_complete=True,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_decision_card_includes_volume_price(client, db_session, monkeypatch):
    from app.api.helpers import decision

    observed_at = datetime(2026, 7, 23, 10, 15)
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: observed_at)
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
        "provider_event_at": observed_at.isoformat(),
        "received_at": observed_at.isoformat(),
    }

    _persist_quote_snapshot(db_session, "600004", quote, captured_at=observed_at)
    before = db_session.query(DataCaptureSnapshot).count()

    response = client.get("/api/stocks/600004/decision-card")

    assert response.status_code == 200
    assert db_session.query(DataCaptureSnapshot).count() == before
    payload = response.json()
    assert payload["volume_price"]["code"] == "600004"
    assert payload["volume_price"]["pattern"]
    assert payload["entry_discipline"]["decision"] == "BLOCK"
    assert payload["entry_discipline"]["allowed_position_ratio"] == 0
    assert payload["allowed_actions"] == ["只允许观察，不下单"]
    assert payload["forbidden_actions"][0] == payload["entry_discipline"]["label"]


def test_decision_card_marks_previous_session_capture_stale_at_read_time(
    client,
    db_session,
    monkeypatch,
):
    from app.api.helpers import decision

    now = datetime(2026, 7, 23, 10, 15)
    quote = _effective_flow_quote("2026-07-22")
    stale = _persist_quote_snapshot(
        db_session,
        "600054",
        quote,
        captured_at=datetime(2026, 7, 22, 15, 0),
    )
    assert stale.is_stale is False
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: now)

    response = client.get("/api/stocks/600054/decision-card")

    assert response.status_code == 200
    payload = response.json()
    assert payload["market_data_trade_date"] == "2026-07-22"
    assert payload["is_current_session"] is False
    assert payload["data_quality"] == "stale"
    assert payload["entry_discipline"]["decision"] == "BLOCK"
    assert payload["entry_discipline"]["allowed_position_ratio"] == 0
    assert payload["consensus_risk"]["actions"] == []
    assert payload["allowed_actions"] == ["历史行情仅供复盘，不生成当日操作建议"]
    assert "有效行情日应为2026-07-23" in payload["data_status_note"]


def test_market_data_status_uses_provider_event_time_not_receive_time():
    now = datetime(2026, 7, 23, 14, 0)
    quote = {
        "price": 10.0,
        "provider_event_at": "2026-07-23T10:00:00",
        "received_at": now.isoformat(),
    }

    status = decision_market_data_status(quote, None, now=now)

    assert status["is_current_session"] is False
    assert status["data_age_seconds"] == 4 * 60 * 60


def test_market_data_status_requires_lunch_and_close_anchors():
    lunch = datetime(2026, 7, 23, 12, 55)
    close = datetime(2026, 7, 23, 18, 0)

    assert decision_market_data_status(
        {"price": 10.0, "provider_event_at": "2026-07-23T09:20:00"},
        None,
        now=lunch,
    )["is_current_session"] is False
    assert decision_market_data_status(
        {"price": 10.0, "provider_event_at": "2026-07-23T11:30:00"},
        None,
        now=lunch,
    )["is_current_session"] is True
    assert decision_market_data_status(
        {"price": 10.0, "provider_event_at": "2026-07-23T09:20:00"},
        None,
        now=close,
    )["is_current_session"] is False
    assert decision_market_data_status(
        {"price": 10.0, "provider_event_at": "2026-07-23T14:50:00"},
        None,
        now=close,
    )["is_current_session"] is False
    assert decision_market_data_status(
        {"price": 10.0, "provider_event_at": "2026-07-23T23:59:00"},
        None,
        now=close,
    )["is_current_session"] is False
    assert decision_market_data_status(
        {"price": 10.0, "provider_event_at": "2026-07-23T15:00:00"},
        None,
        now=close,
    )["is_current_session"] is True


def test_market_data_status_does_not_treat_local_capture_date_as_market_date():
    now = datetime(2026, 7, 23, 10, 15)
    capture = SimpleNamespace(
        trade_date="2026-07-23",
        captured_at=now,
        is_stale=False,
    )

    status = decision_market_data_status(
        {"price": 10.0, "received_at": now.isoformat()},
        capture,
        now=now,
    )

    assert status["is_current_session"] is False
    assert status["market_data_trade_date"] == "2026-07-23"
    assert status["provider_event_at"] is None
    assert status["data_age_seconds"] is None


def test_explicit_decision_card_refresh_collects_queried_symbol_for_current_day(
    client,
    db_session,
    monkeypatch,
):
    from app.api.helpers import decision
    from app.services import intraday_evidence_engine

    now = datetime(2026, 7, 23, 10, 15)
    old_quote = _effective_flow_quote("2026-07-22")
    _persist_quote_snapshot(
        db_session,
        "600055",
        old_quote,
        captured_at=datetime(2026, 7, 22, 15, 0),
    )
    fresh_quote = _effective_flow_quote("2026-07-23")
    fresh_quote.update({
        "name": "当日查询标的",
        "price": 12.34,
        "provider_event_at": now.isoformat(),
        "received_at": now.isoformat(),
    })
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: now)
    monkeypatch.setattr(intraday_evidence_engine, "shanghai_now_naive", lambda *_args, **_kwargs: now)
    monkeypatch.setattr("app.api.routes.stocks.quote_for_code", lambda _code: fresh_quote)

    response = client.post("/api/stocks/600055/decision-card/refresh")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_price"] == 12.34
    assert payload["market_data_trade_date"] == "2026-07-23"
    assert payload["is_current_session"] is True
    latest = db_session.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.target_code == "600055",
        DataCaptureSnapshot.data_type == "tracked_stock_minute",
    ).order_by(DataCaptureSnapshot.captured_at.desc()).first()
    assert latest is not None
    assert latest.trade_date == "2026-07-23"


def test_decision_card_keeps_latest_usable_quote_when_newer_collection_failed(
    client,
    db_session,
    monkeypatch,
):
    from app.api.helpers import decision

    now = datetime(2026, 7, 23, 10, 15)
    valid_quote = _effective_flow_quote("2026-07-23")
    _persist_quote_snapshot(db_session, "600056", valid_quote, captured_at=datetime(2026, 7, 23, 10, 10))
    db_session.add_all([
        VolumePriceSnapshot(
            trade_date="2026-07-23",
            captured_at=datetime(2026, 7, 23, 10, 10),
            code="600056",
            name="有效量价",
            price=valid_quote["price"],
            vwap=10.15,
            minute_bar_count=10,
            vwap_reliable=True,
            data_quality="realtime",
        ),
        VolumePriceSnapshot(
            trade_date="2026-07-23",
            captured_at=datetime(2026, 7, 23, 10, 14),
            code="600056",
            name="失败量价",
            price=0,
            vwap=0,
            data_quality="manual",
        ),
    ])
    db_session.add(DataCaptureSnapshot(
        trade_date="2026-07-23",
        captured_at=datetime(2026, 7, 23, 10, 14),
        source="provider-failure",
        data_type="tracked_stock_minute",
        target_code="600056",
        target_name="采集失败不遮挡",
        raw_value_json="{}",
        normalized_value_json="{}",
        quality="missing",
        is_complete=False,
        status="missing",
        error_message="upstream unavailable",
    ))
    db_session.commit()
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: now)

    response = client.get("/api/stocks/600056/decision-card")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_price"] == valid_quote["price"]
    assert payload["is_current_session"] is True
    assert payload["volume_price"]["price"] == valid_quote["price"]
    assert payload["volume_price"]["data_quality"] == "realtime"


def _effective_flow_quote(trade_date: str, start_hour: int = 10, start_minute: int = 1):
    prices = [10.00, 10.03, 10.06, 10.10, 10.14, 10.18, 10.21, 10.24, 10.27, 10.30]
    rows = []
    for index, price in enumerate(prices):
        minute_of_day = start_hour * 60 + start_minute + index
        amount = 10_000_000.0
        rows.append({
            "trade_date": trade_date,
            "time": f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}",
            "price": price,
            "close": price,
            "high": price,
            "low": price,
            "volume": amount / price,
            "amount": amount,
            "active_buy_amount": 8_000_000.0,
            "active_sell_amount": 2_000_000.0,
        })
    return {
        "name": "订单流测试",
        "price": prices[-1],
        "change_pct": 3.0,
        "open": prices[0],
        "prev_close": 10.0,
        "high": prices[-1],
        "low": prices[0],
        "amount": 1.0,
        "volume": sum(row["volume"] for row in rows),
        "turnover": 2.0,
        "note": "东方财富实时行情",
        "minute_bar_trade_date": trade_date,
        "minute_bars": rows,
    }


def _deep_v_recovery_quote(trade_date: str):
    """Keep the latest repair below the true whole-session VWAP.

    The first five high-volume bars establish an older, higher cost anchor;
    the selected ten-bar decision window then repairs strongly from deep
    water with explicit aggressive-buy classifications.  This is the case
    that used to be mistaken for distribution merely because the latest
    price had not reclaimed the whole-session VWAP yet.
    """

    prices = [10.80] * 5 + [9.50, 9.58, 9.66, 9.75, 9.84, 9.92, 9.98, 10.00, 10.00, 10.00]
    rows = []
    for index, price in enumerate(prices):
        minute_of_day = 9 * 60 + 56 + index
        leading_anchor = index < 5
        amount = 50_000_000.0 if leading_anchor else 10_000_000.0
        rows.append({
            "trade_date": trade_date,
            "time": f"{minute_of_day // 60:02d}:{minute_of_day % 60:02d}",
            "price": price,
            "close": price,
            "high": price,
            "low": price,
            "volume": amount / price,
            "amount": amount,
            "active_buy_amount": 25_000_000.0 if leading_anchor else 8_000_000.0,
            "active_sell_amount": 25_000_000.0 if leading_anchor else 2_000_000.0,
        })
    return {
        "name": "深水修复测试",
        "price": prices[-1],
        "change_pct": -0.5,
        "open": prices[0],
        "prev_close": 10.05,
        "high": max(prices),
        "low": min(prices),
        "amount": sum(row["amount"] for row in rows) / 1e8,
        "volume": sum(row["volume"] for row in rows),
        "turnover": 2.0,
        "note": "东方财富实时行情",
        "minute_bar_trade_date": trade_date,
        "minute_bars": rows,
    }


def test_decision_card_maps_order_flow_units_and_provenance(client, db_session, monkeypatch):
    from app.api.helpers import decision

    now = datetime(2026, 7, 17, 10, 10)
    quote = _effective_flow_quote("2026-07-17")
    _persist_quote_snapshot(db_session, "600040", quote, captured_at=now)
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: now)

    response = client.get("/api/stocks/600040/decision-card")

    assert response.status_code == 200
    evidence = response.json()["effective_capital"]
    assert evidence["state"] == "ATTACK_CONFIRMED"
    assert evidence["state_severity"] == "POSITIVE"
    assert evidence["data_quality"] == "realtime"
    assert evidence["source_label"].startswith("东方财富逐笔成交方向分类")
    assert evidence["metrics"]["active_buy_yi"] == 0.8
    assert evidence["metrics"]["active_sell_yi"] == 0.2
    assert evidence["metrics"]["signed_flow_yi"] == 0.6
    assert evidence["metrics"]["active_flow_coverage_ratio"] == 1.0
    assert evidence["metrics"]["same_time_flow_percentile"] is None
    assert evidence["metrics"]["normalization_sample_count"] == 0
    assert "非账户身份" in evidence["source_label"]
    assert any("不代表机构账户" in item for item in evidence["warnings"])


def test_decision_card_maps_deep_v_repair_to_watch_without_panic_sell_or_chase(client, db_session, monkeypatch):
    from app.api.helpers import decision

    db_session.add(Holding(
        code="600043",
        name="深水修复测试",
        quantity=1000,
        cost_price=10.2,
        current_price=10.0,
        total_asset=100_000,
        position_type="观察仓",
    ))
    db_session.commit()
    now = datetime(2026, 7, 17, 10, 10)
    quote = _deep_v_recovery_quote("2026-07-17")
    _persist_quote_snapshot(db_session, "600043", quote, captured_at=now)
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: now)

    response = client.get("/api/stocks/600043/decision-card")

    assert response.status_code == 200
    payload = response.json()
    evidence = payload["effective_capital"]
    assert evidence["state"] == "RECOVERY_CANDIDATE"
    assert evidence["state_severity"] == "WATCH"
    assert evidence["metrics"]["sample_count"] == 10
    assert evidence["metrics"]["window_minutes"] == 9
    assert evidence["metrics"]["price_change_pct"] > 5
    assert evidence["metrics"]["vwap_distance_pct"] < 0
    assert any("避免在窗口低点附近恐慌卖出" in item for item in evidence["discipline"])
    assert any("禁止追高或逆势补仓" in item for item in evidence["discipline"])
    assert any("深水修复候选" in item and "恐慌卖出" in item for item in payload["allowed_actions"])
    assert any("未站稳分时均价前禁止追高、补仓" in item for item in payload["forbidden_actions"])


def test_decision_card_labels_prior_session_close_without_calling_it_live(client, db_session, monkeypatch):
    from app.api.helpers import decision

    wall_clock = datetime(2026, 7, 18, 10, 10)
    quote = _effective_flow_quote("2026-07-17", start_hour=14, start_minute=51)
    _persist_quote_snapshot(db_session, "600041", quote, captured_at=datetime(2026, 7, 17, 15, 0))
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: wall_clock)

    response = client.get("/api/stocks/600041/decision-card")

    assert response.status_code == 200
    evidence = response.json()["effective_capital"]
    assert evidence["state"] == "ATTACK_CONFIRMED"
    assert evidence["data_quality"] == "historical_close"
    assert evidence["source_label"].startswith("2026-07-17 收盘窗口")
    assert any("不代表当前盘前或盘中的实时状态" in item for item in evidence["warnings"])


def test_decision_card_rejects_future_trade_date_instead_of_replaying_it(client, db_session, monkeypatch):
    from app.api.helpers import decision

    wall_clock = datetime(2026, 7, 17, 10, 10)
    quote = _effective_flow_quote("2026-07-18")
    _persist_quote_snapshot(db_session, "600042", quote, captured_at=datetime(2026, 7, 18, 10, 10))
    monkeypatch.setattr(decision, "shanghai_now_naive", lambda *_args, **_kwargs: wall_clock)

    response = client.get("/api/stocks/600042/decision-card")

    assert response.status_code == 200
    evidence = response.json()["effective_capital"]
    assert evidence["state"] == "INSUFFICIENT_DATA"
    assert "FUTURE_TIMESTAMP" in evidence["reason_codes"]
    assert not evidence["source_label"].startswith("2026-07-18 收盘窗口")


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
    assert "至少两类证据" in refreshed.trim_condition
    assert "全市场扩仓闸门" in refreshed.buyback_condition
    assert any("不恐慌卖出≠允许抄底" in item for item in refreshed.risk_warnings)


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
