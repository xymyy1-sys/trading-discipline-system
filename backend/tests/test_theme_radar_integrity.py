from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.schemas.trading import SectorFlowPoint, ThemeRadarOut
from app.services.market_data import MarketDataProvider, _get_current_theme_radar_cache


def _board(
    name: str,
    code: str,
    *,
    change: float,
    flow: float,
    large: float,
    leader: str,
    up: int,
    down: int,
) -> dict:
    return {
        "name": name,
        "board_code": code,
        "provider": "eastmoney",
        "change_pct": change,
        "net_inflow": flow,
        "main_inflow": large,
        "leaders": [leader],
        "limit_up_count": 0,
        "up_count": up,
        "down_count": down,
        "flat_count": 0,
        "stock_count": up + down,
    }


def _stock(code: str, name: str, change: float, flow: float, large: float) -> dict:
    return {
        "code": code,
        "name": name,
        "change_pct": change,
        "amount": 12.0,
        "main_inflow": flow,
        "large_inflow": large,
    }


def test_theme_classification_uses_board_name_not_temporary_leader() -> None:
    provider = MarketDataProvider()

    assert provider._classify_mainline({
        "name": "化债(AMC)概念",
        "leaders": ["中金黄金"],
        "theme_type": "概念",
    }) == "化债(AMC)概念"
    assert provider._classify_mainline({
        "name": "婴童概念",
        "leaders": ["航天彩虹"],
        "theme_type": "概念",
    }) == "婴童概念"
    assert provider._classify_mainline({
        "name": "化工原料",
        "leaders": ["卫星化学"],
        "theme_type": "行业",
    }) == "化工原料"
    assert provider._classify_mainline({
        "name": "MicroLED",
        "leaders": ["联环药业"],
        "theme_type": "概念",
    }) == "MicroLED"
    assert provider._classify_mainline({"name": "3D玻璃"}) == "3D玻璃"
    assert provider._classify_mainline({"name": "玻璃玻纤"}) == "玻璃玻纤"
    assert provider._classify_mainline({"name": "存储芯片"}) == "存储芯片"
    assert provider._classify_mainline({"name": "半导体概念"}) == "半导体 / 芯片"
    assert provider._classify_mainline({"name": "通信设备"}) == "通信设备"
    assert provider._classify_mainline({"name": "医疗服务"}) == "医疗服务"
    assert provider._classify_mainline({"name": "航空装备Ⅱ"}) == "航空装备Ⅱ"


def test_theme_radar_unions_only_real_related_board_constituents(monkeypatch) -> None:
    provider = MarketDataProvider()
    concept_rows = [
        _board("化债(AMC)概念", "BK_DEBT", change=0.6, flow=-0.7, large=-0.3, leader="中金黄金", up=20, down=10),
        _board("黄金概念", "BK_GOLD_C", change=-0.9, flow=-1.8, large=-0.8, leader="紫金矿业", up=9, down=24),
        _board("3D玻璃", "BK_3D", change=-4.5, flow=-9.7, large=-4.0, leader="联创电子", up=2, down=20),
    ]
    industry_rows = [
        _board("贵金属", "BK_GOLD_I", change=0.2, flow=-3.1, large=-1.0, leader="中金黄金", up=4, down=3),
        _board("玻璃玻纤", "BK_GLASS", change=-5.5, flow=-7.9, large=-3.0, leader="三峡新材", up=1, down=25),
        _board("电力", "BK_POWER", change=4.7, flow=31.1, large=13.0, leader="大唐发电", up=90, down=10),
    ]
    constituents = {
        "BK_DEBT": [_stock("300001", "化债软件", 8.0, 0.4, 0.2)],
        "BK_GOLD_C": [_stock("600489", "中金黄金", 2.4, 1.0, 0.4)],
        "BK_GOLD_I": [
            _stock("600489", "中金黄金", 2.4, 1.0, 0.4),
            _stock("601899", "紫金矿业", 1.0, 0.5, 0.2),
        ],
        "BK_3D": [_stock("002036", "联创电子", 10.0, 0.5, 0.2)],
        "BK_GLASS": [_stock("600293", "三峡新材", -5.0, -0.3, -0.1)],
        "BK_POWER": [
            _stock("601991", "大唐发电", 10.0, 3.0, 1.5),
            _stock("600011", "华能国际", 4.0, 2.0, 1.0),
        ],
    }

    def fetch_flow(flow_type: str, period: str):
        assert period == "今日"
        return concept_rows if flow_type == "概念资金流" else industry_rows

    monkeypatch.setattr(provider, "_fetch_direct_eastmoney_sector_flow_raw", fetch_flow)
    monkeypatch.setattr(provider, "_validate_theme_provider_rows", lambda rows: rows)
    monkeypatch.setattr(
        provider,
        "_theme_limit_up_security_codes",
        lambda: ({"601991"}, "2026-07-20"),
    )
    monkeypatch.setattr(provider, "_fetch_sector_constituents_raw", lambda code: constituents[code])
    monkeypatch.setattr(
        provider,
        "_theme_timeline",
        lambda raw: [SectorFlowPoint(time="15:00", value=float(raw.get("net_inflow") or 0))],
    )

    radar = provider.theme_radar(force_refresh=True)
    by_name = {theme.name: theme for theme in radar.themes}

    assert radar.strongest_theme is not None
    assert radar.strongest_theme.name == "电网设备 / 电力"
    assert "化债(AMC)概念" not in by_name["贵金属 / 黄金"].related_boards
    assert {stock.name for stock in by_name["贵金属 / 黄金"].core_stocks} == {
        "中金黄金",
        "紫金矿业",
    }
    assert by_name["贵金属 / 黄金"].stock_count == 2
    assert {stock.name for stock in by_name["化债(AMC)概念"].core_stocks} == {"化债软件"}
    assert by_name["3D玻璃"].name != by_name["玻璃玻纤"].name
    assert by_name["电网设备 / 电力"].score > by_name["贵金属 / 黄金"].score
    assert by_name["电网设备 / 电力"].limit_up_count == 1
    assert by_name["电网设备 / 电力"].score_basis
    assert by_name["电网设备 / 电力"].constituent_coverage == 1


def test_raw_sector_fields_do_not_fake_limit_up_or_duplicate_main_flow(monkeypatch) -> None:
    provider = MarketDataProvider()

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "data": {
                    "total": 1,
                    "diff": [{
                        "f12": "BK_TEST",
                        "f14": "测试概念",
                        "f2": 1000,
                        "f3": 2.5,
                        "f62": 3_000_000_000,
                        "f72": 700_000_000,
                        "f104": 30,
                        "f105": 12,
                        "f106": 3,
                        "f128": "真实领涨股",
                        "f204": "大单流入股",
                        "f124": int(datetime(2026, 7, 20, 15, 0).timestamp()),
                    }],
                }
            }

    monkeypatch.setattr("app.services.market_data.requests.get", lambda *args, **kwargs: Response())

    rows = provider._fetch_direct_eastmoney_sector_flow_raw("概念资金流", "今日")
    assert len(rows) == 1
    row = rows[0]
    assert row["net_inflow"] == 30.0
    assert row["main_inflow"] == 7.0
    assert row["leaders"] == ["真实领涨股"]
    assert row["stock_count"] == 45
    assert row["limit_up_count"] == 0


def test_negative_ranking_does_not_claim_a_confirmed_mainline(monkeypatch) -> None:
    provider = MarketDataProvider()
    rows = [
        _board("弱势概念甲", "BK_A", change=-1.0, flow=-2.0, large=-1.0, leader="甲", up=2, down=8),
        _board("弱势概念乙", "BK_B", change=-2.0, flow=-5.0, large=-2.0, leader="乙", up=1, down=9),
    ]
    stocks = {
        "BK_A": [_stock("600001", "甲", -1.0, -0.2, -0.1)],
        "BK_B": [_stock("600002", "乙", -2.0, -0.4, -0.2)],
    }
    monkeypatch.setattr(
        provider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        lambda flow_type, period: rows if flow_type == "概念资金流" else [],
    )
    monkeypatch.setattr(provider, "_validate_theme_provider_rows", lambda values: values)
    monkeypatch.setattr(
        provider,
        "_theme_limit_up_security_codes",
        lambda: (set(), "2026-07-20"),
    )
    monkeypatch.setattr(provider, "_fetch_sector_constituents_raw", lambda code: stocks[code])
    monkeypatch.setattr(
        provider,
        "_theme_timeline",
        lambda raw: [SectorFlowPoint(time="15:00", value=float(raw.get("net_inflow") or 0))],
    )

    radar = provider.theme_radar(force_refresh=True)
    assert radar.themes
    assert radar.strongest_theme is None
    assert radar.market_temperature == "低迷"
    assert radar.resonance == []


def test_equal_market_values_receive_equal_percentiles() -> None:
    rows = [
        {"theme_type": "概念", "change_pct": 0, "flow_ratio": 0},
        {"theme_type": "概念", "change_pct": 0, "flow_ratio": 0},
        {"theme_type": "概念", "change_pct": 3, "flow_ratio": 2},
    ]
    MarketDataProvider._annotate_theme_market_ranks(rows, group_by_type=True)
    assert rows[0]["change_percentile"] == rows[1]["change_percentile"]
    assert rows[0]["flow_percentile"] == rows[1]["flow_percentile"]


def test_theme_source_rejects_wrong_trade_date_quorum(monkeypatch) -> None:
    provider = MarketDataProvider()
    monkeypatch.setattr(
        "app.services.market_data._shanghai_now_naive",
        lambda: datetime(2026, 7, 20, 16, 0),
    )
    current = int(datetime(
        2026, 7, 20, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp())
    stale = int(datetime(
        2026, 7, 17, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp())
    rows = [{"provider_timestamp": current} for _ in range(18)] + [
        {"provider_timestamp": stale} for _ in range(2)
    ]
    with pytest.raises(ValueError, match="交易日不一致"):
        provider._validate_theme_provider_rows(rows)


def test_theme_constituents_reject_delayed_intraday_quorum(monkeypatch) -> None:
    provider = MarketDataProvider()
    monkeypatch.setattr(
        "app.services.market_data._shanghai_now_naive",
        lambda: datetime(2026, 7, 20, 10, 30),
    )
    delayed = int(datetime(
        2026, 7, 20, 10, 15, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp())
    rows = [{"f124": delayed} for _ in range(20)]
    with pytest.raises(ValueError, match="盘中快照过旧"):
        provider._validate_theme_constituent_rows(rows)


def test_theme_constituents_allow_small_suspended_minority(monkeypatch) -> None:
    provider = MarketDataProvider()
    monkeypatch.setattr(
        "app.services.market_data._shanghai_now_naive",
        lambda: datetime(2026, 7, 20, 10, 30),
    )
    recent = int(datetime(
        2026, 7, 20, 10, 27, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp())
    stale = int(datetime(
        2026, 7, 17, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")
    ).timestamp())
    rows = [{"f124": recent} for _ in range(19)] + [{"f124": stale}]
    provider._validate_theme_constituent_rows(rows)
    assert sum(row["_theme_quote_eligible"] is True for row in rows) == 19
    assert rows[-1]["_theme_quote_eligible"] is False


def test_theme_constituent_pagination_rejects_duplicate_page_drift(monkeypatch) -> None:
    provider = MarketDataProvider()

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            rows = [
                {
                    "f12": f"60{i:04d}",
                    "f14": f"成分{i}",
                    "f124": int(datetime(
                        2026, 7, 20, 15, 0,
                        tzinfo=ZoneInfo("Asia/Shanghai"),
                    ).timestamp()),
                }
                for i in range(100)
            ]
            return {"data": {"total": 101, "diff": rows}}

    monkeypatch.setattr(
        "app.services.market_data.requests.get",
        lambda *args, **kwargs: FakeResponse(),
    )
    with pytest.raises(ValueError, match="constituent pagination"):
        provider._fetch_sector_constituents_raw("BK_TEST")


def test_theme_cache_rejects_previous_trade_date(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_data._shanghai_now_naive",
        lambda: datetime(2026, 7, 20, 10, 30),
    )
    stale = ThemeRadarOut(
        source="eastmoney",
        updated_at=datetime(2026, 7, 17, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
        market_temperature="轮动",
        strongest_theme=None,
        resonance=[],
        themes=[],
        notes=[],
    )
    current = stale.model_copy(update={
        "updated_at": datetime(2026, 7, 20, 10, 25, tzinfo=ZoneInfo("Asia/Shanghai")),
    })

    monkeypatch.setattr(
        "app.services.market_data._get_response_cache",
        lambda *_args, **_kwargs: stale,
    )
    assert _get_current_theme_radar_cache(allow_stale=True) is None

    monkeypatch.setattr(
        "app.services.market_data._get_response_cache",
        lambda *_args, **_kwargs: current,
    )
    assert _get_current_theme_radar_cache(allow_stale=True) is current


def test_theme_radar_does_not_score_unverifiable_sina_fallback(monkeypatch) -> None:
    provider = MarketDataProvider()
    monkeypatch.setattr(
        provider,
        "_fetch_direct_eastmoney_sector_flow_raw",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("provider unavailable")),
    )
    monkeypatch.setattr(
        provider,
        "_fetch_sina_sector_flow_raw",
        lambda **_kwargs: pytest.fail("unverifiable fallback must not be called"),
    )
    monkeypatch.setattr(
        "app.services.market_data._get_cached_flow",
        lambda _key: None,
    )
    monkeypatch.setattr(
        provider,
        "_theme_limit_up_security_codes",
        lambda: (set(), "2026-07-20"),
    )

    radar = provider.theme_radar(force_refresh=True)

    assert radar.themes == []
    assert radar.strongest_theme is None
    assert all("sina" not in part.lower() for part in radar.source.split("+"))
    assert any("未采用无法核验时效的备用源" in note for note in radar.notes)


def test_aggregate_theme_timeline_discloses_representative_board_scope() -> None:
    provider = MarketDataProvider()
    raw = {
        "name": "半导体 / 芯片",
        "component_count": 3,
        "seed_board": {"name": "半导体"},
    }

    scope = provider._theme_timeline_scope(raw, include_timeline=True)

    assert "代表板块“半导体”" in scope
    assert "去重成分股的当前快照" in scope


def test_single_and_aggregate_themes_share_constituent_change_basis() -> None:
    provider = MarketDataProvider()
    raw = {
        "name": "单一板块",
        "change_pct": 9.9,
        "component_count": 1,
    }
    stocks = [
        _stock("600001", "甲", 1.0, 0.5, 0.2),
        _stock("600002", "乙", 3.0, 0.5, 0.2),
    ]

    enriched = provider._apply_theme_constituent_evidence(
        raw,
        stocks,
        expected_boards=1,
        loaded_boards=1,
        limit_up_codes=set(),
    )

    assert enriched["change_pct"] == 2.0


def test_aggregate_keeps_all_component_boards_for_membership() -> None:
    provider = MarketDataProvider()
    rows = []
    for index in range(9):
        row = _board(
            f"半导体别名{index}",
            f"BK{index}",
            change=1 + index / 10,
            flow=1 + index,
            large=0.5 + index,
            leader=f"股票{index}",
            up=5,
            down=2,
        )
        row.update({
            "provider": "eastmoney",
            "theme_type": "概念",
            "mainline": "半导体 / 芯片",
        })
        rows.append(row)

    aggregate = provider._aggregate_theme_mainlines(rows)[0]

    assert len(aggregate["component_boards"]) == 9
    assert len(aggregate["related_boards"]) == 8


def test_resonance_requires_multiple_positive_boards_not_alias_count() -> None:
    provider = MarketDataProvider()
    raw = {
        "net_inflow": 4,
        "main_inflow": 2,
        "change_pct": 2.5,
        "component_count": 3,
        "positive_component_count": 1,
        "limit_up_count": 0,
    }
    tags = provider._theme_resonance_tags(raw, [], 70)
    assert "多板块同向" not in tags

    raw["positive_component_count"] = 2
    assert "多板块同向" in provider._theme_resonance_tags(raw, [], 70)


def test_policy_and_supply_chain_concepts_are_not_permanently_filtered() -> None:
    provider = MarketDataProvider()
    assert provider._is_broad_style_label("华为概念") is False
    assert provider._is_broad_style_label("国企改革") is False
    assert provider._is_broad_style_label("重组概念") is False
    assert provider._is_broad_style_label("融资融券") is True
