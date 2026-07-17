import json
from datetime import datetime

from app.api.helpers import plan_calc
from app.api.helpers.plan_calc import _limit_up_next_day_plan
from app.api.routes.plans import _guard_limit_up_auction_update
from app.schemas.trading import (
    LimitUpAtmosphereMetrics,
    LimitUpAtmosphereOut,
    LimitUpIdentityRoleOut,
    LimitUpPlanCreate,
    LimitUpThemeLadderOut,
)


def _existing_plan(**updates) -> str:
    payload = {
        "mainline_name": "创新药",
        "mainline_rank": 1,
        "mainline_score": 86,
        "mainline_level": "核心主线",
        "is_mainline": True,
        "theme_stage": "发酵",
        "theme_stage_reason": "资金与涨停梯队共同扩散",
        "identity_roles": ["全场最高标", "题材最高标", "龙头候选"],
        "identity_action": "竞价和承接确认后评估",
        "position_rule": "单只仓位上限10%",
        "theme_evidence": ["题材排名第1", "主力资金净流入"],
        "max_position_ratio": 0.1,
        "overnight_order": True,
        "order_price": 10.0,
    }
    payload.update(updates)
    return json.dumps(payload, ensure_ascii=False)


def test_limit_up_update_cannot_raise_system_position_cap_or_forge_context():
    guarded = _guard_limit_up_auction_update(
        _existing_plan(max_position_ratio=0.05),
        {
            "max_position_ratio": 0.1,
            "overnight_order": True,
            "mainline_name": "伪造主线",
            "mainline_rank": 1,
            "identity_roles": ["全场最高标"],
        },
    )

    assert guarded["max_position_ratio"] == 0.05
    assert guarded["mainline_name"] == "创新药"
    assert guarded["identity_roles"] == ["全场最高标", "题材最高标", "龙头候选"]


def test_limit_up_update_fails_closed_for_non_mainline_or_climax():
    for existing in (
        _existing_plan(is_mainline=False, mainline_level="非主线题材"),
        _existing_plan(theme_stage="高潮"),
        _existing_plan(identity_roles=[]),
    ):
        guarded = _guard_limit_up_auction_update(
            existing,
            {"max_position_ratio": 0.1, "overnight_order": True},
        )

        assert guarded["max_position_ratio"] == 0
        assert guarded["overnight_order"] is False


def test_old_limit_up_plan_without_system_evidence_cannot_be_opened_by_client():
    guarded = _guard_limit_up_auction_update(
        json.dumps({"max_position_ratio": 0.1, "overnight_order": True}),
        {
            "max_position_ratio": 0.1,
            "overnight_order": True,
            "is_mainline": True,
            "theme_stage": "发酵",
            "identity_roles": ["龙头候选"],
        },
    )

    assert guarded["max_position_ratio"] == 0
    assert guarded["overnight_order"] is False
    assert "is_mainline" not in guarded


def _atmosphere_with_mainline_role() -> LimitUpAtmosphereOut:
    role = LimitUpIdentityRoleOut(
        code="600664",
        name="测试龙头",
        level=6,
        roles=["全场最高标", "题材最高标", "龙头候选"],
        role_score=92,
        reason="题材和全市场高度均为6板",
        recommended_action="竞价与承接确认后评估，单只上限5%",
        max_position_ratio=0.05,
        risk_level="中",
        persistence_basis=["题材定位=核心主线", "题材阶段=分歧"],
    )
    theme = LimitUpThemeLadderOut(
        name="创新药",
        limit_up_count=8,
        completeness_label="多层梯队已成形",
        action="只做主线前排",
        continuation_expectation="等待分歧转强",
        identity_roles=[role],
        mainline_name="创新药",
        mainline_rank=1,
        mainline_score=88,
        mainline_level="核心主线",
        is_mainline=True,
        stage="分歧",
        stage_reason="内部强弱分化，观察核心承接",
        stage_position_rule="分歧阶段仅允许核心确认后上限5%",
        max_position_ratio=0.05,
        eligible_roles=["全场最高标", "题材最高标", "龙头候选"],
        evidence=["题材资金排名第1", "涨停梯队完整"],
    )
    return LimitUpAtmosphereOut(
        source="eastmoney-test",
        trade_date="2026-07-17",
        updated_at=datetime.now(),
        decision="ALLOW",
        decision_label="允许评估打板（仅限前排确认）",
        score=6,
        data_quality="完整",
        metrics=LimitUpAtmosphereMetrics(limit_up_count=30, highest_board=6),
        theme_ladders=[theme],
    )


def test_limit_up_plan_inherits_exact_stock_identity_and_system_cap(monkeypatch):
    atmosphere = _atmosphere_with_mainline_role()
    monkeypatch.setattr(
        plan_calc,
        "_get_response_cache",
        lambda key: atmosphere if key == "limit-up-atmosphere-latest" else None,
    )
    payload = LimitUpPlanCreate(
        code="600664",
        name="测试龙头",
        price=10,
        level=6,
        industry="化学制药",
        concepts=["创新药"],
        max_position_ratio=0.1,
    )

    plan = _limit_up_next_day_plan(payload, "2026-07-20")
    auction = json.loads(plan.auction_plan)

    assert auction["mainline_name"] == "创新药"
    assert auction["theme_stage"] == "分歧"
    assert auction["identity_roles"] == ["全场最高标", "题材最高标", "龙头候选"]
    assert auction["max_position_ratio"] == 0.05
    assert auction["overnight_order"] is True
    assert plan.holding_category == "主线前排股"


def test_limit_up_plan_without_exact_front_role_is_observation_only(monkeypatch):
    atmosphere = _atmosphere_with_mainline_role()
    monkeypatch.setattr(
        plan_calc,
        "_get_response_cache",
        lambda key: atmosphere if key == "limit-up-atmosphere-latest" else None,
    )
    payload = LimitUpPlanCreate(
        code="600999",
        name="测试后排",
        price=10,
        level=1,
        industry="化学制药",
        concepts=["创新药"],
        max_position_ratio=0.1,
    )

    plan = _limit_up_next_day_plan(payload, "2026-07-20")
    auction = json.loads(plan.auction_plan)

    assert auction["max_position_ratio"] == 0
    assert auction["overnight_order"] is False
    assert "只生成观察预案" in auction["identity_action"]
    assert plan.holding_category == "非主线观察股"
