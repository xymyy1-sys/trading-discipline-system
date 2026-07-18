from datetime import datetime

from app.services.flow_kinetics import (
    analyze_flow_kinetics,
    classify_price_volume_flow_alerts,
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
