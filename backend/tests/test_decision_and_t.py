from app.api.helpers.decision import build_expectation_snapshot, build_t_eligibility, create_t_plan
from app.models.trading import Holding


def test_expectation_snapshot_marks_underperform(db_session):
    snapshot = build_expectation_snapshot(
        db_session,
        "600000",
        name="预期测试",
        quote={"price": 9.5, "prev_close": 10, "open": 9.4, "change_pct": -5},
        base_hint="强预期 主线前排",
    )

    assert snapshot.base_expectation == "STRONG"
    assert snapshot.expectation_result in {"WEAKER", "SLIGHTLY_WEAKER"}
    assert snapshot.expectation_gap_score < 0
    assert "禁止补仓" in snapshot.suggestion or "降风险" in snapshot.suggestion


def test_t_eligibility_forbids_invalid_trade(db_session):
    holding = Holding(
        code="600001",
        name="禁T测试",
        quantity=1000,
        cost_price=10,
        current_price=9.2,
        total_asset=100000,
        position_type="打板仓",
        next_discipline="证伪退出",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    eligibility = build_t_eligibility(db_session, holding)

    assert eligibility.eligible is False
    assert eligibility.t_type == "NO_T"
    assert eligibility.forbidden_reasons


def test_create_positive_t_plan_when_eligible(db_session):
    holding = Holding(
        code="600002",
        name="正T测试",
        quantity=2000,
        cost_price=10,
        current_price=10.8,
        total_asset=100000,
        position_type="盈利趋势仓",
        next_discipline="允许小比例正T",
    )
    db_session.add(holding)
    db_session.commit()
    db_session.refresh(holding)

    plan = create_t_plan(db_session, holding)

    assert plan.t_type in {"POSITIVE_T", "NO_T"}
    if plan.t_type == "POSITIVE_T":
        assert plan.planned_sell_quantity > 0
        assert plan.buyback_conditions
    else:
        assert plan.status == "forbidden"
