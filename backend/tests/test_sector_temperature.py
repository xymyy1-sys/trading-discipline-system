from datetime import datetime, timedelta, timezone

import pytest

from app.services import sector_temperature as sector_temperature_service
from app.services.sector_temperature import build_sector_temperature


def _row(
    name: str,
    change: float,
    net: float,
    *,
    speed: float | None = None,
    acceleration: float | None = None,
    turning: str | None = None,
    limit_up_count: int = 0,
):
    return {
        "name": name,
        "board_code": f"BK-{name}",
        "change_pct": change,
        "net_inflow": net,
        "flow_speed": speed,
        "flow_acceleration": acceleration,
        "flow_turning": turning,
        "limit_up_count": limit_up_count,
    }


def _item(result, name: str):
    return next(item for item in result["items"] if item["name"] == name)


def _confirmed(state: str) -> dict:
    return {
        "strict_state": state,
        "sample_confirmation_count": 2,
        "trading_day_confirmation_count": 1,
        "persistence_confirmed": True,
        "confirmation_basis": ["连续2个有效采样点"],
        "data_as_of": "2026-07-17T10:29:00+08:00",
        "recent_samples": [{"strict_state": state}, {"strict_state": state}],
    }


def test_overheated_healthy_trend_is_distinct_from_turning_down():
    five = [_row("健康热门", 14, 150), _row("兑现热门", 14, 150)]
    ten = [_row("健康热门", 24, 260), _row("兑现热门", 24, 260)]
    current = [
        _row("健康热门", 3.6, 55, speed=1.2, acceleration=0.12, turning="INFLOW_ACCELERATING", limit_up_count=8),
        _row("兑现热门", -2.1, -45, speed=-1.4, acceleration=-0.18, turning="OUTFLOW_TURN", limit_up_count=3),
    ]

    result = build_sector_temperature(current, five, ten)
    healthy = _item(result, "健康热门")
    reversal = _item(result, "兑现热门")

    assert healthy["status"] == "偏热趋势健康"
    assert reversal["status"] == "过热兑现风险"
    assert reversal["risk_level"] == "HIGH"
    assert any("不构成" in action or "不因" in action for action in healthy["actions"])


def test_oversold_falling_is_distinct_from_stabilizing():
    five = [_row("继续杀跌", -9, -120), _row("开始企稳", -9, -120)]
    ten = [_row("继续杀跌", -14, -210), _row("开始企稳", -14, -210)]
    current = [
        _row("继续杀跌", -2.8, -38, speed=-1.0, acceleration=-0.12, turning="OUTFLOW_ACCELERATING"),
        _row("开始企稳", 0.8, 12, speed=0.6, acceleration=0.08, turning="INFLOW_TURN"),
    ]

    result = build_sector_temperature(current, five, ten)
    falling = _item(result, "继续杀跌")
    stabilizing = _item(result, "开始企稳")

    assert falling["status"] == "过冷仍下跌"
    assert "禁止接飞刀" in falling["actions"][0]
    assert stabilizing["status"] in {"过冷企稳观察", "修复初步确认"}
    assert any("不抢" in action or "观察修复" in action for action in stabilizing["actions"])


def test_margin_crowding_alone_never_becomes_a_sell_signal():
    current = [_row("融资拥挤但中性", 0.1, 1, speed=0.0, acceleration=0.0)]
    five = [_row("融资拥挤但中性", 0.4, 2)]
    ten = [_row("融资拥挤但中性", 0.8, 4)]
    margin = {
        "融资拥挤但中性": {
            "as_of": "2026-07-16",
            "financing_balance": 9999,
            "financing_balance_ratio": 30,
            "financing_net_buy": 80,
            "net_buy_5d": 300,
            "net_buy_10d": 500,
            "realtime": True,
        }
    }

    item = _item(build_sector_temperature(current, five, ten, margin_by_name=margin), "融资拥挤但中性")

    assert item["margin_score"] is not None and item["margin_score"] > 90
    assert item["margin_realtime"] is False
    assert item["status"] not in {"过热分歧", "过热兑现风险"}
    assert not any("卖出" in action or "清仓" in action for action in item["actions"])
    assert any("不能单独触发" in text for text in item["counter_evidence"])


def test_high_distribution_requires_price_cash_flow_joint_confirmation(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=tz),
    )
    current_row = _row("高位承载转弱", -2.0, -40, speed=-1.2, acceleration=-0.2, turning="INFLOW_FADING")
    current_row.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:29:00+08:00",
    })
    current = [current_row]
    five = [_row("高位承载转弱", 12.0, 150)]
    ten = [_row("高位承载转弱", 22.0, 260)]
    margin = {
        "高位承载转弱": {
            "as_of": "2026-07-16",
            "financing_balance_ratio": 9.0,
            "financing_net_buy": 10,
            "net_buy_5d": 40,
            "net_buy_10d": 80,
            "net_buy_20d": 120,
        }
    }

    item = _item(build_sector_temperature(
        current,
        five,
        ten,
        margin_by_name=margin,
        persistence_by_name={"高位承载转弱": _confirmed("高位派发风险")},
    ), "高位承载转弱")

    assert item["distribution_state"] == "高位派发风险"
    assert item["distribution_risk_level"] == "HIGH"
    assert item["distribution_risk_score"] >= 80
    assert item["order_flow_exhausted"] is True
    assert item["price_response_weak"] is True
    assert item["distribution_confirmation_count"] >= 3
    assert any("阶段高位" in text for text in item["distribution_evidence"])


def test_positive_order_flow_without_price_response_is_carrying_decay():
    current = [_row("有流入无价格", 0.1, 30, speed=0.5, acceleration=0.1, turning="FLOW_IMPROVING")]
    five = [_row("有流入无价格", 0.5, 80)]
    ten = [_row("有流入无价格", 1.5, 100)]

    item = _item(build_sector_temperature(current, five, ten), "有流入无价格")

    assert item["distribution_state"] == "资金承载衰减"
    assert item["distribution_risk_level"] == "MEDIUM"
    assert item["price_response_weak"] is True
    assert item["order_flow_exhausted"] is False
    assert any("有效价格推进" in action for action in item["distribution_actions"])


def test_sustained_negative_price_and_order_flow_is_not_called_healthy_increment():
    current = [_row("持续负反馈", -1.2, -18)]
    five = [_row("持续负反馈", -3.5, -70)]
    ten = [_row("持续负反馈", -6.0, -120)]

    item = _item(build_sector_temperature(current, five, ten), "持续负反馈")

    assert item["distribution_state"] == "资金承载衰减"
    assert item["distribution_risk_level"] == "MEDIUM"
    assert item["strict_state"] != "健康增量"


def test_leverage_crowding_is_observation_only_and_capped_below_high_risk():
    current = [_row("杠杆单项", 2.0, 10, speed=0.2, acceleration=0.1, turning="FLOW_IMPROVING")]
    five = [_row("杠杆单项", 4.0, 30)]
    ten = [_row("杠杆单项", 7.0, 60)]
    margin = {
        "杠杆单项": {
            "as_of": "2026-07-16",
            "financing_balance_ratio": 12,
            "financing_net_buy": 30,
            "net_buy_5d": 80,
            "net_buy_10d": 140,
            "net_buy_20d": 220,
        }
    }

    item = _item(build_sector_temperature(current, five, ten, margin_by_name=margin), "杠杆单项")

    assert item["leverage_crowding"] is True
    assert item["distribution_state"] == "杠杆追涨观察"
    assert item["distribution_risk_level"] == "MEDIUM"
    assert item["distribution_risk_score"] <= 45
    assert item["distribution_confirmation_count"] == 1
    assert not any("卖出" in action or "清仓" in action for action in item["distribution_actions"])


def test_deleveraging_stampede_needs_negative_price_and_cash_order_flow(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=tz),
    )
    current_row = _row("联合踩踏", -2.0, -40, speed=-1.0, acceleration=-0.1, turning="OUTFLOW_ACCELERATING")
    current_row.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:29:00+08:00",
    })
    current = [current_row]
    five = [_row("联合踩踏", -4.0, -100)]
    ten = [_row("联合踩踏", -8.0, -180)]
    margin = {
        "联合踩踏": {
            "as_of": "2026-07-16",
            "financing_balance_ratio": 4,
            "financing_net_buy": -20,
            "net_buy_5d": -60,
            "net_buy_10d": -100,
            "net_buy_20d": -160,
        }
    }

    item = _item(build_sector_temperature(
        current,
        five,
        ten,
        margin_by_name=margin,
        persistence_by_name={"联合踩踏": _confirmed("去杠杆踩踏")},
    ), "联合踩踏")

    assert item["distribution_state"] == "去杠杆踩踏"
    assert item["distribution_risk_level"] == "HIGH"
    assert item["distribution_risk_score"] >= 80
    assert item["distribution_confirmation_count"] == 3


def test_negative_financing_alone_does_not_create_stampede_or_trade_action():
    current = [_row("融资减但价格健康", 1.5, 20, speed=0.5, acceleration=0.1, turning="INFLOW_ACCELERATING")]
    five = [_row("融资减但价格健康", 3.0, 60)]
    ten = [_row("融资减但价格健康", 5.0, 100)]
    margin = {
        "融资减但价格健康": {
            "as_of": "2026-07-16",
            "financing_balance_ratio": 3,
            "financing_net_buy": -10,
            "net_buy_5d": -30,
            "net_buy_10d": -50,
            "net_buy_20d": -80,
        }
    }

    item = _item(build_sector_temperature(current, five, ten, margin_by_name=margin), "融资减但价格健康")

    assert item["distribution_state"] == "健康增量"
    assert item["distribution_risk_level"] == "LOW"
    assert item["distribution_risk_score"] < 25
    assert not any("卖出" in action or "清仓" in action for action in item["distribution_actions"])


def test_distribution_assessment_degrades_when_cross_windows_are_missing():
    item = _item(build_sector_temperature([_row("单窗口", -3.0, -50)], [], []), "单窗口")

    assert item["distribution_state"] == "数据不足"
    assert item["distribution_risk_level"] == "UNKNOWN"
    assert item["distribution_confirmation_count"] == 0
    assert any("窗口少于2个" in text for text in item["distribution_counter_evidence"])


def test_rounding_dust_flow_cannot_create_exhaustion_or_weak_price_response():
    current = [_row("极小订单流", -0.01, 0.0)]
    five = [_row("极小订单流", 8.0, 0.01)]
    ten = [_row("极小订单流", 10.0, 0.02)]

    item = _item(build_sector_temperature(current, five, ten), "极小订单流")

    assert item["order_flow_exhausted"] is False
    assert item["price_response_weak"] is False
    assert item["distribution_risk_level"] != "HIGH"
    assert item["distribution_confirmation_count"] == 0
    assert item["distribution_state"] == "数据不足"


def test_neutral_values_are_not_mislabelled_as_healthy_increment():
    current = [_row("中性零值", 0.0, 0.0)]
    five = [_row("中性零值", 0.0, 0.0)]
    ten = [_row("中性零值", 0.0, 0.0)]

    item = _item(build_sector_temperature(current, five, ten), "中性零值")

    assert item["distribution_state"] == "数据不足"
    assert item["instantaneous_distribution_state"] == "数据不足"
    assert item["distribution_risk_level"] == "UNKNOWN"
    assert any("不把中性或零值行情误标" in text for text in item["distribution_counter_evidence"])


def test_partial_snapshot_caps_strong_distribution_candidate_at_watch_level():
    current = [_row("时间缺失的强信号", -2.0, -40, speed=-1.2, acceleration=-0.2, turning="INFLOW_FADING")]
    five = [_row("时间缺失的强信号", 12.0, 150)]
    ten = [_row("时间缺失的强信号", 22.0, 260)]

    item = _item(build_sector_temperature(current, five, ten), "时间缺失的强信号")

    assert item["data_quality"] == "partial"
    assert item["distribution_state"] == "资金承载衰减"
    assert item["distribution_risk_level"] == "MEDIUM"
    assert item["distribution_risk_score"] <= 74
    assert any("只允许观察级结论" in text for text in item["distribution_counter_evidence"])


def test_five_day_rebound_from_weak_ten_day_base_is_not_high_position():
    current = [_row("超跌反弹不是高位", -1.2, -20, speed=-0.5, acceleration=-0.1, turning="INFLOW_FADING")]
    five = [_row("超跌反弹不是高位", 8.5, 60)]
    ten = [_row("超跌反弹不是高位", -12.0, -100)]

    item = _item(build_sector_temperature(current, five, ten), "超跌反弹不是高位")

    assert item["distribution_state"] != "高位派发风险"
    assert item["distribution_risk_level"] != "HIGH"


def test_relative_flow_significance_can_confirm_a_smaller_board(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=tz),
    )
    current_row = _row("小板块相对显著", -1.0, -0.5, speed=-0.06, acceleration=-0.01, turning="INFLOW_FADING")
    current_row.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:29:00+08:00",
    })

    item = _item(build_sector_temperature(
        [current_row],
        [_row("小板块相对显著", 9.0, 6.0)],
        [_row("小板块相对显著", 10.0, 12.0)],
        persistence_by_name={"小板块相对显著": _confirmed("高位派发风险")},
    ), "小板块相对显著")

    assert item["order_flow_exhausted"] is True
    assert item["price_response_weak"] is True
    assert item["distribution_state"] == "高位派发风险"
    assert item["distribution_risk_level"] == "HIGH"


def test_missing_attention_is_unknown_instead_of_zero():
    current = [_row("无人气数据", 1.0, 10)]
    five = [_row("无人气数据", 2.0, 20)]
    ten = [_row("无人气数据", 3.0, 30)]

    item = _item(build_sector_temperature(current, five, ten), "无人气数据")

    assert item["attention_score"] is None
    assert any("未按0分" in text for text in item["counter_evidence"])


def test_limited_windows_degrade_honestly():
    result = build_sector_temperature([_row("只有当日", 5, 50)], [], [])
    item = result["items"][0]

    assert item["status"] == "数据不足"
    assert item["data_quality"] == "limited"
    assert any("数据不足" in action for action in item["actions"])


def test_missing_current_window_stays_null_instead_of_fake_zero():
    result = build_sector_temperature([], [_row("仅历史窗口", 6, 30)], [_row("仅历史窗口", 10, 70)])
    item = result["items"][0]

    assert item["change_pct"] is None
    assert item["net_inflow"] is None


def test_previous_trade_date_snapshot_is_marked_stale_not_realtime():
    current = _row("旧快照", 1.0, 10, speed=0.5, acceleration=0.1, turning="INFLOW_ACCELERATING")
    current["provider_trade_date"] = "2026-07-16"
    result = build_sector_temperature(
        [current],
        [_row("旧快照", 3.0, 20)],
        [_row("旧快照", 5.0, 30)],
    )
    item = result["items"][0]

    assert item["data_quality"] == "stale"
    assert item["provider_trade_date"] == "2026-07-16"
    assert any("不能作为今日盘中实时拐点" in text for text in item["counter_evidence"])


@pytest.mark.parametrize(
    ("turning", "direction"),
    [
        ("INFLOW_FADING", "down"),
        ("FLOW_WEAKENING", "down"),
        ("OUTFLOW_NARROWING", "up"),
        ("FLOW_IMPROVING", "up"),
    ],
)
def test_marginal_flow_turning_direction_is_not_inferred_by_substring(turning, direction):
    assert sector_temperature_service._turning_direction(turning) == direction


def test_same_day_old_provider_timestamp_cannot_remain_high_quality(monkeypatch):
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 17, 10, 30, tzinfo=tz)
    monkeypatch.setattr(sector_temperature_service, "_shanghai_now", lambda: now)
    current = _row("分钟级旧快照", 1.0, 10, speed=0.5, acceleration=0.1, turning="FLOW_IMPROVING")
    current.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:10:00+08:00",
    })

    item = _item(build_sector_temperature(
        [current],
        [_row("分钟级旧快照", 3.0, 20)],
        [_row("分钟级旧快照", 5.0, 30)],
    ), "分钟级旧快照")

    assert item["data_quality"] == "stale"
    assert any("滞后 20 分钟" in text for text in item["counter_evidence"])


def test_cached_current_snapshot_is_capped_below_high_quality(monkeypatch):
    tz = timezone(timedelta(hours=8))
    now = datetime(2026, 7, 17, 10, 30, tzinfo=tz)
    monkeypatch.setattr(sector_temperature_service, "_shanghai_now", lambda: now)
    current = _row("缓存快照", 1.0, 10, speed=0.5, acceleration=0.1, turning="FLOW_IMPROVING")
    current.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:29:00+08:00",
        "_cache_used": True,
        "_cache_source": "eastmoney",
        "_cache_trade_date": "2026-07-17",
    })

    item = _item(build_sector_temperature(
        [current],
        [_row("缓存快照", 3.0, 20)],
        [_row("缓存快照", 5.0, 30)],
    ), "缓存快照")

    assert item["data_quality"] == "partial"
    assert any("来自 eastmoney 缓存" in text for text in item["counter_evidence"])


def test_high_risk_instantaneous_state_waits_for_distinct_persistence(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=tz),
    )
    current = _row(
        "待持续确认",
        -2.0,
        -40,
        speed=-1.2,
        acceleration=-0.2,
        turning="INFLOW_FADING",
    )
    current.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:29:00+08:00",
    })

    item = _item(build_sector_temperature(
        [current],
        [_row("待持续确认", 12.0, 150)],
        [_row("待持续确认", 22.0, 260)],
    ), "待持续确认")

    assert item["instantaneous_distribution_state"] == "高位派发风险"
    assert item["distribution_state"] == "资金承载衰减"
    assert item["strict_state"] == "资金承载衰减"
    assert item["persistence_confirmed"] is False
    assert any("连续" in text and "确认" in text for text in item["distribution_counter_evidence"])


def test_current_unpersisted_provider_timestamp_cannot_confirm_persistence(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 31, tzinfo=tz),
    )
    current = _row(
        "实时补计",
        -2.0,
        -40,
        speed=-1.2,
        acceleration=-0.2,
        turning="INFLOW_FADING",
    )
    current.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:30:00+08:00",
    })
    prior = {
        "strict_state": "高位派发风险",
        "sample_confirmation_count": 1,
        "trading_day_confirmation_count": 1,
        "last_sample_at": "2026-07-17T10:25:00+08:00",
        "last_trade_date": "2026-07-17",
        "recent_samples": [{"strict_state": "高位派发风险"}],
    }

    item = _item(build_sector_temperature(
        [current],
        [_row("实时补计", 12.0, 150)],
        [_row("实时补计", 22.0, 260)],
        persistence_by_name={"实时补计": prior},
    ), "实时补计")

    assert item["distribution_state"] == "资金承载衰减"
    assert item["sample_confirmation_count"] == 1
    assert item["persistence_confirmed"] is False
    assert item["confirmed_state"] == ""
    assert len(item["recent_state_samples"]) == 1

    same_timestamp = {**prior, "last_sample_at": "2026-07-17T10:30:00+08:00"}
    duplicate = _item(build_sector_temperature(
        [current],
        [_row("实时补计", 12.0, 150)],
        [_row("实时补计", 22.0, 260)],
        persistence_by_name={"实时补计": same_timestamp},
    ), "实时补计")
    assert duplicate["sample_confirmation_count"] == 1
    assert duplicate["persistence_confirmed"] is False
    assert duplicate["distribution_state"] == "资金承载衰减"

    older_timestamp = {**prior, "last_sample_at": "2026-07-17T10:31:00+08:00"}
    out_of_order = _item(build_sector_temperature(
        [current],
        [_row("实时补计", 12.0, 150)],
        [_row("实时补计", 22.0, 260)],
        persistence_by_name={"实时补计": older_timestamp},
    ), "实时补计")
    assert out_of_order["sample_confirmation_count"] == 1
    assert out_of_order["persistence_confirmed"] is False


def test_financing_buy_turnover_ratio_requires_same_trade_date(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=tz),
    )
    current = _row("融资成交占比", 1.0, 10, speed=0.2, acceleration=0.1)
    current.update({
        "provider_trade_date": "2026-07-17",
        "provider_updated_at": "2026-07-17T10:29:00+08:00",
        "turnover_amount": 100.0,
        "turnover_complete": True,
    })
    margin = {
        "融资成交占比": {
            "as_of": "2026-07-17",
            "financing_buy": 12.0,
            "financing_balance_ratio": 3.0,
        }
    }
    aligned = _item(build_sector_temperature(
        [current],
        [_row("融资成交占比", 2.0, 20)],
        [_row("融资成交占比", 3.0, 30)],
        margin_by_name=margin,
    ), "融资成交占比")
    assert aligned["financing_buy_turnover_ratio"] == 12.0
    assert aligned["financing_turnover_date_aligned"] is True

    margin["融资成交占比"]["as_of"] = "2026-07-16"
    mismatched = _item(build_sector_temperature(
        [current],
        [_row("融资成交占比", 2.0, 20)],
        [_row("融资成交占比", 3.0, 30)],
        margin_by_name=margin,
    ), "融资成交占比")
    assert mismatched["financing_buy_turnover_ratio"] is None
    assert mismatched["financing_turnover_date_aligned"] is False

    margin["融资成交占比"].update({
        "financing_buy": 10.0,
        "financing_reference_turnover": 50.0,
        "financing_turnover_as_of": "2026-07-16",
    })
    historical_aligned = _item(build_sector_temperature(
        [current],
        [_row("融资成交占比", 2.0, 20)],
        [_row("融资成交占比", 3.0, 30)],
        margin_by_name=margin,
    ), "融资成交占比")
    assert historical_aligned["financing_buy_turnover_ratio"] == 20.0
    assert historical_aligned["financing_turnover_date_aligned"] is True
    assert historical_aligned["financing_turnover_as_of"] == "2026-07-16"


def test_continuous_carrying_efficiency_requires_immutable_history():
    current = _row("连续承载", -2.0, 30, speed=0.2, acceleration=0.1)
    current["flow_ratio"] = 3.0
    five = _row("连续承载", -1.0, 80)
    five["flow_ratio"] = 4.0
    ten = _row("连续承载", -2.0, 100)
    ten["flow_ratio"] = 5.0

    without_history = _item(
        build_sector_temperature([current], [five], [ten]),
        "连续承载",
    )
    assert without_history["capital_price_carrying_efficiency"] is None
    assert without_history["capital_price_carrying_sample_count"] == 0

    item = _item(build_sector_temperature(
        [current],
        [five],
        [ten],
        persistence_by_name={
            "连续承载": {
                "capital_price_carrying_efficiency": 28.0,
                "capital_price_carrying_sample_count": 4,
                "capital_price_carrying_span_minutes": 20.0,
                "capital_price_carrying_slope": -3.0,
            },
        },
    ), "连续承载")

    assert item["capital_price_carrying_efficiency"] == 28.0
    assert item["capital_price_carrying_efficiency"] < 40
    assert item["capital_price_carrying_sample_count"] == 4
    assert item["capital_price_carrying_span_minutes"] == 20.0
    assert item["capital_price_carrying_slope"] == -3.0
    assert item["new_high_count"] is None
    assert item["promotion_rate"] is None
    assert item["break_rate"] is None
    assert item["sector_below_vwap"] is None


def test_stale_snapshot_preserves_valid_archived_financing_turnover_ratio(monkeypatch):
    tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        sector_temperature_service,
        "_shanghai_now",
        lambda: datetime(2026, 7, 17, 10, 30, tzinfo=tz),
    )
    current = _row("历史描述比率", 1.0, 10)
    current.update({
        "provider_trade_date": "2026-07-16",
        "provider_updated_at": "2026-07-16T15:05:00+08:00",
    })
    margin = {
        "历史描述比率": {
            "as_of": "2026-07-16",
            "financing_buy": 10.0,
            "financing_reference_turnover": 50.0,
            "financing_turnover_as_of": "2026-07-16",
        }
    }

    item = _item(build_sector_temperature(
        [current],
        [_row("历史描述比率", 2.0, 20)],
        [_row("历史描述比率", 3.0, 30)],
        margin_by_name=margin,
    ), "历史描述比率")

    assert item["data_quality"] == "stale"
    assert item["distribution_state"] == "数据不足"
    assert item["financing_buy_turnover_ratio"] == 20.0
    assert item["financing_turnover_date_aligned"] is True


def test_margin_history_features_are_exposed_without_fake_non_leveraged_flow():
    current = _row("历史融资", 1.0, 15)
    current.update({
        "non_leveraged_net_inflow": 999.0,
        "non_leveraged_flow_audited": False,
    })
    margin = {
        "历史融资": {
            "as_of": "2026-07-16",
            "financing_net_buy_slope_5d": 1.2,
            "financing_net_buy_slope_10d": 0.8,
            "financing_net_buy_slope_20d": 0.4,
            "financing_balance_ratio_percentile_60d": 92.0,
            "financing_balance_ratio_percentile_120d": 88.0,
            "margin_history_sample_count": 121,
            "margin_history_method": "逐日真实序列",
        }
    }

    item = _item(build_sector_temperature(
        [current],
        [_row("历史融资", 2.0, 20)],
        [_row("历史融资", 3.0, 30)],
        margin_by_name=margin,
    ), "历史融资")

    assert item["financing_net_buy_slope_5d"] == 1.2
    assert item["financing_balance_ratio_percentile_120d"] == 88.0
    assert item["margin_history_sample_count"] == 121
    assert item["non_leveraged_net_inflow"] is None
    assert item["non_leveraged_flow_audited"] is False
    assert any("未把主力资金算法冒充" in text for text in item["distribution_counter_evidence"])


def test_strict_business_state_is_one_of_exact_six_states():
    result = build_sector_temperature(
        [_row("六态约束", 1.0, 12)],
        [_row("六态约束", 2.0, 20)],
        [_row("六态约束", 3.0, 30)],
    )
    item = _item(result, "六态约束")
    assert item["strict_state"] in {
        "健康增量",
        "杠杆追涨观察",
        "资金承载衰减",
        "高位派发风险",
        "去杠杆踩踏",
        "超跌企稳观察",
    }
