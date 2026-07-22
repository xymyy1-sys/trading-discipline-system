from datetime import datetime

from app.services.flow_kinetics import (
    analyze_flow_kinetics,
    classify_price_volume_flow_alerts,
    classify_volume_price_pattern,
)


def test_flow_kinetics_filters_future_points_and_uses_trading_minutes():
    as_of = datetime(2026, 7, 15, 10, 5, 0)
    result = analyze_flow_kinetics(
        [
            {"time": "09:55", "value": -12.0},
            {"time": "10:00", "value": -8.0},
            {"time": "10:10", "value": 999.0},  # future data must be ignored
        ],
        current_value=-2.0,
        change_pct=-1.2,
        as_of=as_of,
    )

    assert result.reliable is True
    assert result.speed == 1.2
    assert result.turning == "OUTFLOW_NARROWING"
    assert result.as_of == "2026-07-15 10:05:00"
    assert "999" not in " ".join(result.evidence)


def test_flow_kinetics_detects_both_sign_turns():
    turn_out = analyze_flow_kinetics(
        [{"time": "10:00", "value": 4.0}],
        current_value=-1.0,
        change_pct=1.5,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    turn_in = analyze_flow_kinetics(
        [{"time": "10:00", "value": -4.0}],
        current_value=1.0,
        change_pct=-1.5,
        as_of=datetime(2026, 7, 15, 10, 5),
    )

    assert turn_out.turning == "TURN_TO_OUTFLOW"
    assert turn_out.signal == "价格上涨但订单流方向转弱，形成订单流与价格背离，警惕诱多"
    assert turn_in.turning == "TURN_TO_INFLOW"
    assert "反抽观察" in (turn_in.signal or "")


def test_flow_kinetics_does_not_dilute_afternoon_speed_with_lunch_break():
    result = analyze_flow_kinetics(
        [
            {"time": "11:30", "value": -10.0},
            {"time": "13:00", "value": -9.0},
        ],
        current_value=-7.0,
        as_of=datetime(2026, 7, 15, 13, 2),
    )

    assert result.reliable is True
    assert result.window_minutes == 2
    assert result.speed == 1.0
    assert result.acceleration is not None


def test_flow_kinetics_refuses_single_or_cross_day_snapshot():
    result = analyze_flow_kinetics(
        [
            {"time": "2026-07-14 14:55:00", "value": 50.0},
            {"time": "10:10", "value": 60.0},  # future at as_of
        ],
        current_value=2.0,
        as_of=datetime(2026, 7, 15, 10, 0),
    )

    assert result.reliable is False
    assert result.speed is None
    assert "一个带时点" in result.evidence[0]


def test_price_volume_flow_alerts_cover_lure_falling_knife_and_panic_guard():
    worsening = analyze_flow_kinetics(
        [{"time": "10:00", "value": 10.0}],
        current_value=-4.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    lure = classify_price_volume_flow_alerts(
        change_pct=2.1,
        volume_ratio=0.65,
        price_vs_vwap_pct=0.5,
        vwap_reliable=True,
        flow=worsening,
    )
    falling_knife = classify_price_volume_flow_alerts(
        change_pct=-4.2,
        volume_ratio=1.6,
        price_vs_vwap_pct=-2.0,
        vwap_reliable=True,
        flow=worsening,
    )

    assert any(item.event_type == "SHRINKING_RISE_DIVERGENCE" and "诱多" in item.title for item in lure)
    assert any(item.event_type == "VOLUME_DOWN_FLOW_ACCELERATION" and "飞刀" in item.title for item in falling_knife)

    improving = analyze_flow_kinetics(
        [{"time": "10:00", "value": -8.0}],
        current_value=1.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    panic = classify_price_volume_flow_alerts(
        change_pct=-6.0,
        volume_ratio=1.4,
        price_vs_vwap_pct=-3.0,
        vwap_reliable=True,
        flow=improving,
        near_intraday_low=True,
        hard_stop_triggered=False,
        low_rebound_pct=1.8,
    )
    assert any(item.event_type == "LOW_PANIC_SELL_GUARD" and "禁止" in item.action for item in panic)


def test_hard_stop_disables_low_panic_sell_guard():
    improving = analyze_flow_kinetics(
        [{"time": "10:00", "value": -8.0}],
        current_value=1.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    alerts = classify_price_volume_flow_alerts(
        change_pct=-6.0,
        volume_ratio=1.4,
        price_vs_vwap_pct=-3.0,
        vwap_reliable=True,
        flow=improving,
        near_intraday_low=True,
        hard_stop_triggered=True,
        low_rebound_pct=2.0,
    )

    assert all(item.event_type != "LOW_PANIC_SELL_GUARD" for item in alerts)


def test_shrinking_rebound_is_not_called_a_reversal_below_vwap():
    improving = analyze_flow_kinetics(
        [{"time": "10:00", "value": -8.0}],
        current_value=-3.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    alerts = classify_price_volume_flow_alerts(
        change_pct=-2.0,
        volume_ratio=0.65,
        price_vs_vwap_pct=-0.8,
        vwap_reliable=True,
        flow=improving,
        low_rebound_pct=2.1,
    )

    assert any(item.event_type == "SHRINKING_REBOUND_UNCONFIRMED" for item in alerts)
    assert any("不追反弹" in item.action for item in alerts)


def test_shrinking_decline_distinguishes_exhaustion_from_persistent_outflow():
    improving = analyze_flow_kinetics(
        [{"time": "10:00", "value": -8.0}],
        current_value=-3.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    worsening = analyze_flow_kinetics(
        [{"time": "10:00", "value": 4.0}],
        current_value=-3.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )

    exhaustion = classify_price_volume_flow_alerts(
        change_pct=-2.2,
        volume_ratio=0.7,
        price_vs_vwap_pct=-1.0,
        vwap_reliable=True,
        flow=improving,
    )
    weakness = classify_price_volume_flow_alerts(
        change_pct=-2.2,
        volume_ratio=0.7,
        price_vs_vwap_pct=-1.0,
        vwap_reliable=True,
        flow=worsening,
    )

    assert any(item.event_type == "SHRINKING_DECLINE_EXHAUSTION_WATCH" for item in exhaustion)
    assert any(item.event_type == "SHRINKING_DECLINE_WEAKNESS" for item in weakness)


def test_volume_rebound_requires_vwap_and_improving_flow():
    improving = analyze_flow_kinetics(
        [{"time": "10:00", "value": -6.0}],
        current_value=2.0,
        as_of=datetime(2026, 7, 15, 10, 5),
    )
    alerts = classify_price_volume_flow_alerts(
        change_pct=0.5,
        volume_ratio=1.4,
        price_vs_vwap_pct=0.6,
        vwap_reliable=True,
        flow=improving,
        low_rebound_pct=3.0,
    )

    assert any(item.event_type == "VOLUME_REBOUND_CONFIRMED" for item in alerts)
    assert any("停止沿用低点卖出结论" in item.action for item in alerts)


def test_volume_shape_refuses_a_conclusion_when_vwap_or_ratio_is_missing():
    result = classify_volume_price_pattern(
        change_pct=2.0,
        volume_ratio=0,
        price_vs_vwap_pct=None,
        vwap_reliable=False,
    )

    assert result.state == "INSUFFICIENT_DATA"
    assert result.decisive is False
    assert result.risk_level == "未知"
    assert "不能判断诱多或延续" in result.counter_evidence[0]


def test_shrinking_rise_distinguishes_light_supply_from_multi_evidence_fragility():
    supported = classify_volume_price_pattern(
        change_pct=2.2,
        volume_ratio=0.68,
        price_vs_vwap_pct=1.1,
        vwap_reliable=True,
        high_drawdown_pct=0.4,
        near_recent_high=False,
        follow_through=True,
        active_buy_amount=6.5,
        active_sell_amount=2.5,
        active_flow_reliable=True,
    )
    fragile = classify_volume_price_pattern(
        change_pct=2.2,
        volume_ratio=0.68,
        price_vs_vwap_pct=-0.3,
        vwap_reliable=True,
        high_drawdown_pct=2.4,
        near_recent_high=True,
        follow_through=False,
        active_buy_amount=2.0,
        active_sell_amount=8.0,
        active_flow_reliable=True,
        sector_resonance=False,
    )

    assert supported.state == "SHRINKING_RISE_SUPPORTED"
    assert supported.label == "缩量上涨·抛压较轻"
    assert supported.risk_level == "低"
    assert "不追直线拉升" in supported.advice
    assert fragile.state == "SHRINKING_RISE_FRAGILE"
    assert fragile.label == "缩量上涨脆弱·疑似诱多"
    assert fragile.risk_level == "高"
    assert len(fragile.evidence) >= 4
    assert fragile.invalidation and fragile.recovery_conditions


def test_estimated_minute_direction_cannot_confirm_a_shrinking_rise():
    result = classify_volume_price_pattern(
        change_pct=2.0,
        volume_ratio=0.7,
        price_vs_vwap_pct=1.0,
        vwap_reliable=True,
        high_drawdown_pct=0.3,
        near_recent_high=False,
        follow_through=True,
        active_buy_amount=9.0,
        active_sell_amount=1.0,
        active_flow_reliable=False,
    )

    assert result.state == "SHRINKING_RISE_PENDING"
    assert result.decisive is False
    assert "订单流" in result.counter_evidence[0]


def test_volume_rise_distinguishes_confirmation_from_high_level_absorption_decay():
    confirmed = classify_volume_price_pattern(
        change_pct=3.1,
        volume_ratio=1.55,
        price_vs_vwap_pct=1.4,
        vwap_reliable=True,
        high_drawdown_pct=0.3,
        near_recent_high=False,
        follow_through=True,
        active_buy_amount=9.0,
        active_sell_amount=3.0,
        active_flow_reliable=True,
        sector_resonance=True,
    )
    stalled = classify_volume_price_pattern(
        change_pct=1.4,
        volume_ratio=1.8,
        price_vs_vwap_pct=-0.4,
        vwap_reliable=True,
        high_drawdown_pct=3.2,
        near_recent_high=True,
        follow_through=False,
        active_buy_amount=3.0,
        active_sell_amount=9.0,
        active_flow_reliable=True,
        sector_resonance=False,
    )

    assert confirmed.state == "VOLUME_RISE_CONFIRMED"
    assert confirmed.label == "放量上涨确认"
    assert "不能推导为后续必涨" in confirmed.counter_evidence[0]
    assert stalled.state == "VOLUME_RISE_STALLED"
    assert stalled.label == "放量滞涨·高位承载衰减"
    assert stalled.risk_level == "高"
    assert "禁止追高" in stalled.advice


def test_shrinking_pullback_requires_vwap_hold_and_no_adverse_flow():
    held = classify_volume_price_pattern(
        change_pct=1.0,
        volume_ratio=0.7,
        price_vs_vwap_pct=0.6,
        vwap_reliable=True,
        high_drawdown_pct=1.2,
        near_recent_high=False,
        follow_through=True,
        active_buy_amount=5.4,
        active_sell_amount=4.6,
        active_flow_reliable=True,
    )

    assert held.state == "SHRINKING_PULLBACK_HOLD"
    assert held.label == "缩量回踩不破VWAP"
    assert "不在回踩中恐慌卖出" in held.advice
