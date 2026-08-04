from app.services import autonomous_selection
from app.services.autonomous_selection import (
    _sector_coverage_audit,
    _strong_sector_core_signals,
    autonomous_selection_targets,
    rank_full_market_rows,
)


def quote(
    code: str,
    name: str,
    *,
    price: float = 10.1,
    change: float = 1.2,
    amount: float = 500_000_000,
    volume_lots: float = 500_000,
    turnover: float = 5,
    volume_ratio: float = 2,
    industry: str = "测试行业",
) -> dict:
    return {
        "f12": code,
        "f14": name,
        "f2": price,
        "f3": change,
        "f5": volume_lots,
        "f6": amount,
        "f8": turnover,
        "f10": volume_ratio,
        "f62": 80_000_000,
        "f100": industry,
        "f184": 3.2,
    }


def test_rank_scans_independent_rows_and_rejects_chasing_and_st_names():
    rows = [
        quote("600001", "独立候选"),
        quote("600002", "ST风险股"),
        quote("600003", "直线追高", price=10.8, change=7.2),
    ]
    result = rank_full_market_rows(rows, minimum_score=60)
    assert [item["code"] for item in result] == ["600001"]
    assert result[0]["style"] in {"回踩承接", "趋势确认"}
    assert "不依赖" not in "".join(result[0]["reasons"])


def test_rank_diversifies_industry_and_applies_small_forward_feedback():
    rows = [quote(f"60000{index}", f"候选{index}") for index in range(1, 5)]
    rows.append(quote("000001", "跨行业", industry="另一行业"))
    result = rank_full_market_rows(
        rows,
        feedback={"600001": -10, "000001": 5},
        minimum_score=55,
        limit=4,
    )
    assert sum(item["industry"] == "测试行业" for item in result) <= 2
    assert any(item["code"] == "000001" and item["feedback_adjustment"] == 5 for item in result)


def test_existing_screens_are_bounded_traceable_evidence_not_a_direct_buy_signal():
    rows = [quote("600001", "多源候选", change=-4.0, volume_ratio=0.6, turnover=2.0)]
    result = rank_full_market_rows(
        rows,
        reference_signals={"600001": ["断板反包", "抓涨停"]},
        source_feedback={"断板反包": {"score_adjustment": 2}, "抓涨停": {"score_adjustment": -1}},
        minimum_score=30,
    )
    assert result[0]["source_tags"] == ["断板反包", "抓涨停"]
    assert sum(item["base"] + item["learned"] for item in result[0]["source_contributions"]) == 12
    assert any("不单独触发买入" in reason for reason in result[0]["reasons"])


def test_strong_sector_core_expands_discovery_but_excludes_non_main_board():
    class Provider:
        def _fetch_direct_eastmoney_sector_flow_raw(self, flow_type, period):
            return [{
                "name": f"强{flow_type}", "board_code": f"BK{flow_type[:2]}",
                "change_pct": 2.5, "net_inflow": 18.0, "strength": 85,
            }]

        def _fetch_sector_constituents_raw(self, board_code):
            return [
                {"code": "600001", "name": "容量龙头", "price": 10, "change_pct": 2, "amount": 20, "main_inflow": 3, "theme_quote_eligible": True},
                {"code": "002001", "name": "资金核心", "price": 12, "change_pct": 3, "amount": 12, "main_inflow": 5, "theme_quote_eligible": True},
                {"code": "300001", "name": "创业板标的", "price": 20, "change_pct": 8, "amount": 30, "main_inflow": 9, "theme_quote_eligible": True},
            ]

    signals, status = _strong_sector_core_signals(Provider())

    assert "600001" in signals
    assert "002001" in signals
    assert "300001" not in signals
    assert status["core_security_count"] == 2
    assert all(item["source"] == "强板块核心" for item in signals["600001"])


def test_sector_core_is_traceable_and_coverage_explains_omission():
    rows = [
        quote("600001", "量价合格"),
        quote("600002", "过度追高", price=12, change=7.5),
    ]
    contexts = {
        "600001": [{"source": "强板块核心", "sector": "半导体", "sector_rank": 1, "role": "容量核心"}],
        "600002": [{"source": "强板块核心", "sector": "半导体", "sector_rank": 1, "role": "强度核心"}],
    }
    selected = rank_full_market_rows(rows, sector_signals=contexts, minimum_score=60)
    audit = _sector_coverage_audit(rows, contexts, selected, [])

    assert selected[0]["source_tags"] == ["强板块核心"]
    assert selected[0]["sector_contexts"][0]["sector"] == "半导体"
    omitted = next(item for item in audit["items"] if item["code"] == "600002")
    assert omitted["status"] == "未入选"
    assert any("追高区" in reason for reason in omitted["reasons"])


def test_minute_targets_include_bounded_deduplicated_exploration_universe(monkeypatch):
    payload = {
        "items": [{"code": "600001", "name": "正式候选"}],
        "exploration_items": [
            {"code": "600001", "name": "重复候选"},
            *({"code": f"000{index:03d}", "name": f"探索{index}"} for index in range(1, 25)),
        ],
    }
    monkeypatch.setattr(autonomous_selection, "latest_autonomous_selection", lambda *args, **kwargs: payload)

    targets = autonomous_selection_targets(object(), "2026-08-04")

    assert targets[0] == ("600001", "正式候选")
    assert len(targets) == 20  # 1 formal + first 20 exploration rows - one duplicate
    assert len({code for code, _ in targets}) == len(targets)
