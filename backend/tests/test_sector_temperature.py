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
