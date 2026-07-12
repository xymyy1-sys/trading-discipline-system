from types import SimpleNamespace
from datetime import datetime, timedelta

from app.api.helpers.execution import _confirmation_deadline, _confirmation_policy, build_position_execution_state
from app.models.trading import (
    ExpectationSnapshot,
    ActionRecommendation,
    ExitCard,
    Holding,
    IntradayEvidenceEvent,
    NextDayPlan,
    PositionStateHistory,
    TimeStopRule,
    TradeLog,
    VolumePriceSnapshot,
)
from app.services.intraday_evidence_engine import collect_holding_evidence, nearest_sample_label


def test_position_execution_profit_drawdown_requires_reduce(db_session):
    holding = Holding(
        code="600000",
        name="测试股份",
        quantity=1000,
        cost_price=10,
        current_price=10.8,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="按利润保护执行",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    quote = {
        "price": 10.8,
        "high": 11.2,
        "low": 10.6,
        "open": 11.0,
        "vwap": 11.05,
        "minute_bars": [
            {"price": 11.0, "volume": 1000, "amount": 11000},
            {"price": 11.1, "volume": 1000, "amount": 11100},
            {"price": 11.05, "volume": 1000, "amount": 11050},
        ],
        "amount": 1,
        "volume": 1000000,
        "note": "实时行情",
    }
    seesaw = SimpleNamespace(
        risk_level="中高",
        signal="板块资金回落",
        sector_ebb_trigger=["所属板块资金从峰值回落。"],
        stock_weakening_trigger=["个股跌破VWAP。"],
        profit_drawdown_trigger=["浮盈保护区内回撤。"],
        theme_flow_pullback_pct=25,
        theme_flow_current=5,
        theme_flow_peak=10,
        theme_flow_summary="主线资金从高位回落。",
        holding_theme="测试主线",
        pullback_from_high_pct=3,
    )

    state = build_position_execution_state(db_session, holding, quote=quote, seesaw=seesaw)

    assert state.recommended_action in {"减仓50%", "只留观察仓"}
    assert state.recommended_reduce_ratio >= 0.5
    assert state.t_eligible is False
    assert state.profit_snapshot is not None
    assert state.profit_snapshot.maximum_profit_pct == 12
    assert any(event.event_type == "SECTOR_FLOW_PEAK_REVERSAL" for event in state.events)


def test_position_execution_hard_stop_forbids_t(db_session):
    holding = Holding(
        code="600001",
        name="止损测试",
        quantity=1000,
        cost_price=10,
        current_price=9.3,
        total_asset=100000,
        position_type="打板仓",
        next_discipline="跌破硬止损退出",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 9.3, "high": 9.6, "low": 9.25, "open": 9.5, "note": "实时行情"},
        seesaw=None,
    )

    assert state.state == "EXIT_REQUIRED"
    assert state.recommended_action == "全部退出"
    assert state.recommended_reduce_ratio == 1
    assert state.t_eligible is False
    assert any("硬止损" in item for item in state.evidence)

    build_position_execution_state(
        db_session,
        holding,
        quote={"price": 9.2, "high": 9.6, "low": 9.15, "open": 9.5, "note": "实时行情"},
        seesaw=None,
    )
    assert db_session.query(ActionRecommendation).filter(ActionRecommendation.holding_id == holding.id).count() == 1


def test_expectation_and_vwap_breakdown_requires_risk_reduction(db_session):
    holding = Holding(
        code="600006",
        name="预期量价联动",
        quantity=1000,
        cost_price=10,
        current_price=10.4,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="弱于预期跌破VWAP降风险",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    expectation = ExpectationSnapshot(
        trade_date="2026-07-12",
        code="600006",
        name="预期量价联动",
        stage="五分钟确认",
        base_expectation="STRONG",
        expected_open_low=2,
        expected_open_high=5.5,
        outperform_threshold=6.5,
        underperform_threshold=1,
        severe_underperform_threshold=-3,
        actual_open_pct=0,
        actual_change_pct=0.2,
        expectation_gap_score=-10,
        expectation_result="SLIGHTLY_WEAKER",
        state_transition="CONSENSUS_TO_DIVERGENCE",
        confidence=0.8,
        evidence_json='["开盘低于强预期阈值。"]',
        counter_evidence_json="[]",
        suggestion="预期转弱，禁止补仓。",
    )
    volume = VolumePriceSnapshot(
        trade_date="2026-07-12",
        code="600006",
        name="预期量价联动",
        stage="五分钟确认",
        price=10.4,
        change_pct=0.2,
        open_price=10.6,
        high_price=11.1,
        low_price=10.3,
        prev_close=10,
        amount=8,
        estimated_full_day_amount=20,
        turnover=5,
        vwap=10.75,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        price_vs_vwap=-3.26,
        high_drawdown=6.31,
        pattern="冲高回落跌破VWAP",
        data_quality="realtime",
        data_source="测试行情",
        evidence_json='["冲高回落跌破VWAP。"]',
        counter_evidence_json="[]",
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.4,
            "high": 11.1,
            "low": 10.3,
            "open": 10.6,
            "vwap": 10.75,
            "minute_bars": [
                {"price": 10.8, "volume": 1000, "amount": 10800},
                {"price": 10.7, "volume": 1000, "amount": 10700},
                {"price": 10.75, "volume": 1000, "amount": 10750},
            ],
            "note": "东方财富实时行情",
        },
        expectation=expectation,
        volume_price=volume,
    )

    assert state.state == "EXPECTATION_VOLUME_BREAKDOWN"
    assert state.recommended_reduce_ratio >= 0.5
    assert state.t_eligible is False
    assert state.expectation_state == "SLIGHTLY_WEAKER"
    assert state.volume_price_state == "VOLUME_PRICE_WEAKENING"
    assert any(event.event_type == "EXPECTATION_VOLUME_BREAKDOWN" for event in state.events)
    assert any("禁止补仓" in item or "不允许补仓" in item for item in state.evidence)


def test_stronger_expectation_and_vwap_strength_stays_hold(db_session):
    holding = Holding(
        code="600007",
        name="预期量价强势",
        quantity=1000,
        cost_price=10,
        current_price=10.9,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="按计划持有",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    expectation = ExpectationSnapshot(
        trade_date="2026-07-12",
        code="600007",
        name="预期量价强势",
        stage="五分钟确认",
        base_expectation="STRONG",
        expected_open_low=2,
        expected_open_high=5.5,
        outperform_threshold=6.5,
        underperform_threshold=1,
        severe_underperform_threshold=-3,
        actual_open_pct=3,
        actual_change_pct=9,
        expectation_gap_score=16,
        expectation_result="STRONGER",
        state_transition="STRONG_TO_STRONGER",
        confidence=0.8,
        evidence_json='["开盘和盘中均超预期。"]',
        counter_evidence_json="[]",
        suggestion="按计划确认。",
    )
    volume = VolumePriceSnapshot(
        trade_date="2026-07-12",
        code="600007",
        name="预期量价强势",
        stage="五分钟确认",
        price=10.9,
        change_pct=9,
        open_price=10.3,
        high_price=11,
        low_price=10.2,
        prev_close=10,
        amount=12,
        estimated_full_day_amount=25,
        turnover=8,
        vwap=10.55,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        price_vs_vwap=3.32,
        high_drawdown=0.9,
        pattern="VWAP上方强势",
        data_quality="realtime",
        data_source="测试行情",
        evidence_json='["VWAP上方强势。"]',
        counter_evidence_json="[]",
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.9, "high": 11, "low": 10.2, "open": 10.3, "note": "东方财富实时行情"},
        expectation=expectation,
        volume_price=volume,
    )

    assert state.state in {"PROFIT_EXPANSION", "PROFIT_PROTECTION", "NORMAL_HOLD"}
    assert state.recommended_action in {"继续持有", "减仓25%"}
    assert state.volume_price_state in {"REPAIR_CONFIRMED", "VWAP_STRONG"}
    assert any("暂未构成预期证伪" in item for item in state.counter_evidence)


def test_degraded_vwap_does_not_emit_deterministic_reduce(db_session):
    holding = Holding(
        code="600010",
        name="降级VWAP",
        quantity=1000,
        cost_price=10,
        current_price=10.4,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="缺分钟数据不确定触发",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date="2026-07-12",
        code="600010",
        name="降级VWAP",
        stage="五分钟确认",
        price=10.4,
        change_pct=0.2,
        open_price=10.6,
        high_price=11.1,
        low_price=10.3,
        prev_close=10,
        amount=8,
        estimated_full_day_amount=20,
        turnover=5,
        vwap=10.75,
        vwap_source="range_estimated",
        minute_bar_count=0,
        vwap_reliable=False,
        price_vs_vwap=-3.26,
        high_drawdown=6.31,
        pattern="冲高回落跌破VWAP",
        data_quality="degraded_vwap",
        data_source="估算行情",
        evidence_json='["冲高回落跌破VWAP。"]',
        counter_evidence_json='["缺少真实1分钟成交数据。"]',
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.4, "high": 11.1, "low": 10.3, "open": 10.6, "note": "东方财富实时行情"},
        volume_price=volume,
    )

    assert state.state == "DEGRADED_DATA_OBSERVATION"
    assert state.recommended_action == "观察但禁止加仓"
    assert state.recommended_reduce_ratio == 0
    assert state.data_quality == "degraded_vwap"
    assert any("不输出确定性减仓" in item for item in state.evidence)


def test_t_plus_one_sellable_quantity_excludes_today_buys(db_session):
    holding = Holding(
        code="600011",
        name="T加一",
        quantity=1500,
        cost_price=10,
        current_price=10.5,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="验证可卖数量",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(TradeLog(
        code="600011",
        name="T加一",
        traded_at=datetime.now(),
        side="买入",
        price=10.5,
        quantity=500,
        amount=5250,
        total_asset=100000,
        position_ratio=0.0525,
        cost_price=10,
        stop_loss_price=9.6,
        reason="今日买入不计入可卖。",
        mode="标准短线模式",
        compliant=True,
        human_tags="",
    ))
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.5,
            "high": 10.8,
            "low": 10.3,
            "open": 10.4,
            "vwap": 10.45,
            "minute_bars": [
                {"price": 10.4, "volume": 1000, "amount": 10400},
                {"price": 10.5, "volume": 1000, "amount": 10500},
                {"price": 10.45, "volume": 1000, "amount": 10450},
            ],
            "note": "实时行情",
        },
    )

    assert state.current_quantity == 1500
    assert state.today_buy_quantity == 500
    assert state.sellable_quantity == 1000
    assert state.yesterday_quantity == 1000


def test_script_stop_levels_override_candidate_stop(db_session):
    holding = Holding(
        code="600012",
        name="剧本止损",
        quantity=1000,
        cost_price=10,
        current_price=10.15,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="交易剧本：结构止损 10.20，硬止损 9.80。",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.15, "high": 10.8, "low": 10.1, "open": 10.5, "note": "实时行情"},
    )

    assert state.structure_stop_price == 10.2
    assert state.hard_stop_price == 9.8
    assert state.stop_source == "text_script"
    assert "交易剧本解析" in state.stop_source_detail
    assert any("交易剧本解析结构止损" in item for item in state.evidence)


def test_structured_plan_and_exit_card_stop_levels_take_priority(db_session):
    holding = Holding(
        code="600019",
        name="结构化止损",
        quantity=1000,
        cost_price=10,
        current_price=10.5,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="按计划执行",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(NextDayPlan(
        plan_date="2026-07-13",
        plan_type="holding",
        holding_id=holding.id,
        code="600019",
        name="结构化止损",
        confirm_price=10.35,
        trim_price=10.45,
        reduce_price=10.4,
        final_risk_price=9.9,
        stop_loss_4pct=9.6,
    ))
    db_session.add(ExitCard(
        code="600019",
        name="结构化止损",
        max_position_ratio=0.1,
        confirm_price=10.5,
        trim_price=10.55,
        failure_price=9.85,
        outperform_condition="站稳确认价",
        underperform_action="跌破失败价退出",
    ))
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.5, "high": 10.8, "low": 10.3, "open": 10.4, "note": "实时行情"},
    )

    assert state.structure_stop_price == 10.55
    assert state.hard_stop_price == 9.85
    assert state.stop_source == "next_day_plan+sell_card"
    assert "次日计划" in state.stop_source_detail
    assert "卖出卡" in state.stop_source_detail
    assert any("次日计划结构位" in item for item in state.evidence)
    assert any("卖出卡失败价" in item for item in state.evidence)


def test_event_confirmation_policy_uses_event_specific_windows():
    assert _confirmation_policy("TIME_STOP_TRIGGERED") == (3, 1)
    assert _confirmation_policy("SECTOR_MIGRATION_CONFIRMED") == (10, 2)


def test_confirmation_deadline_can_be_configured_by_script():
    holding = Holding(
        code="600020",
        name="确认截止",
        quantity=1000,
        cost_price=10,
        current_price=10,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="10:30确认仍未修复则退出",
    )

    assert _confirmation_deadline(holding).strftime("%H:%M") == "10:30"


def test_time_stop_triggers_on_sustained_reliable_vwap_break(db_session):
    holding = Holding(
        code="600013",
        name="时间止损",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="持续低于VWAP退出",
    )
    db_session.add(holding)
    db_session.add(TimeStopRule(
        script_type="trend",
        display_name="趋势/容量",
        confirmation_deadline="10:30",
        below_vwap_minutes=5,
        below_vwap_min_bars=5,
        recent_window_minutes=15,
        failed_limit_reseal_pct=0.985,
        enabled=True,
    ))
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date="2026-07-12",
        code="600013",
        name="时间止损",
        stage="第一阶段确认",
        price=10.2,
        change_pct=2,
        high_price=10.9,
        low_price=10.1,
        vwap=10.55,
        vwap_source="minute",
        minute_bar_count=8,
        vwap_reliable=True,
        high_drawdown=6.4,
        pattern="跌破VWAP",
        data_quality="realtime",
        data_source="测试行情",
        evidence_json="[]",
        counter_evidence_json="[]",
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.2,
            "high": 10.9,
            "low": 10.1,
            "open": 10.7,
            "vwap": 10.55,
            "minute_bars": [
                {"price": 10.48, "volume": 1000, "amount": 10480},
                {"price": 10.45, "volume": 1000, "amount": 10450},
                {"price": 10.42, "volume": 1000, "amount": 10420},
                {"price": 10.35, "volume": 1000, "amount": 10350},
                {"price": 10.2, "volume": 1000, "amount": 10200},
            ],
            "note": "实时行情",
        },
        volume_price=volume,
    )

    assert any("真实分钟数据连续" in item for item in state.evidence)
    assert any(event.event_type == "TIME_STOP_TRIGGERED" for event in state.events)


def test_time_stop_rule_can_tighten_breakout_threshold(db_session):
    holding = Holding(
        code="600023",
        name="规则时间止损",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="打板仓",
        next_discipline="冲板未回封按规则处理",
    )
    db_session.add(holding)
    db_session.add(TimeStopRule(
        script_type="breakout",
        display_name="打板/冲板",
        confirmation_deadline="09:45",
        below_vwap_minutes=2,
        below_vwap_min_bars=2,
        recent_window_minutes=8,
        failed_limit_reseal_pct=0.995,
        enabled=True,
    ))
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.9,
            "high": 11.0,
            "low": 10.8,
            "open": 10.9,
            "prev_close": 10,
            "vwap": 10.95,
            "minute_bars": [
                {"price": 10.96, "volume": 1000, "amount": 10960},
                {"price": 10.94, "volume": 1000, "amount": 10940},
                {"price": 10.93, "volume": 1000, "amount": 10930},
            ],
            "note": "实时行情",
        },
    )

    assert any("打板/冲板规则" in item for item in state.evidence)
    assert any(event.event_type == "TIME_STOP_TRIGGERED" for event in state.events)


def test_recovery_event_after_previous_risk_event(db_session):
    holding = Holding(
        code="600014",
        name="风险恢复",
        quantity=1000,
        cost_price=10,
        current_price=10.8,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="修复后观察",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(IntradayEvidenceEvent(
        trade_date="2026-07-12",
        captured_at=datetime.now() - timedelta(minutes=10),
        scope="stock",
        target_code="600014",
        target_name="风险恢复",
        event_type="VWAP_BROKEN",
        severity="warning",
        value=10.1,
        previous_value=10.5,
        evidence_json='["跌破VWAP。"]',
    ))
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date="2026-07-12",
        code="600014",
        name="风险恢复",
        stage="修复确认",
        price=10.8,
        change_pct=8,
        high_price=10.9,
        low_price=10.1,
        vwap=10.45,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        high_drawdown=0.9,
        pattern="VWAP上方强势",
        data_quality="realtime",
        data_source="测试行情",
        evidence_json="[]",
        counter_evidence_json="[]",
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.8, "high": 10.9, "low": 10.1, "open": 10.2, "vwap": 10.45, "note": "实时行情"},
        volume_price=volume,
    )

    assert any(event.event_type == "RISK_RECOVERY_CONFIRMED" for event in state.events)


def test_sector_migration_event_when_external_flow_takes_over(db_session):
    holding = Holding(
        code="600015",
        name="迁移识别",
        quantity=1000,
        cost_price=10,
        current_price=10.6,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="板块退潮降风险",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    seesaw = SimpleNamespace(
        risk_level="中高",
        signal="资金迁移",
        external_inflow_target="机器人链",
        sector_rank=18,
        sector_net_inflow=-3,
        theme_flow_pullback_pct=25,
        theme_flow_current=2,
        theme_flow_peak=8,
        theme_flow_summary="原主线资金回落。",
        holding_theme="半导体链",
        pullback_from_high_pct=3,
        sector_ebb_trigger=["资金排名降至第18。"],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.6,
            "high": 10.9,
            "low": 10.4,
            "open": 10.5,
            "vwap": 10.5,
            "minute_bars": [
                {"price": 10.5, "volume": 1000, "amount": 10500},
                {"price": 10.55, "volume": 1000, "amount": 10550},
                {"price": 10.6, "volume": 1000, "amount": 10600},
            ],
            "note": "实时行情",
        },
        seesaw=seesaw,
    )

    assert any(event.event_type == "SECTOR_MIGRATION_CONFIRMED" for event in state.events)
    migration = [event for event in state.events if event.event_type == "SECTOR_MIGRATION_CONFIRMED"][0]
    assert any("可信度" in item for item in migration.evidence)
    assert migration.priority >= 75


def test_position_state_history_records_transitions(db_session):
    holding = Holding(
        code="600021",
        name="状态历史",
        quantity=1000,
        cost_price=10,
        current_price=10.5,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="按状态迁移记录",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    first = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.5,
            "high": 10.8,
            "low": 10.3,
            "open": 10.4,
            "vwap": 10.45,
            "minute_bars": [
                {"price": 10.4, "volume": 1000, "amount": 10400},
                {"price": 10.5, "volume": 1000, "amount": 10500},
                {"price": 10.45, "volume": 1000, "amount": 10450},
            ],
            "note": "实时行情",
        },
    )
    second = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 9.1, "high": 10.8, "low": 9.0, "open": 10.4, "note": "实时行情"},
    )

    rows = (
        db_session.query(PositionStateHistory)
        .filter(PositionStateHistory.holding_id == holding.id)
        .order_by(PositionStateHistory.id.asc())
        .all()
    )
    assert first.state_history
    assert second.state_history
    assert len(rows) >= 2
    assert rows[0].old_state == ""
    assert rows[-1].new_state == "EXIT_REQUIRED"
    assert rows[-1].reason == "全部退出"


def test_high_open_failed_breakout_event_escalates_to_red(db_session):
    holding = Holding(
        code="600022",
        name="高开失败",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="打板仓",
        next_discipline="高开冲板失败禁止补仓",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date="2026-07-12",
        code="600022",
        name="高开失败",
        stage="冲板失败",
        price=10.2,
        change_pct=2,
        open_price=10.5,
        high_price=11.0,
        low_price=10.1,
        prev_close=10,
        amount=18,
        vwap=10.55,
        vwap_source="minute",
        minute_bar_count=8,
        vwap_reliable=True,
        high_drawdown=7.3,
        active_buy_amount=3.0,
        active_sell_amount=5.2,
        attack_efficiency=0.1,
        pattern="冲高回落跌破VWAP",
        data_quality="realtime",
        data_source="测试行情",
        evidence_json="[]",
        counter_evidence_json="[]",
    )
    seesaw = SimpleNamespace(
        risk_level="中高",
        signal="板块资金回落",
        theme_flow_pullback_pct=30,
        sector_net_inflow=-5,
        sector_ebb_trigger=["板块资金峰值回落。"],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.2,
            "prev_close": 10,
            "open": 10.5,
            "high": 11.0,
            "low": 10.1,
            "vwap": 10.55,
            "minute_bars": [
                {"price": 10.8, "volume": 1000, "amount": 10800},
                {"price": 10.6, "volume": 1000, "amount": 10600},
                {"price": 10.2, "volume": 1000, "amount": 10200},
            ],
            "note": "实时行情",
        },
        seesaw=seesaw,
        volume_price=volume,
    )

    high_open_events = [event for event in state.events if event.event_type == "HIGH_OPEN_FAILED_BREAKOUT"]
    assert high_open_events
    assert high_open_events[0].severity == "critical"
    assert any("RED 风险" in item for item in high_open_events[0].evidence)


def test_intraday_evidence_engine_saves_sample_event(monkeypatch, db_session):
    holding = Holding(
        code="600023",
        name="证据采样",
        quantity=1000,
        cost_price=10,
        current_price=10.6,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="记录盘中证据",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    quote = {
        "price": 10.6,
        "prev_close": 10,
        "open": 10.4,
        "high": 10.8,
        "low": 10.3,
        "vwap": 10.5,
        "volume": 3000,
        "amount": 31500,
        "minute_bars": [
            {"time": "09:31", "price": 10.4, "volume": 1000, "amount": 10400},
            {"time": "09:32", "price": 10.5, "volume": 1000, "amount": 10500},
            {"time": "09:33", "price": 10.6, "volume": 1000, "amount": 10600},
        ],
        "note": "实时行情",
    }
    monkeypatch.setattr("app.services.intraday_evidence_engine.quote_for_code", lambda code: quote)

    _, state, sample = collect_holding_evidence(
        db_session,
        holding,
        stage="09:35确认",
        now=datetime(2026, 7, 12, 9, 35),
    )

    assert nearest_sample_label(datetime(2026, 7, 12, 9, 36)) == "09:35"
    assert sample.event_type == "INTRADAY_EVIDENCE_SNAPSHOT"
    assert sample.confirmed is True
    assert sample.recommendation_id == state.recommendation.id
    assert "09:35" in sample.group_key
