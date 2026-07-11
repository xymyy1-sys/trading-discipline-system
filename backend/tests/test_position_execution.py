from types import SimpleNamespace

from app.api.helpers.execution import build_position_execution_state
from app.models.trading import ExpectationSnapshot, Holding, VolumePriceSnapshot


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
        quote={"price": 10.4, "high": 11.1, "low": 10.3, "open": 10.6, "note": "东方财富实时行情"},
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
