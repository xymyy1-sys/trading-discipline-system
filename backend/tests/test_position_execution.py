from types import SimpleNamespace

from app.api.helpers.execution import build_position_execution_state
from app.models.trading import Holding


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
