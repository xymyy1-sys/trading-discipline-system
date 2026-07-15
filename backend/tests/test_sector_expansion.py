from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.services.sector_expansion import (
    STATUS_CONFIRMED,
    STATUS_WATCH,
    SectorExpansionRadarService,
)
from app.schemas.trading import SectorExpansionRadarOut


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 15, 13, 3, tzinfo=TZ)


def _stock(
    code: str,
    name: str,
    first_limit_time: str,
    *,
    industry: str = "半导体",
    concepts: list[str] | None = None,
    level: int = 1,
    break_count: int = 0,
) -> dict:
    return {
        "code": code,
        "name": name,
        "first_limit_time": first_limit_time,
        "industry": industry,
        "concepts": concepts or [industry],
        "consecutive_limit_days": level,
        "break_count": break_count,
    }


def _ladder(
    stocks: list[dict],
    *,
    trade_date: str = "2026-07-15",
    updated_at=None,
    source: str = "eastmoney-limit-up-pool",
) -> dict:
    return {
        "source": source,
        "trade_date": trade_date,
        "updated_at": updated_at or NOW - timedelta(seconds=10),
        "groups": [{"level": 1, "stocks": stocks}],
    }


def _flow(**overrides) -> dict:
    values = {
        "name": "半导体行业",
        "display_name": "半导体",
        "provider": "eastmoney",
        "change_pct": 2.6,
        "net_inflow": 18.5,
        "flow_speed": 2.4,
        "flow_acceleration": 0.35,
        "flow_turning": "TURN_TO_INFLOW",
        "flow_signal": "资金由净流出拐为净流入",
        "flow_as_of": "2026-07-15 13:03:00",
        "flow_kinetics_reliable": True,
        "sector_vwap_reliable": True,
        "sector_below_vwap": False,
        "index_timeline": [
            {"time": "11:29", "price": 100.0, "vwap": 99.5},
            {"time": "13:02", "price": 101.0, "vwap": 100.1},
        ],
    }
    values.update(overrides)
    return values


def test_confirms_multi_stock_burst_with_flow_turn_and_price_strength_across_lunch():
    service = SectorExpansionRadarService(window_minutes=15)
    ladder = _ladder([
        _stock("600001", "芯片一号", "11:27:00", level=2),
        _stock("600002", "芯片二号", "13:01:00"),
        _stock("600003", "未来封板", "13:05:00"),  # future observation is excluded
    ])

    result = service.assess(ladder, {"inflow": [_flow()], "outflow": []}, as_of=NOW)

    item = result["items"][0]
    assert item["sector"] == "半导体"
    assert item["status"] == STATUS_CONFIRMED
    assert item["new_limit_up_count"] == 2
    assert item["highest_board"] == 2
    assert item["buy_signal"] is False
    assert "禁止追后排" in item["action"]
    assert any("11:27" in text and "13:01" in text for text in item["evidence"])
    assert all("13:05" not in text for text in item["evidence"])
    assert any("资金由净流出拐为净流入" in text for text in item["evidence"])
    assert result["counts"][STATUS_CONFIRMED] == 1


def test_missing_flow_never_becomes_confirmed_and_states_required_evidence():
    service = SectorExpansionRadarService()
    result = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00"),
            _stock("600002", "芯片二号", "13:02"),
        ]),
        None,
        as_of=NOW,
    )

    item = result["items"][0]
    assert item["status"] == STATUS_WATCH
    assert "同名板块真实资金流" in item["missing"]
    assert "板块涨幅" in item["missing"]
    assert "不开仓、不追高" in item["action"]
    assert item["buy_signal"] is False


def test_negative_flow_turn_blocks_confirmation_and_exposes_risk_and_invalidation():
    service = SectorExpansionRadarService()
    weak_flow = _flow(
        net_inflow=-8.0,
        flow_speed=-3.0,
        flow_acceleration=-0.6,
        flow_turning="TURN_TO_OUTFLOW",
        flow_signal="资金由净流入拐为净流出",
        sector_below_vwap=True,
    )
    result = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00", break_count=2),
            _stock("600002", "芯片二号", "13:02", break_count=2),
        ]),
        [_flow(name="别的板块", display_name="别的板块"), weak_flow],
        as_of=NOW,
    )

    item = result["items"][0]
    assert item["status"] == STATUS_WATCH
    assert any("净流出" in text for text in item["counter_evidence"])
    assert any("资金正在转弱" in text for text in item["risk"])
    assert any("炸板" in text for text in item["counter_evidence"])
    assert any("资金由流入拐为流出" in text for text in item["invalidation"])


def test_stale_or_unreliable_flow_is_not_used_as_confirmation():
    service = SectorExpansionRadarService(max_flow_age_minutes=20)
    # Lunch is excluded from the age calculation, so use a genuinely stale
    # observation measured in trading minutes rather than wall-clock minutes.
    stale = _flow(flow_as_of="2026-07-15 10:30:00")
    result = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00"),
            _stock("600002", "芯片二号", "13:02"),
        ]),
        [stale],
        as_of=NOW,
    )

    item = result["items"][0]
    assert item["status"] == STATUS_WATCH
    assert "同名板块真实资金流" in item["missing"]

    unreliable = _flow(flow_kinetics_reliable=False, flow_speed=None, flow_acceleration=None)
    result = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00"),
            _stock("600002", "芯片二号", "13:02"),
        ]),
        [unreliable],
        as_of=NOW,
    )
    item = result["items"][0]
    assert item["status"] == STATUS_WATCH
    assert any("资金快照" in text for text in item["missing"])


def test_rejects_cross_day_or_future_ladder_instead_of_leaking_data():
    service = SectorExpansionRadarService()
    stocks = [
        _stock("600001", "芯片一号", "13:00"),
        _stock("600002", "芯片二号", "13:02"),
    ]

    cross_day = service.assess(_ladder(stocks, trade_date="2026-07-14"), [_flow()], as_of=NOW)
    future = service.assess(
        _ladder(stocks, updated_at=NOW + timedelta(minutes=1)),
        [_flow()],
        as_of=NOW,
    )

    assert cross_day["items"] == []
    assert cross_day["data_quality"] == "missing"
    assert "不是当前交易日" in cross_day["notes"][-1]
    assert future["items"] == []
    assert future["data_quality"] == "degraded"
    assert "因果约束" in future["notes"][-1]


def test_one_recent_limit_up_does_not_appear_without_improving_real_flow():
    service = SectorExpansionRadarService()
    ladder = _ladder([
        _stock("600001", "早盘老封板", "10:00"),
        _stock("600002", "午后新封板", "13:02"),
    ])
    weak = _flow(
        net_inflow=-2.0,
        flow_speed=-0.5,
        flow_turning="FLOW_WEAKENING",
        flow_signal="资金边际转弱",
    )

    result = service.assess(ladder, [weak], as_of=NOW)

    assert result["items"] == []


def test_outflow_narrowing_is_visible_as_watch_but_never_claimed_as_reversal():
    service = SectorExpansionRadarService()
    ladder = _ladder([
        _stock("600001", "早盘老封板", "10:00"),
        _stock("600002", "午后新封板", "13:02"),
    ])
    narrowing = _flow(
        net_inflow=-2.0,
        flow_speed=0.5,
        flow_acceleration=0.1,
        flow_turning="OUTFLOW_NARROWING",
        flow_signal="净流出正在快速收窄",
    )

    result = service.assess(ladder, [narrowing], as_of=NOW)

    item = result["items"][0]
    assert item["status"] == STATUS_WATCH
    assert any("净流出正在快速收窄" in text for text in item["evidence"])
    assert "板块资金由净流出转为净流入" in item["missing"]
    assert any("不能确认趋势反转" in text for text in item["risk"])


def test_alias_matching_and_duplicate_group_rows_do_not_double_count():
    service = SectorExpansionRadarService()
    first = _stock("600001", "芯片一号", "13:00", industry="半导体行业", concepts=["先进封装概念"])
    second = _stock("600002", "芯片二号", "13:02", industry="半导体行业", concepts=["先进封装概念"])
    ladder = _ladder([first, second])
    # The same stock may be present in another level/group in provider-shaped
    # data; it must not inflate the burst count.
    ladder["groups"].append({"level": 2, "stocks": [first]})

    result = service.assess(ladder, [_flow(name="半导体板块", display_name=None)], as_of=NOW)

    semiconductor = next(item for item in result["items"] if item["sector"] == "半导体")
    assert semiconductor["total_limit_up_count"] == 2
    assert semiconductor["new_limit_up_count"] == 2
    assert semiconductor["status"] == STATUS_CONFIRMED


def test_price_timeline_future_point_cannot_fake_strengthening():
    service = SectorExpansionRadarService()
    flow = _flow(
        change_pct=2.0,
        index_timeline=[
            {"time": "13:00", "price": 100.0, "vwap": 99.5},
            {"time": "13:02", "price": 99.8, "vwap": 99.6},
            {"time": "13:05", "price": 120.0, "vwap": 100.0},
        ],
    )
    result = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00"),
            _stock("600002", "芯片二号", "13:02"),
        ]),
        [flow],
        as_of=NOW,
    )

    item = result["items"][0]
    assert item["status"] == STATUS_WATCH
    assert all("+20" not in text for text in item["evidence"])


def test_provider_naive_utc_timestamp_is_fresh_but_old_ladder_is_rejected():
    service = SectorExpansionRadarService(max_ladder_age_minutes=6)
    stocks = [
        _stock("600001", "芯片一号", "13:00"),
        _stock("600002", "芯片二号", "13:02"),
    ]
    # MarketDataProvider currently emits datetime.utcnow() without tzinfo.
    utc_naive = datetime(2026, 7, 15, 5, 2, 30)
    fresh = service.assess(_ladder(stocks, updated_at=utc_naive), [_flow()], as_of=NOW)
    stale = service.assess(
        _ladder(stocks, updated_at=datetime(2026, 7, 15, 10, 0, tzinfo=TZ)),
        [_flow()],
        as_of=NOW,
    )

    assert fresh["items"][0]["status"] == STATUS_CONFIRMED
    assert stale["items"] == []
    assert stale["data_quality"] == "degraded"
    assert "已过期" in stale["notes"][-1]


def test_only_verified_real_sources_can_participate_in_confirmation():
    service = SectorExpansionRadarService()
    stocks = [
        _stock("600001", "芯片一号", "13:00"),
        _stock("600002", "芯片二号", "13:02"),
    ]
    fake_ladder = service.assess(
        _ladder(stocks, source="synthetic-demo-pool"),
        [_flow()],
        as_of=NOW,
    )
    fake_flow = service.assess(
        _ladder(stocks),
        [_flow(provider="eastmoney-mock")],
        as_of=NOW,
    )

    assert fake_ladder["items"] == []
    assert "真实源" in fake_ladder["notes"][-1]
    assert fake_flow["items"][0]["status"] == STATUS_WATCH
    assert "同名板块真实资金流" in fake_flow["items"][0]["missing"]


def test_missing_flow_timestamp_cannot_claim_fresh_kinetics():
    service = SectorExpansionRadarService()
    no_timestamp = _flow(flow_as_of=None, updated_at=None)
    result = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00"),
            _stock("600002", "芯片二号", "13:02"),
        ]),
        [no_timestamp],
        as_of=NOW,
    )

    assert result["items"][0]["status"] == STATUS_WATCH
    assert any("资金快照" in text for text in result["items"][0]["missing"])


def test_sector_aliases_are_canonicalized_before_theme_deduplication():
    service = SectorExpansionRadarService()
    stocks = [
        _stock(
            "600001",
            "芯片一号",
            "13:00",
            industry="申万半导体行业Ⅱ",
            concepts=["半导体板块"],
        ),
        _stock(
            "600002",
            "芯片二号",
            "13:02",
            industry="半导体行业",
            concepts=["东方财富半导体概念III"],
        ),
    ]

    result = service.assess(_ladder(stocks), [_flow()], as_of=NOW)

    assert [item["sector"] for item in result["items"]] == ["半导体"]
    assert result["items"][0]["new_limit_up_count"] == 2


def test_lunch_weekend_and_after_close_do_not_refresh_intraday_direction():
    service = SectorExpansionRadarService()
    stocks = [
        _stock("600001", "芯片一号", "11:27"),
        _stock("600002", "芯片二号", "11:29"),
    ]
    lunch = datetime(2026, 7, 15, 12, 0, tzinfo=TZ)
    after_close = datetime(2026, 7, 15, 15, 1, tzinfo=TZ)
    weekend = datetime(2026, 7, 18, 10, 0, tzinfo=TZ)

    lunch_result = service.assess(
        _ladder(stocks, updated_at=datetime(2026, 7, 15, 11, 30, tzinfo=TZ)),
        [_flow(flow_as_of="2026-07-15 11:30:00")],
        as_of=lunch,
    )
    close_result = service.assess(
        _ladder(stocks, updated_at=datetime(2026, 7, 15, 15, 0, tzinfo=TZ)),
        [_flow(flow_as_of="2026-07-15 15:00:00")],
        as_of=after_close,
    )
    weekend_result = service.assess(
        _ladder(stocks, trade_date="2026-07-18", updated_at=weekend),
        [_flow(flow_as_of="2026-07-18 10:00:00")],
        as_of=weekend,
    )

    assert lunch_result["items"] == []
    assert "不在集合竞价或连续交易时段" in lunch_result["notes"][-1]
    assert close_result["items"] == []
    assert "不在集合竞价或连续交易时段" in close_result["notes"][-1]
    assert weekend_result["items"] == []
    assert "非交易日" in weekend_result["notes"][-1]


def test_service_payload_matches_api_schema_for_confirmed_direction():
    service = SectorExpansionRadarService()
    payload = service.assess(
        _ladder([
            _stock("600001", "芯片一号", "13:00"),
            _stock("600002", "芯片二号", "13:02"),
        ]),
        [_flow()],
        as_of=NOW,
    )

    model = SectorExpansionRadarOut.model_validate(payload)
    assert model.items[0].status == STATUS_CONFIRMED
    assert model.items[0].buy_signal is False
    assert model.window_minutes == 15

    payload["items"][0]["buy_signal"] = True
    with pytest.raises(ValidationError):
        SectorExpansionRadarOut.model_validate(payload)
