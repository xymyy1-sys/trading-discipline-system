import json
from datetime import datetime
from types import SimpleNamespace

from app.api.helpers.plan_calc import (
    _default_next_day_plan,
    _next_trade_date,
    _opening_branch,
    refresh_plan_stage_from_evidence,
)
from app.models.trading import ExpectationSnapshot, Holding, IntradayEvidenceEvent, NextDayPlan, VolumePriceSnapshot
from app.schemas.trading import VolumePriceSnapshotOut
from app.services import trading_calendar
from app.services.intraday_collector import _refresh_collected_holding_plan
from app.services.next_day_expectations import (
    generate_next_day_expectations,
    rotate_watchlist_and_generate_next_day_expectations,
)


def _holding(code: str = "600101", *, current_price: float = 10) -> Holding:
    return Holding(
        code=code,
        name="闭环样本",
        quantity=1000,
        cost_price=9,
        current_price=current_price,
        total_asset=100_000,
        position_type="趋势持仓",
    )


def _volume(
    *,
    pattern: str,
    reliable: bool,
    bars: int,
    open_price: float = 9.7,
    price: float = 9.6,
) -> VolumePriceSnapshotOut:
    return VolumePriceSnapshotOut(
        trade_date="2026-07-23",
        code="600101",
        name="闭环样本",
        stage="五分钟确认",
        captured_at=datetime(2026, 7, 23, 9, 36),
        price=price,
        change_pct=(price / 10 - 1) * 100,
        open_price=open_price,
        high_price=max(open_price, price),
        low_price=min(open_price, price),
        prev_close=10,
        volume=100_000,
        amount=1_000_000,
        vwap=9.75,
        vwap_reliable=reliable,
        minute_bar_count=bars,
        pattern=pattern,
        data_quality="realtime",
        data_source="测试真实分钟行情",
    )


def test_next_plan_date_uses_exchange_holiday_calendar():
    trading_calendar._reset_calendar_cache_for_tests()

    assert _next_trade_date(datetime(2026, 9, 30).date()) == "2026-10-08"


def test_close_generates_holding_plan_from_same_day_close_and_replay_is_idempotent(db_session, monkeypatch):
    from app.api.routes import stocks

    trading_calendar._reset_calendar_cache_for_tests()
    monkeypatch.setattr(stocks, "watchlist_recommendations", lambda _db: [])
    holding = _holding(current_price=8)
    db_session.add(holding)
    db_session.add(VolumePriceSnapshot(
        trade_date="2026-09-30",
        code=holding.code,
        name=holding.name,
        stage="收盘",
        captured_at=datetime(2026, 9, 30, 15, 0),
        price=12,
        change_pct=4,
        open_price=11,
        high_price=12.2,
        low_price=10.8,
        prev_close=11.54,
        volume=2_000_000,
        amount=24_000_000,
        turnover=5,
        volume_ratio=1.4,
        vwap=11.8,
        vwap_reliable=True,
        minute_bar_count=240,
        price_vs_vwap=1.69,
        high_drawdown=1.64,
        pattern="VWAP上方强势",
        data_quality="realtime",
    ))
    db_session.commit()

    assert generate_next_day_expectations(db_session, completed_trade_date="2026-09-30") == 1
    plan = db_session.query(NextDayPlan).filter_by(plan_date="2026-10-08", code=holding.code, plan_type="holding").one()
    assert plan.current_price == 12
    assert plan.position_ratio == 0.12
    assert plan.confirm_price == 12
    assert plan.buyback_price == 11.64
    assert plan.reduce_price == 11.64
    lifecycle = json.loads(plan.auction_plan)
    assert lifecycle["baseline_trade_date"] == "2026-09-30"
    lifecycle.update({
        "selected_branch": "low_open_selloff",
        "selected_branch_label": "低开下杀",
        "branch_status": "active",
        "current_advice": "盘中已选择的建议",
        "advice_revision": 7,
        "advice_history": [{"revision": 7, "state": "active", "advice": "盘中已选择的建议"}],
        "stage_checks": [{"stage": "五分钟量价确认", "status": "观察"}],
    })
    plan.auction_plan = json.dumps(lifecycle, ensure_ascii=False)
    plan.outperform_action = "用户编辑：超预期时保留核心仓"
    db_session.commit()

    # Simulate a morning restart replaying the same completed close.
    generate_next_day_expectations(db_session, completed_trade_date="2026-09-30")
    db_session.refresh(plan)
    replayed = json.loads(plan.auction_plan)
    assert replayed["selected_branch"] == "low_open_selloff"
    assert replayed["advice_revision"] == 7
    assert replayed["advice_history"][0]["advice"] == "盘中已选择的建议"
    assert replayed["stage_checks"][0]["stage"] == "五分钟量价确认"
    assert plan.outperform_action == "用户编辑：超预期时保留核心仓"


def test_watchlist_provider_delay_does_not_block_holding_plan(db_session, monkeypatch):
    from app.api.routes import stocks

    holding = _holding()
    db_session.add(holding)
    db_session.commit()
    monkeypatch.setattr(stocks, "_watchlist_generation_completed", lambda _db, _date: False)
    monkeypatch.setattr(stocks, "_watchlist_recommendations", lambda _db, persist_rotation=False: [])
    monkeypatch.setattr(stocks, "watchlist_recommendations", lambda _db: [])

    completed = rotate_watchlist_and_generate_next_day_expectations(
        db_session,
        completed_trade_date="2026-07-22",
    )

    assert completed is False
    assert db_session.query(NextDayPlan).filter_by(
        plan_date="2026-07-23",
        code=holding.code,
        plan_type="holding",
    ).count() == 1
    assert db_session.query(ExpectationSnapshot).filter_by(
        trade_date="2026-07-23",
        code=holding.code,
        stage="次日盘前预期",
    ).count() == 1


def test_opening_branches_are_mutually_exclusive():
    holding = _holding()
    plan = _default_next_day_plan(holding, "2026-07-23", quote={"price": 10})
    expectation = SimpleNamespace(expected_open_low=-1, expected_open_high=1)

    low = _opening_branch(plan, expectation, {"open": 9.7, "prev_close": 10}, _volume(pattern="量价中性", reliable=False, bars=0))
    middle = _opening_branch(plan, expectation, {"open": 10, "prev_close": 10}, _volume(pattern="量价中性", reliable=False, bars=0))
    high = _opening_branch(plan, expectation, {"open": 10.3, "prev_close": 10}, _volume(pattern="量价中性", reliable=False, bars=0))
    gap = _opening_branch(plan, expectation, {}, _volume(pattern="量价中性", reliable=False, bars=0, open_price=0))

    assert [low[0], middle[0], high[0], gap[0]] == [
        "low_open_selloff",
        "range_open_balance",
        "high_open_rally",
        "data_gap",
    ]


def test_plan_cannot_select_opening_branch_before_auction_finishes(db_session):
    holding = _holding()
    db_session.add(holding)
    db_session.flush()
    plan = _default_next_day_plan(holding, "2026-07-23", quote={"price": 10})
    db_session.add(plan)
    baseline = ExpectationSnapshot(
        trade_date="2026-07-23",
        code=holding.code,
        name=holding.name,
        stage="次日盘前预期",
        expected_open_low=-1,
        expected_open_high=1,
    )
    db_session.add(baseline)
    db_session.commit()

    refresh_plan_stage_from_evidence(
        plan,
        db_session,
        expectation=baseline,
        volume_price=_volume(pattern="量价中性", reliable=False, bars=0, open_price=10.5),
        quote={"price": 10.5, "open": 10.5, "prev_close": 10, "high": 10.5},
        now=datetime(2026, 7, 23, 9, 20),
    )

    auction = json.loads(plan.auction_plan)
    assert auction["selected_branch"] == "data_gap"
    assert auction["branch_status"] == "pending"
    assert "竞价尚未结束" in auction["branch_reason"]


def test_low_open_without_five_reliable_minutes_cannot_upgrade_to_critical(db_session):
    holding = _holding()
    db_session.add(holding)
    db_session.flush()
    plan = _default_next_day_plan(holding, "2026-07-23", quote={"price": 10})
    db_session.add(plan)
    db_session.add(ExpectationSnapshot(
        trade_date="2026-07-23",
        code=holding.code,
        name=holding.name,
        stage="次日盘前预期",
        expected_open_low=-1,
        expected_open_high=1,
    ))
    db_session.commit()
    expectation = SimpleNamespace(
        expectation_result="INVALID",
        state_transition="EXPECTATION_INVALIDATED",
        expected_open_low=-1,
        expected_open_high=1,
        evidence=[],
    )
    volume = _volume(pattern="跌破VWAP", reliable=True, bars=3)

    refresh_plan_stage_from_evidence(
        plan,
        db_session,
        expectation=expectation,
        volume_price=volume,
        quote={"price": 9.6, "open": 9.7, "prev_close": 10, "high": 9.8},
        now=datetime(2026, 7, 23, 9, 33),
    )

    auction = json.loads(plan.auction_plan)
    assert auction["selected_branch"] == "low_open_selloff"
    assert auction["advice_level"] == "warning"
    assert "真实分钟VWAP或分钟样本不足" in next(
        item["required_action"] for item in auction["stage_checks"] if item["stage"] == "五分钟量价确认"
    )


def test_plan_advice_upgrades_then_is_withdrawn_by_reversal(db_session):
    holding = _holding()
    db_session.add(holding)
    db_session.flush()
    plan = _default_next_day_plan(holding, "2026-07-23", quote={"price": 10})
    db_session.add(plan)
    baseline = ExpectationSnapshot(
        trade_date="2026-07-23",
        code=holding.code,
        name=holding.name,
        stage="次日盘前预期",
        expected_open_low=-1,
        expected_open_high=1,
    )
    db_session.add(baseline)
    db_session.commit()
    invalid = SimpleNamespace(
        expectation_result="INVALID",
        state_transition="EXPECTATION_INVALIDATED",
        expected_open_low=-1,
        expected_open_high=1,
        evidence=[],
    )
    failed_volume = _volume(pattern="跌破VWAP", reliable=True, bars=6)
    refresh_plan_stage_from_evidence(
        plan,
        db_session,
        expectation=invalid,
        volume_price=failed_volume,
        quote={"price": 9.6, "open": 9.7, "prev_close": 10, "high": 9.8},
        now=datetime(2026, 7, 23, 9, 36),
    )
    critical = json.loads(plan.auction_plan)
    assert critical["advice_level"] == "critical"
    assert critical["advice_change"] == "upgraded"

    repaired = SimpleNamespace(
        expectation_result="INVALID",
        state_transition="INVALIDATION_TO_REVERSAL",
        expected_open_low=-1,
        expected_open_high=1,
        evidence=[],
    )
    repaired_volume = _volume(
        pattern="水下V形反转站回VWAP",
        reliable=True,
        bars=12,
        open_price=9.7,
        price=10.1,
    )
    refresh_plan_stage_from_evidence(
        plan,
        db_session,
        expectation=repaired,
        volume_price=repaired_volume,
        quote={"price": 10.1, "open": 9.7, "prev_close": 10, "high": 10.2},
        now=datetime(2026, 7, 23, 9, 43),
    )
    restored = json.loads(plan.auction_plan)
    assert restored["advice_level"] == "observe"
    assert restored["advice_change"] == "withdrawn"
    assert "撤销低点立即卖出" in restored["current_advice"]
    assert restored["advice_history"][-2]["state"] == "withdrawn"
    assert restored["advice_history"][-1]["state"] == "active"


def test_collector_plan_refresh_persists_event_and_execution_link(db_session):
    holding = _holding()
    db_session.add(holding)
    db_session.flush()
    plan = _default_next_day_plan(holding, "2026-07-23", quote={"price": 10})
    db_session.add(plan)
    db_session.add(ExpectationSnapshot(
        trade_date="2026-07-23",
        code=holding.code,
        name=holding.name,
        stage="次日盘前预期",
        expected_open_low=-1,
        expected_open_high=1,
    ))
    db_session.add(ExpectationSnapshot(
        trade_date="2026-07-23",
        code=holding.code,
        name=holding.name,
        stage="五分钟确认",
        expected_open_low=-1,
        expected_open_high=1,
        expectation_result="WEAKER",
        state_transition="CONSENSUS_TO_DIVERGENCE",
    ))
    db_session.commit()
    state = SimpleNamespace(
        id=88,
        state="REDUCE_REQUIRED",
        recommended_action="减仓25%",
        recommendation=None,
    )

    assert _refresh_collected_holding_plan(
        db_session,
        holding,
        _volume(pattern="跌破VWAP", reliable=True, bars=6),
        state,
        now=datetime(2026, 7, 23, 9, 36),
    ) is True

    db_session.refresh(plan)
    auction = json.loads(plan.auction_plan)
    assert auction["selected_branch"] == "low_open_selloff"
    assert auction["execution_state_advice"] == "减仓25%"
    event = db_session.query(IntradayEvidenceEvent).filter(
        IntradayEvidenceEvent.event_type.like("PLAN_ADVICE_%")
    ).one()
    assert "计划建议" in event.evidence_json


def test_default_plan_list_uses_active_today_plan_during_session(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.api.helpers.plan_calc.shanghai_now_naive",
        lambda _now=None: datetime(2026, 7, 23, 10, 0),
    )
    db_session.add(NextDayPlan(
        plan_date="2026-07-23",
        plan_type="holding",
        code="600101",
        name="今日执行计划",
    ))
    db_session.add(NextDayPlan(
        plan_date="2026-07-24",
        plan_type="holding",
        code="600102",
        name="明日计划",
    ))
    db_session.commit()

    response = client.get("/api/next-day-plans")

    assert response.status_code == 200
    assert [row["code"] for row in response.json()] == ["600101"]
