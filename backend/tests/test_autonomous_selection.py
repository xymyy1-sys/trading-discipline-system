from app.services import autonomous_selection
from app.services.autonomous_selection import autonomous_selection_targets, rank_full_market_rows


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
