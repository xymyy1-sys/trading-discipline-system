from types import SimpleNamespace

from app.models.trading import LimitUpPromotionSample
from app.services.limit_up_promotion import promotion_dashboard, record_closing_promotion_cohort


def stock(code: str, name: str, level: int, *, breaks: int = 0):
    return SimpleNamespace(
        code=code,
        name=name,
        consecutive_limit_days=level,
        break_count=breaks,
        amount=100_000_000,
        sealed_amount=20_000_000,
        turnover=8.0,
        industry="测试题材",
        first_limit_time="09:35:00",
        last_limit_time="14:30:00",
    )


def ladder(*stocks):
    return SimpleNamespace(groups=[SimpleNamespace(stocks=list(stocks))])


def atmosphere(*codes: str):
    roles = [
        SimpleNamespace(
            code=code,
            roles=["同身位竞争"],
            role_score=80 - rank,
            max_position_ratio=0.1,
        )
        for rank, code in enumerate(codes)
    ]
    return SimpleNamespace(theme_ladders=[SimpleNamespace(
        name="测试主线",
        stage="发酵",
        is_mainline=True,
        mainline_rank=1,
        promotion_rate=0.5,
        completeness_score=80,
        identity_roles=roles,
    )])


def test_each_board_level_is_recorded_and_resolved_independently(db_session):
    day_one = ladder(stock("600001", "一板甲", 1), stock("600002", "一板乙", 1), stock("600003", "二板甲", 2))
    prepared = record_closing_promotion_cohort(
        db_session,
        completed_trade_date="2026-08-03",
        ladder=day_one,
        atmosphere=atmosphere("600001", "600002", "600003"),
    )
    db_session.commit()

    assert prepared["600001"]["transition"] == "1进2"
    assert prepared["600003"]["transition"] == "2进3"
    assert prepared["600001"]["same_level_count"] == 2
    assert db_session.query(LimitUpPromotionSample).count() == 3

    day_two = ladder(stock("600001", "一板甲", 2), stock("600003", "二板甲", 3))
    record_closing_promotion_cohort(
        db_session,
        completed_trade_date="2026-08-04",
        ladder=day_two,
        atmosphere=atmosphere("600001", "600003"),
    )
    db_session.commit()

    outcomes = {
        row.code: row.status
        for row in db_session.query(LimitUpPromotionSample).filter(
            LimitUpPromotionSample.signal_date == "2026-08-03",
        )
    }
    assert outcomes == {"600001": "PROMOTED", "600002": "FAILED", "600003": "PROMOTED"}
    report = promotion_dashboard(db_session, signal_date="2026-08-04")
    history = {row["from_level"]: row for row in report["history"]}
    assert history[1]["sample_count"] == 2
    assert history[1]["promoted_count"] == 1
    assert history[2]["sample_count"] == 1
    assert history[2]["promoted_count"] == 1


def test_first_forward_cohort_exposes_priors_ranks_and_trial_caps(db_session):
    record_closing_promotion_cohort(
        db_session,
        completed_trade_date="2026-08-07",
        ladder=ladder(stock("600001", "一板甲", 1), stock("600002", "二板甲", 2)),
        atmosphere=atmosphere("600001", "600002"),
    )
    db_session.commit()

    report = promotion_dashboard(db_session, signal_date="2026-08-07")
    history = {row["from_level"]: row for row in report["history"]}
    assert history[1]["basis"] == "收缩先验"
    assert history[1]["posterior"] == 33.3
    items = {row["code"]: row for row in report["items"]}
    assert items["600001"]["same_level_rank"] == 1
    assert items["600001"]["trial_position_ratio"] == 0.1
    assert items["600002"]["trial_position_ratio"] == 0.1
