from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from app.api.helpers.execution import (
    _confirmation_deadline,
    _confirmation_policy,
    _load_global_market_snapshot,
    build_position_execution_state,
    global_market_execution_gate,
)
from app.models.trading import (
    ExpectationSnapshot,
    ActionRecommendation,
    ExitCard,
    Holding,
    IntradayEvidenceEvent,
    MarketRegimeSnapshot,
    NextDayPlan,
    PositionStateHistory,
    ProfitProtectionSnapshot,
    TimeStopRule,
    TradeLog,
    VolumePriceSnapshot,
)
from app.services.intraday_evidence_engine import collect_holding_evidence, nearest_sample_label


def test_sector_distribution_event_freezes_expansion_but_never_sells_alone(db_session, monkeypatch):
    holding = Holding(
        code="600900",
        name="板块派发联动测试",
        quantity=1000,
        cost_price=10,
        current_price=10,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="等待个股预期与量价确认",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    high_distribution = {
        "items": [{
            "name": "半导体",
            "heat_score": 82,
            "distribution_state": "高位派发风险",
            "distribution_risk_level": "HIGH",
            "distribution_risk_score": 86,
            "distribution_confirmation_count": 3,
            "order_flow_exhausted": True,
            "leverage_crowding": True,
            "price_response_weak": True,
            "distribution_evidence": ["历史净流入后当日转为流出", "价格对新增资金响应转弱"],
        }],
    }
    monkeypatch.setattr(
        "app.api.helpers.execution._get_response_cache",
        lambda key, allow_stale=False: high_distribution if key.endswith("|行业") else None,
    )
    seesaw = SimpleNamespace(
        risk_level="低",
        signal="板块尚未形成个股卖出共振",
        sector_ebb_trigger=[],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
        holding_theme="半导体",
        primary_industry_sector="半导体",
        matched_flow_sector="半导体",
        concept_flow_sectors=[],
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10, "high": 10.1, "low": 9.9, "open": 10, "note": "实时行情"},
        seesaw=seesaw,
        persist=False,
    )

    assert state.recommended_reduce_ratio == 0
    assert "禁止加仓" in state.recommended_action or "禁止加仓" in " ".join(state.evidence)
    assert any("不据此单独机械卖出" in item for item in state.evidence)
    assert any(event.event_type == "SECTOR_DISTRIBUTION_RISK" for event in state.events)


def test_healthy_incremental_sector_state_is_recognized_as_counter_evidence(db_session, monkeypatch):
    holding = Holding(
        code="600901",
        name="健康增量兼容测试",
        quantity=100,
        cost_price=10,
        current_price=10,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="按计划执行",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    healthy_distribution = {
        "items": [{
            "name": "半导体",
            "distribution_state": "健康增量",
            "distribution_risk_level": "LOW",
            "distribution_confirmation_count": 3,
        }],
    }
    monkeypatch.setattr(
        "app.api.helpers.execution._get_response_cache",
        lambda key, allow_stale=False: healthy_distribution if key.endswith("|行业") else None,
    )
    seesaw = SimpleNamespace(
        risk_level="低",
        signal="板块资金健康",
        sector_ebb_trigger=[],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
        holding_theme="半导体",
        primary_industry_sector="半导体",
        matched_flow_sector="半导体",
        concept_flow_sectors=[],
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10, "high": 10.1, "low": 9.9, "open": 10, "note": "实时行情"},
        seesaw=seesaw,
        persist=False,
    )

    assert any("资金与价格响应仍属健康" in item for item in state.counter_evidence)
    assert not any(event.event_type == "SECTOR_DISTRIBUTION_RISK" for event in state.events)


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
        next_discipline="交易剧本：硬止损 9.60，跌破立即退出",
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


def test_intraday_low_is_not_used_as_a_self_referential_stop(db_session):
    holding = Holding(
        code="600101",
        name="低点不造止损",
        quantity=1000,
        cost_price=10,
        current_price=9.85,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="尚未制定盘前硬止损",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 9.85, "high": 10.5, "low": 9.85, "open": 10.2, "note": "实时行情"},
    )

    assert state.structure_stop_price == 9.7
    assert state.structure_stop_price != 9.85
    assert state.hard_stop_price == 0
    assert state.stop_source == "cost_reference"
    assert any("不使用盘中最低价" in item for item in state.evidence)


def test_extreme_low_without_frozen_hard_stop_blocks_chasing_the_sell(db_session):
    holding = Holding(
        code="600102",
        name="极端低点门控",
        quantity=1000,
        cost_price=10,
        current_price=9.05,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="预期转弱时降低风险",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        expectation_gap_score=-10,
        expectation_result="SLIGHTLY_WEAKER",
    )
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        price=9.05,
        open_price=9.8,
        high_price=10,
        low_price=9,
        prev_close=10,
        vwap=9.5,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        pattern="跌破VWAP",
        data_quality="realtime",
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 9.05,
            "prev_close": 10,
            "high": 10,
            "low": 9,
            "open": 9.8,
            "vwap": 9.5,
            "minute_bars": [
                {"price": 9.4, "volume": 1000, "amount": 9400},
                {"price": 9.3, "volume": 1000, "amount": 9300},
                {"price": 9.2, "volume": 1000, "amount": 9200},
            ],
            "note": "实时行情",
        },
        expectation=expectation,
        volume_price=volume,
    )

    assert state.state == "EXTREME_LOW_STAGED_RISK_REDUCTION"
    assert state.recommended_action == "禁止低位追卖，首次有效反抽分批减仓25%"
    assert state.recommended_reduce_ratio == 0.25
    assert state.t_eligible is False
    assert any("执行时机门控" in item for item in state.evidence)


def test_extreme_low_with_untriggered_explicit_hard_stop_still_blocks_chasing_the_sell(db_session):
    holding = Holding(
        code="600107",
        name="未触发硬止损不追卖",
        quantity=1000,
        cost_price=10,
        current_price=9.05,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="交易剧本：硬止损 8.80，预期与量价转弱时降低风险",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        expectation_gap_score=-10,
        expectation_result="SLIGHTLY_WEAKER",
    )
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        price=9.05,
        high_price=10,
        low_price=9,
        prev_close=10,
        vwap=9.5,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        pattern="跌破VWAP",
        data_quality="realtime",
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 9.05,
            "prev_close": 10,
            "high": 10,
            "low": 9,
            "open": 9.8,
            "vwap": 9.5,
            "minute_bars": [
                {"price": 9.4, "volume": 1000, "amount": 9400},
                {"price": 9.3, "volume": 1000, "amount": 9300},
                {"price": 9.2, "volume": 1000, "amount": 9200},
            ],
            "note": "实时行情",
        },
        expectation=expectation,
        volume_price=volume,
    )

    assert state.hard_stop_price == 8.8
    assert state.state == "EXTREME_LOW_STAGED_RISK_REDUCTION"
    assert state.recommended_action == "禁止低位追卖，首次有效反抽分批减仓25%"
    assert state.recommended_reduce_ratio == 0.25
    assert any("硬止损尚未实际触发" in item for item in state.evidence)


def test_confirmed_v_reversal_revises_stale_invalidation_sell_advice(db_session):
    holding = Holding(
        code="600109", name="V形反转修正", quantity=1000, cost_price=10.2,
        current_price=10.0, total_asset=100000, position_type="普通持仓",
        next_discipline="预期证伪后等待量价二次确认，未设明确硬止损",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        stage="第一阶段确认", expected_open_low=2, expected_open_high=5.5,
        actual_open_pct=-2, actual_change_pct=0, expectation_gap_score=-18,
        expectation_result="INVALID", state_transition="EXPECTATION_INVALIDATED",
    )
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        stage="第一阶段确认", price=10.0, change_pct=0, open_price=9.7,
        high_price=10.1, low_price=9.0, prev_close=10, vwap=9.65,
        vwap_source="minute", minute_bar_count=8, vwap_reliable=True,
        price_vs_vwap=3.63, pattern="跌停开板V形修复", data_quality="realtime",
    )

    state = build_position_execution_state(
        db_session, holding,
        quote={
            "price": 10.0, "prev_close": 10, "open": 9.7, "high": 10.1, "low": 9.0,
            "minute_bars": [
                {"price": value, "volume": 1000, "amount": value * 1000}
                for value in [9.7, 9.3, 9.0, 9.2, 9.5, 9.7, 9.9, 10.0]
            ],
            "note": "东方财富实时行情",
        },
        expectation=expectation, volume_price=volume,
    )

    assert state.state == "REVERSAL_CONFIRMED_RISK_REDUCTION"
    assert state.recommended_action == "反转确认，利用反抽分批减仓25%"
    assert state.recommended_reduce_ratio == 0.25
    assert state.t_eligible is False
    assert state.volume_price_state == "REVERSAL_CONFIRMED"
    assert any("禁止在低点清仓" in item for item in state.evidence)
    assert any("暂停沿用低点时的卖出结论" in item for item in state.counter_evidence)


def test_confirmed_v_reversal_never_overrides_explicit_hard_stop(db_session):
    holding = Holding(
        code="600110", name="反转不覆盖硬止损", quantity=1000, cost_price=10,
        current_price=9.3, total_asset=100000, position_type="普通持仓",
        next_discipline="交易剧本：硬止损 9.60，跌破立即退出",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        price=9.3, prev_close=10, high_price=9.5, low_price=9.0, vwap=9.2,
        vwap_source="minute", minute_bar_count=8, vwap_reliable=True,
        pattern="跌停开板V形修复", data_quality="realtime",
    )

    state = build_position_execution_state(
        db_session, holding,
        quote={"price": 9.3, "prev_close": 10, "high": 9.5, "low": 9.0, "note": "实时行情"},
        volume_price=volume,
    )

    assert state.state == "EXIT_REQUIRED"
    assert state.recommended_action == "全部退出"
    assert state.recommended_reduce_ratio == 1


def test_cost_reference_breach_needs_dynamic_confirmation_before_adding_sell_score(db_session):
    holding = Holding(
        code="600108",
        name="成本参考需共振",
        quantity=1000,
        cost_price=10,
        current_price=9.65,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="尚未制定明确盘前止损计划",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    neutral = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 9.65, "prev_close": 10, "high": 10.2, "low": 9.3, "note": "实时行情"},
        persist=False,
    )

    assert neutral.stop_source == "cost_reference"
    assert neutral.recommended_reduce_ratio == 0
    assert any("该参考不单独计入卖出分" in item for item in neutral.counter_evidence)

    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        expectation_gap_score=-10,
        expectation_result="SLIGHTLY_WEAKER",
    )
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        price=9.65,
        high_price=10.2,
        low_price=9.3,
        prev_close=10,
        vwap=9.85,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        pattern="跌破VWAP",
        data_quality="realtime",
    )
    confirmed = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 9.65,
            "prev_close": 10,
            "high": 10.2,
            "low": 9.3,
            "vwap": 9.85,
            "minute_bars": [
                {"price": 9.9, "volume": 1000, "amount": 9900},
                {"price": 9.8, "volume": 1000, "amount": 9800},
                {"price": 9.65, "volume": 1000, "amount": 9650},
            ],
            "note": "实时行情",
        },
        expectation=expectation,
        volume_price=volume,
        persist=False,
    )

    # Before the script deadline the engine stages the reduction; after the
    # deadline the same confirmed evidence can escalate it.  The test verifies
    # the dynamic confirmation rather than pinning an action to wall-clock time.
    assert confirmed.recommended_reduce_ratio >= 0.50
    assert any("并与预期证伪或可靠量价破位共振" in item for item in confirmed.evidence)


def test_profit_snapshots_use_utc_storage_and_do_not_cross_local_trade_days(db_session):
    holding = Holding(
        code="600109",
        name="利润快照UTC隔离",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="只沿用本地交易日内的利润高点",
    )
    db_session.add(holding)
    db_session.flush()
    local_now = datetime.now()
    db_session.add_all([
        ProfitProtectionSnapshot(
            holding_id=holding.id,
            code=holding.code,
            captured_at=local_now - timedelta(days=1, minutes=10, hours=8),
            current_profit_pct=40,
            maximum_profit_pct=50,
            maximum_price=15,
        ),
        ProfitProtectionSnapshot(
            holding_id=holding.id,
            code=holding.code,
            captured_at=local_now - timedelta(minutes=10, hours=8),
            current_profit_pct=3,
            maximum_profit_pct=4,
            maximum_price=10.4,
        ),
    ])
    db_session.commit()
    db_session.refresh(holding)

    before_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.2, "prev_close": 10, "high": 10.3, "low": 10.1, "note": "实时行情"},
    )
    after_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    assert state.profit_snapshot.maximum_profit_pct == 4
    stored = (
        db_session.query(ProfitProtectionSnapshot)
        .filter(
            ProfitProtectionSnapshot.holding_id == holding.id,
            ProfitProtectionSnapshot.captured_at >= before_utc - timedelta(seconds=1),
            ProfitProtectionSnapshot.captured_at <= after_utc + timedelta(seconds=1),
        )
        .order_by(ProfitProtectionSnapshot.id.desc())
        .first()
    )
    assert stored is not None
    assert before_utc - timedelta(seconds=1) <= stored.captured_at <= after_utc + timedelta(seconds=1)


def test_same_expectation_and_volume_evidence_has_same_ratio_across_profit_sign(db_session):
    states = []
    for index, cost in enumerate((9.9, 10.1), start=1):
        code = f"60010{index + 2}"
        holding = Holding(
            code=code,
            name=f"盈亏符号{index}",
            quantity=1000,
            cost_price=cost,
            current_price=10,
            total_asset=100000,
            position_type="普通持仓",
            next_discipline="相同证据相同动作",
        )
        db_session.add(holding)
        db_session.flush()
        expectation = ExpectationSnapshot(
            trade_date=datetime.now().date().isoformat(),
            code=code,
            name=holding.name,
            stage="盘中确认",
            expectation_gap_score=-10,
            expectation_result="SLIGHTLY_WEAKER",
        )
        volume = VolumePriceSnapshot(
            trade_date=datetime.now().date().isoformat(),
            code=code,
            name=holding.name,
            price=10,
            open_price=10.05,
            high_price=10.1,
            low_price=9.7,
            prev_close=10,
            vwap=10.05,
            vwap_source="minute",
            minute_bar_count=5,
            vwap_reliable=True,
            pattern="跌破VWAP",
            data_quality="realtime",
        )
        states.append(build_position_execution_state(
            db_session,
            holding,
            quote={
                "price": 10,
                "prev_close": 10,
                "high": 10.1,
                "low": 9.7,
                "open": 10.05,
                "vwap": 10.05,
                "minute_bars": [
                    {"price": 10.04, "volume": 1000, "amount": 10040},
                    {"price": 10.02, "volume": 1000, "amount": 10020},
                    {"price": 10, "volume": 1000, "amount": 10000},
                ],
                "note": "实时行情",
            },
            expectation=expectation,
            volume_price=volume,
            persist=False,
        ))

    assert states[0].recommended_action == states[1].recommended_action == "减仓50%"
    assert states[0].recommended_reduce_ratio == states[1].recommended_reduce_ratio == 0.5


def test_previous_trade_date_snapshots_are_not_reused(db_session):
    holding = Holding(
        code="600105",
        name="隔日快照隔离",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="只使用当日快照",
    )
    db_session.add(holding)
    db_session.add(ExpectationSnapshot(
        trade_date=(datetime.now() - timedelta(days=1)).date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="昨日盘中",
        expectation_gap_score=-25,
        expectation_result="INVALID",
    ))
    db_session.add(VolumePriceSnapshot(
        trade_date=(datetime.now() - timedelta(days=1)).date().isoformat(),
        code=holding.code,
        name=holding.name,
        price=9,
        vwap=10,
        vwap_source="minute",
        minute_bar_count=20,
        vwap_reliable=True,
        pattern="冲高回落跌破VWAP",
        data_quality="realtime",
    ))
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.2,
            "prev_close": 10,
            "high": 10.3,
            "low": 10.1,
            "open": 10.1,
            "vwap": 10.15,
            "minute_bars": [
                {"price": 10.1, "volume": 1000, "amount": 10100},
                {"price": 10.15, "volume": 1000, "amount": 10150},
                {"price": 10.2, "volume": 1000, "amount": 10200},
            ],
            "note": "实时行情",
        },
    )

    assert state.expectation_state == "MATCHED"
    assert state.volume_price_state != "VOLUME_PRICE_WEAKENING"
    assert not any("阶段预期结果 INVALID" in item for item in state.evidence)


def test_explicit_positive_evidence_downgrades_non_hard_risk(db_session):
    holding = Holding(
        code="600106",
        name="正向反证降级",
        quantity=1000,
        cost_price=10,
        current_price=10.1,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="正向证据可降级非硬风险",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        expectation_gap_score=10,
        expectation_result="STRONGER",
    )
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        price=10.1,
        high_price=10.2,
        low_price=9.9,
        prev_close=10,
        vwap=10.02,
        vwap_source="minute",
        minute_bar_count=5,
        vwap_reliable=True,
        pattern="VWAP上方强势",
        data_quality="realtime",
    )
    seesaw = SimpleNamespace(
        risk_level="中高",
        signal="板块仍在观察",
        sector_ebb_trigger=[],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.1,
            "prev_close": 10,
            "high": 10.2,
            "low": 9.9,
            "open": 10,
            "vwap": 10.02,
            "minute_bars": [
                {"price": 10, "volume": 1000, "amount": 10000},
                {"price": 10.05, "volume": 1000, "amount": 10050},
                {"price": 10.1, "volume": 1000, "amount": 10100},
            ],
            "note": "实时行情",
        },
        seesaw=seesaw,
        expectation=expectation,
        volume_price=volume,
    )

    assert state.recommended_action == "继续持有"
    assert state.recommended_reduce_ratio == 0
    assert any("明确正向反证抵扣" in item for item in state.counter_evidence)


def test_high_risk_market_freezes_expansion_without_forcing_low_level_sell(db_session):
    holding = Holding(
        code="600206",
        name="弱市不追卖",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="个股未证伪则等待自身证据",
    )
    regime = MarketRegimeSnapshot(
        trade_date=datetime.now().date().isoformat(),
        captured_at=datetime.now(),
        source="测试全市场",
        data_quality="complete",
        coverage_ratio=1,
        confidence=0.95,
        regime_code="EXTREME_SHRINK_DECLINE",
        regime_name="极致缩量普跌",
        risk_level="极高",
        evidence_json='["上涨占比不足20%。", "主力资金大幅净流出。"]',
        forbidden_actions_json='["禁止新开仓", "禁止补仓摊低"]',
        missing_fields_json="[]",
    )
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        expectation_gap_score=8,
        expectation_result="STRONGER",
    )
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        price=10.2,
        high_price=10.25,
        low_price=10.05,
        prev_close=10,
        vwap=10.12,
        vwap_source="minute",
        minute_bar_count=6,
        vwap_reliable=True,
        pattern="VWAP上方强势",
        data_quality="realtime",
    )
    db_session.add_all([holding, regime, expectation, volume])
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.2,
            "prev_close": 10,
            "high": 10.25,
            "low": 10.05,
            "vwap": 10.12,
            "minute_bars": [
                {"price": 10.1, "volume": 1000, "amount": 10100},
                {"price": 10.2, "volume": 1000, "amount": 10200},
            ],
            "note": "实时行情",
        },
        expectation=expectation,
        volume_price=volume,
    )

    assert state.recommended_action == "持有但禁止加仓/抄底"
    assert state.recommended_reduce_ratio == 0
    assert state.t_eligible is False
    assert any("全市场状态：极致缩量普跌" in item for item in state.evidence)
    assert any("不在低位机械卖出" in item for item in state.evidence)
    assert any("禁止加仓、抄底和做T买回" in item for item in state.invalid_conditions)


def test_unknown_market_snapshot_is_labeled_but_not_used_as_sell_score(db_session):
    holding = Holding(
        code="600207",
        name="市场数据缺口",
        quantity=1000,
        cost_price=10,
        current_price=10.1,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="等待真实市场数据",
    )
    regime = MarketRegimeSnapshot(
        trade_date=datetime.now().date().isoformat(),
        captured_at=datetime.now(),
        source="unavailable",
        data_quality="missing",
        regime_code="UNKNOWN",
        regime_name="数据不足",
        risk_level="未知",
        evidence_json='["关键数据缺口。"]',
        missing_fields_json='["预计全天成交额/5日均额", "全市场主力净流入"]',
    )
    db_session.add_all([holding, regime])
    db_session.commit()
    db_session.refresh(holding)

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.1, "prev_close": 10, "high": 10.2, "low": 10.0, "note": "实时行情"},
    )

    assert state.recommended_reduce_ratio == 0
    assert state.recommended_action in {"观察但禁止加仓", "持有但禁止加仓/抄底"}
    assert any("全市场数据质量不足" in item for item in state.evidence)
    assert any("预计全天成交额/5日均额" in item for item in state.evidence)


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


def test_multi_evidence_shrinking_rise_fragility_upgrades_execution_risk(db_session):
    holding = Holding(
        code="600907",
        name="缩量诱多联合验证",
        quantity=1000,
        cost_price=10,
        current_price=10.2,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="禁止追高并按量价承接复核",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        price=10.2,
        change_pct=2.0,
        open_price=10.0,
        high_price=10.5,
        low_price=9.9,
        prev_close=10.0,
        amount=5,
        volume_ratio=0.65,
        vwap=10.05,
        vwap_source="minute",
        minute_bar_count=8,
        vwap_reliable=True,
        price_vs_vwap=1.49,
        high_drawdown=2.86,
        distance_recent_high_pct=-1.0,
        active_buy_amount=2.0,
        active_sell_amount=8.0,
        active_flow_source="provider_tick_direction",
        active_flow_estimated=False,
        pattern="缩量上涨脆弱·疑似诱多",
        data_quality="realtime",
        data_source="测试实时行情",
        evidence_json="[]",
        counter_evidence_json="[]",
    )
    seesaw = SimpleNamespace(
        risk_level="观察",
        signal="板块订单流边际转弱",
        sector_ebb_trigger=[],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
        holding_theme="半导体",
        sector_flow_kinetics_reliable=True,
        sector_flow_direction="NET_OUTFLOW",
        sector_flow_speed=-0.8,
        sector_flow_acceleration=-0.1,
        sector_flow_turning="OUTFLOW_ACCELERATING",
        sector_flow_signal="板块订单流流出加速",
        sector_flow_as_of=datetime.now().replace(microsecond=0).isoformat(),
        sector_flow_window_minutes=5,
        sector_net_inflow=-6.0,
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.2, "high": 10.5, "low": 9.9, "open": 10.0, "note": "东方财富实时行情"},
        volume_price=volume,
        seesaw=seesaw,
        persist=False,
    )

    assert state.volume_price_state == "VOLUME_PRICE_WEAKENING"
    assert state.recommended_reduce_ratio >= 0.25
    assert any("缩量上涨脆弱·疑似诱多" in item for item in state.evidence)
    assert any("量价恢复条件" in item for item in state.recovery_conditions)
    assert any(event.event_type == "PRICE_VOLUME_PATTERN_SHRINKING_RISE_FRAGILE" for event in state.events)


def test_pending_volume_rise_is_upgraded_after_reliable_sector_flow_arrives(db_session):
    holding = Holding(
        code="600908",
        name="跨层放量确认",
        quantity=1000,
        cost_price=10,
        current_price=10.4,
        total_asset=100000,
        position_type="普通持仓",
        next_discipline="等待板块共振完成确认",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(),
        code=holding.code,
        name=holding.name,
        stage="盘中确认",
        price=10.4,
        change_pct=4.0,
        open_price=10.0,
        high_price=10.45,
        low_price=9.95,
        prev_close=10.0,
        amount=8,
        volume_ratio=1.45,
        vwap=10.2,
        vwap_source="minute",
        minute_bar_count=8,
        vwap_reliable=True,
        price_vs_vwap=1.96,
        high_drawdown=0.48,
        ma5=10.0,
        distance_recent_high_pct=-5.0,
        active_flow_source="unavailable",
        active_flow_estimated=False,
        pattern="放量上涨待承接确认",
        data_quality="realtime",
        data_source="测试实时行情",
        evidence_json="[]",
        counter_evidence_json="[]",
    )
    seesaw = SimpleNamespace(
        risk_level="低",
        signal="板块订单流边际改善",
        sector_ebb_trigger=[],
        stock_weakening_trigger=[],
        profit_drawdown_trigger=[],
        holding_theme="半导体",
        sector_flow_kinetics_reliable=True,
        sector_flow_direction="NET_INFLOW",
        sector_flow_speed=0.8,
        sector_flow_acceleration=0.1,
        sector_flow_turning="INFLOW_ACCELERATING",
        sector_flow_signal="板块订单流流入加速",
        sector_flow_as_of=datetime.now().replace(microsecond=0).isoformat(),
        sector_flow_window_minutes=5,
        sector_net_inflow=8.0,
    )

    state = build_position_execution_state(
        db_session,
        holding,
        quote={"price": 10.4, "high": 10.45, "low": 9.95, "open": 10.0, "note": "东方财富实时行情"},
        volume_price=volume,
        seesaw=seesaw,
        persist=False,
    )

    assert state.volume_price_state == "REPAIR_CONFIRMED"
    assert any("放量上涨确认" in item for item in state.evidence)
    assert any(event.event_type == "PRICE_VOLUME_PATTERN_VOLUME_RISE_CONFIRMED" for event in state.events)


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
        plan_date=datetime.now().date().isoformat(),
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
        trade_date=datetime.now().date().isoformat(),
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
        next_discipline="按状态迁移记录，硬止损 9.60",
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


def test_high_sell_window_requires_target_and_multiple_weakening_families(db_session):
    holding = Holding(
        code="600310", name="冲高兑现测试", quantity=1000, cost_price=10,
        current_price=10.4, total_asset=100000, position_type="趋势仓",
        next_discipline="冲高按多证据分批兑现",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(NextDayPlan(
        plan_date=datetime.now().date().isoformat(), plan_type="holding",
        holding_id=holding.id, code=holding.code, name=holding.name,
        quantity=1000, cost_price=10, current_price=10.4,
        trim_price=10.8, final_risk_price=9.5, reduce_price=9.8,
    ))
    db_session.commit()
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        price=10.4, prev_close=10, high_price=11, low_price=10.3,
        vwap=10.65, vwap_source="minute", minute_bar_count=12, vwap_reliable=True,
        attack_efficiency=0.08, pullback_sell_ratio=68,
        pattern="冲高回落跌破VWAP", data_quality="realtime",
    )
    seesaw = SimpleNamespace(
        risk_level="中高", signal="板块走弱", sector_net_inflow=-5,
        sector_acceleration=-2, theme_flow_pullback_pct=28,
        sector_ebb_trigger=["板块资金转弱。"], stock_weakening_trigger=[],
        profit_drawdown_trigger=[], theme_flow_current=2, theme_flow_peak=8,
        holding_theme="半导体链", pullback_from_high_pct=5,
    )

    state = build_position_execution_state(
        db_session, holding,
        quote={
            "price": 10.4, "prev_close": 10, "high": 11, "low": 10.3,
            "open": 10.7, "vwap": 10.65,
            "minute_bars": [
                {"price": 10.8, "volume": 1000, "amount": 10800},
                {"price": 10.6, "volume": 1200, "amount": 12720},
                {"price": 10.4, "volume": 1500, "amount": 15600},
            ],
            "note": "实时行情",
        },
        seesaw=seesaw, volume_price=volume,
    )

    assert state.high_sell_signal is not None
    assert state.high_sell_signal.status == "ACTIVE"
    assert state.high_sell_signal.recommended_ratio == 0.5
    assert any("次日计划兑现位" in item for item in state.high_sell_signal.evidence)
    assert any("VWAP" in item for item in state.high_sell_signal.evidence)
    assert any(event.event_type == "HIGH_SELL_WINDOW" for event in state.events)


def test_high_sell_window_is_not_triggered_by_profit_or_target_alone(db_session):
    holding = Holding(
        code="600313", name="不按浮盈猜顶", quantity=1000, cost_price=10,
        current_price=10.7, total_asset=100000, position_type="趋势仓",
        next_discipline="没有结构走弱不猜顶",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(NextDayPlan(
        plan_date=datetime.now().date().isoformat(), plan_type="holding",
        holding_id=holding.id, code=holding.code, name=holding.name,
        quantity=1000, cost_price=10, current_price=10.7,
        trim_price=10.8, final_risk_price=9.5, reduce_price=9.8,
    ))
    db_session.commit()
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        price=10.7, prev_close=10, high_price=10.9, low_price=10.5,
        vwap=10.6, vwap_source="minute", minute_bar_count=8, vwap_reliable=True,
        attack_efficiency=0.65, pullback_sell_ratio=20,
        pattern="VWAP上方强势", data_quality="realtime",
    )

    state = build_position_execution_state(
        db_session, holding,
        quote={
            "price": 10.7, "prev_close": 10, "high": 10.9, "low": 10.5,
            "open": 10.4, "vwap": 10.6,
            "minute_bars": [
                {"price": 10.55, "volume": 1000, "amount": 10550},
                {"price": 10.65, "volume": 1000, "amount": 10650},
                {"price": 10.7, "volume": 1000, "amount": 10700},
            ],
            "note": "实时行情",
        },
        volume_price=volume,
    )

    assert state.high_sell_signal is not None
    assert state.high_sell_signal.status == "WATCH"
    assert state.high_sell_signal.recommended_ratio == 0
    assert any("结构证据" in item for item in state.high_sell_signal.missing_conditions)


def test_panic_guard_does_not_grant_permission_to_average_down(db_session):
    holding = Holding(
        code="600311", name="恐慌保护测试", quantity=1000, cost_price=10,
        current_price=9.3, total_asset=100000, position_type="普通持仓",
        next_discipline="无固定硬止损不在低位追卖",
    )
    db_session.add(holding)
    db_session.commit()
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        price=9.3, prev_close=10, high_price=10.1, low_price=9.0,
        vwap=9.2, vwap_source="minute", minute_bar_count=12, vwap_reliable=True,
        pattern="跌停开板V形修复", data_quality="realtime",
    )

    state = build_position_execution_state(
        db_session, holding,
        quote={
            "price": 9.3, "prev_close": 10, "high": 10.1, "low": 9,
            "open": 9.1, "vwap": 9.2,
            "minute_bars": [
                {"price": 9.05, "volume": 2000, "amount": 18100},
                {"price": 9.2, "volume": 1500, "amount": 13800},
                {"price": 9.3, "volume": 1500, "amount": 13950},
            ],
            "note": "实时行情",
        },
        volume_price=volume,
    )

    assert state.panic_sell_guard is not None
    assert state.panic_sell_guard.status == "ACTIVE"
    assert state.contrarian_add_signal is not None
    assert state.contrarian_add_signal.status == "BLOCKED"
    assert "不恐慌卖出不等于允许抄底" in state.contrarian_add_signal.action


def test_contrarian_add_is_only_eligible_after_all_four_gates(db_session):
    holding = Holding(
        code="600312", name="反转评估测试", quantity=1000, cost_price=10,
        current_price=10, total_asset=100000, position_type="趋势仓",
        next_discipline="四道闸门齐全才评估补仓",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add_all([
        MarketRegimeSnapshot(
            trade_date=datetime.now().date().isoformat(), captured_at=datetime.now(),
            source="测试全市场", data_quality="complete", coverage_ratio=1,
            confidence=0.95, regime_code="BROAD_REPAIR", regime_name="放量修复",
            risk_level="低", missing_fields_json="[]",
        ),
        NextDayPlan(
            plan_date=datetime.now().date().isoformat(), plan_type="holding",
            holding_id=holding.id, code=holding.code, name=holding.name,
            quantity=1000, cost_price=10, current_price=10,
            trim_price=11.2, final_risk_price=9.8, reduce_price=9.9,
        ),
    ])
    db_session.commit()
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        price=10, prev_close=10, high_price=10.3, low_price=9.2,
        vwap=9.9, vwap_source="minute", minute_bar_count=15, vwap_reliable=True,
        pattern="水下V形反转站回VWAP", data_quality="realtime",
    )
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        stage="盘中确认", expectation_gap_score=6, expectation_result="STRONGER",
    )
    seesaw = SimpleNamespace(
        risk_level="低", signal="板块资金转强", sector_net_inflow=6,
        sector_acceleration=2, theme_flow_pullback_pct=0,
        sector_ebb_trigger=[], stock_weakening_trigger=[], profit_drawdown_trigger=[],
        theme_flow_current=6, theme_flow_peak=6, holding_theme="半导体链",
        pullback_from_high_pct=1,
    )

    state = build_position_execution_state(
        db_session, holding,
        quote={
            "price": 10, "prev_close": 10, "high": 10.3, "low": 9.2,
            "open": 9.4, "vwap": 9.9,
            "minute_bars": [
                {"price": 9.3, "volume": 2000, "amount": 18600},
                {"price": 9.8, "volume": 1800, "amount": 17640},
                {"price": 10, "volume": 1600, "amount": 16000},
            ],
            "note": "实时行情",
        },
        seesaw=seesaw, expectation=expectation, volume_price=volume,
    )

    assert state.contrarian_add_signal is not None
    assert state.contrarian_add_signal.status == "ELIGIBLE"
    assert any("风险收益比" in item for item in state.contrarian_add_signal.evidence)
    assert any(event.event_type == "CONTRARIAN_ADD_EVALUATION" for event in state.events)


def _global_snapshot(now: datetime, change_pct: float) -> dict:
    as_of = (now - timedelta(hours=12)).isoformat()
    return {
        "data_quality": "ok",
        "us_indices": [
            {"symbol": "SPX", "name": "标普500", "status": "ok", "change_pct": change_pct, "as_of": as_of, "source": "eastmoney"},
            {"symbol": "NDX", "name": "纳斯达克100", "status": "ok", "change_pct": change_pct, "as_of": as_of, "source": "eastmoney"},
            {"symbol": "DJIA", "name": "道琼斯", "status": "ok", "change_pct": change_pct, "as_of": as_of, "source": "eastmoney"},
        ],
        "korea_indices": [],
        "korea_equities": [],
        "us_sector_rank": [],
    }


def test_fresh_broad_global_weakness_freezes_only_new_exposure():
    now = datetime.now()
    gate = global_market_execution_gate(_global_snapshot(now, -2.0), now=now)

    assert gate["score"] == -2
    assert gate["freeze_expansion"] is True
    assert any("冻结新增风险" in item for item in gate["evidence"])


def test_execution_loader_prefers_newer_persisted_global_snapshot(
    db_session,
    monkeypatch,
):
    from app.api.helpers import execution
    from app.services.sector_evidence_history import persist_global_evidence_snapshot

    stale_cache = _global_snapshot(datetime.now(), -1.0)
    stale_cache.update({
        "generated_at": "2026-07-20T08:30:00+08:00",
        "as_of": "2026-07-20T08:30:00+08:00",
        "source": ["worker-cache"],
    })
    persisted = _global_snapshot(datetime.now(), -2.5)
    persisted.update({
        "generated_at": "2026-07-20T09:00:00+08:00",
        "as_of": "2026-07-20T09:00:00+08:00",
        "source": ["database-worker"],
    })
    row = persist_global_evidence_snapshot(db_session, persisted)
    monkeypatch.setattr(
        execution.global_market_service,
        "read_cached_snapshot",
        lambda: stale_cache,
    )

    result = _load_global_market_snapshot(db=db_session)

    assert result is not None
    assert result["snapshot_origin"] == "database"
    assert result["snapshot_id"] == row.id
    assert result["us_indices"][0]["change_pct"] == -2.5


def test_stale_global_quotes_are_ignored_instead_of_becoming_a_risk_score():
    now = datetime.now()
    snapshot = _global_snapshot(now, -3.0)
    stale = (now - timedelta(days=6)).isoformat()
    for item in snapshot["us_indices"]:
        item["as_of"] = stale

    gate = global_market_execution_gate(snapshot, now=now)

    assert gate["score"] == 0
    assert gate["freeze_expansion"] is False
    assert gate["valid_quote_count"] == 0


def test_missing_global_change_is_not_coerced_to_a_flat_quote():
    now = datetime.now()
    as_of = (now - timedelta(hours=12)).isoformat()
    gate = global_market_execution_gate({
        "data_quality": "degraded",
        "us_indices": [
            {"symbol": "SPX", "name": "标普500", "status": "ok", "change_pct": None, "as_of": as_of, "source": "eastmoney"},
            {"symbol": "NDX", "name": "纳斯达克100", "status": "ok", "change_pct": "bad", "as_of": as_of, "source": "eastmoney"},
        ],
    }, now=now)

    assert gate["valid_quote_count"] == 0
    assert gate["score"] == 0
    assert gate["freeze_expansion"] is False


def test_strategic_macro_and_authorised_flows_enter_only_the_expansion_gate():
    now = datetime.now(timezone(timedelta(hours=8)))
    quote_as_of = (now - timedelta(hours=12)).isoformat()
    metric_as_of = (now - timedelta(hours=2)).isoformat()

    def metric(metric_id, name, kind, value, *, direction=None, change_pct=None):
        return {
            "metric_id": metric_id,
            "name": name,
            "status": "ok",
            "value": value,
            "direction": direction,
            "change_pct": change_pct,
            "metric_kind": kind,
            "source": "licensed adapter",
            "source_url": f"https://licensed.example.test/{metric_id}",
            "published_at": metric_as_of,
            "data_quality": "official_audited",
        }

    snapshot = {
        "data_quality": "degraded",
        "quote_quality": "ok",
        "institutional_flow_quality": "ok",
        "us_indices": [],
        "korea_indices": [],
        "korea_equities": [],
        "us_sector_rank": [],
        "strategic_assets": [
            {"symbol": "EWY", "name": "韩国ETF", "status": "delayed", "change_pct": -2.0, "as_of": quote_as_of, "source": "yahoo"},
            {"symbol": "MU", "name": "美光", "status": "delayed", "change_pct": -2.4, "as_of": quote_as_of, "source": "yahoo"},
        ],
        "macro_indicators": [
            {"symbol": "USDKRW", "name": "美元兑韩元", "status": "delayed", "change_pct": 1.0, "as_of": quote_as_of, "source": "yahoo"},
            {"symbol": "DXY", "name": "美元指数", "status": "delayed", "change_pct": 0.7, "as_of": quote_as_of, "source": "yahoo"},
        ],
        "etf_flows": [metric("EWY_NET", "EWY净申赎", "etf_share_creation_redemption", -1, direction="outflow")],
        "korea_foreign_flows": [metric("KR_FLOW", "韩国外资净买卖", "korea_foreign_net_flow", -50)],
        "korea_leverage_products": [metric("KR_LEV", "韩国杠杆产品规模", "korea_single_stock_leverage_product", 100, change_pct=8)],
        "official_rates": [metric("KR_RATE", "韩国官方利率", "official_interest_rate", 2.5)],
    }

    gate = global_market_execution_gate(snapshot, now=now)

    assert gate["freeze_expansion"] is True
    assert gate["freeze_actions"] == ["追高", "补仓", "做T回补"]
    assert gate["can_trigger_sell"] is False
    assert gate["valid_quote_count"] == 4
    assert gate["valid_official_metric_count"] == 4
    assert any("战略资产共振偏弱" in item for item in gate["evidence"])
    assert any("汇率/美元/利率代理共振收紧" in item for item in gate["evidence"])
    assert any("可审计机构/杠杆证据共振偏弱" in item for item in gate["evidence"])


def test_official_metric_kind_and_quality_must_match_configured_evidence_group():
    now = datetime.now(timezone(timedelta(hours=8)))
    published_at = (now - timedelta(hours=2)).isoformat()
    base = {
        "metric_id": "EWY_NET",
        "name": "EWY净申赎",
        "status": "ok",
        "value": -1,
        "direction": "outflow",
        "metric_kind": "official_interest_rate",
        "source": "licensed adapter",
        "source_url": "https://licensed.example.test/EWY_NET",
        "published_at": published_at,
        "data_quality": "official",
    }
    snapshot = {
        "quote_quality": "missing",
        "institutional_flow_quality": "partial",
        "etf_flows": [base],
    }

    mismatched = global_market_execution_gate(snapshot, now=now)
    assert mismatched["valid_official_metric_count"] == 0
    assert "ETF真实份额申赎" in mismatched["missing_evidence_groups"]

    base["metric_kind"] = "etf_share_creation_redemption"
    base["data_quality"] = "ok"
    unaudited = global_market_execution_gate(snapshot, now=now)
    assert unaudited["valid_official_metric_count"] == 0

    base["data_quality"] = "official"
    base["published_at"] = (now - timedelta(hours=2)).replace(tzinfo=None).isoformat()
    timezone_missing = global_market_execution_gate(snapshot, now=now)
    assert timezone_missing["valid_official_metric_count"] == 0


def test_missing_official_global_metrics_stay_unknown_instead_of_zero():
    now = datetime.now()
    snapshot = {
        "data_quality": "missing",
        "quote_quality": "missing",
        "institutional_flow_quality": "missing",
        "etf_flows": [{"status": "unavailable", "value": None}],
        "korea_foreign_flows": [{"status": "unavailable", "value": None}],
        "korea_leverage_products": [{"status": "unavailable", "value": None}],
        "official_rates": [{"status": "unavailable", "value": None}],
    }

    gate = global_market_execution_gate(snapshot, now=now)

    assert gate["score"] == 0
    assert gate["freeze_expansion"] is False
    assert gate["valid_official_metric_count"] == 0
    assert set(gate["missing_evidence_groups"]) == {
        "ETF真实份额申赎",
        "韩国外资净买卖",
        "韩国单股杠杆产品",
        "韩国官方利率",
    }


def test_global_risk_never_becomes_a_standalone_sell_instruction(db_session):
    holding = Holding(
        code="600314", name="外围门控测试", quantity=1000, cost_price=10,
        current_price=10.5, total_asset=100000, position_type="趋势仓",
        next_discipline="外围弱势只冻结扩仓",
    )
    db_session.add(holding)
    db_session.flush()
    db_session.add(MarketRegimeSnapshot(
        trade_date=datetime.now().date().isoformat(), captured_at=datetime.now(),
        source="测试全市场", data_quality="complete", coverage_ratio=1,
        confidence=0.95, regime_code="BROAD_REPAIR", regime_name="放量修复",
        risk_level="低", missing_fields_json="[]",
    ))
    db_session.commit()
    volume = VolumePriceSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        price=10.5, prev_close=10, high_price=10.6, low_price=10.1,
        vwap=10.4, vwap_source="minute", minute_bar_count=12, vwap_reliable=True,
        pattern="VWAP上方强势", data_quality="realtime",
    )
    expectation = ExpectationSnapshot(
        trade_date=datetime.now().date().isoformat(), code=holding.code, name=holding.name,
        stage="盘中确认", expectation_gap_score=0, expectation_result="MATCHED",
    )
    now = datetime.now()

    state = build_position_execution_state(
        db_session,
        holding,
        quote={
            "price": 10.5, "prev_close": 10, "high": 10.6, "low": 10.1,
            "open": 10.2, "vwap": 10.4,
            "minute_bars": [
                {"price": 10.3, "volume": 1000, "amount": 10300},
                {"price": 10.4, "volume": 1000, "amount": 10400},
                {"price": 10.5, "volume": 1000, "amount": 10500},
            ],
            "note": "实时行情",
        },
        expectation=expectation,
        volume_price=volume,
        global_cues=_global_snapshot(now, -2.0),
    )

    assert state.recommended_reduce_ratio == 0
    assert "禁止加仓" in state.recommended_action
    assert not any(item in state.recommended_action for item in ("减仓", "退出", "清仓"))
    assert any("绝不单独触发减仓或清仓" in item for item in state.evidence)


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
        global_cues=_global_snapshot(datetime(2026, 7, 12, 9, 35), -2.0),
    )

    assert nearest_sample_label(datetime(2026, 7, 12, 9, 36)) == "09:35"
    assert sample.event_type == "INTRADAY_EVIDENCE_SNAPSHOT"
    assert sample.confirmed is True
    assert sample.recommendation_id == state.recommendation.id
    assert "09:35" in sample.group_key
    assert any("外围环境扩仓分" in item for item in state.evidence)
