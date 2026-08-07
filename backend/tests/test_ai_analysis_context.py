from datetime import datetime, timedelta

from app.models.trading import ActionRecommendation, ExpectationSnapshot, Holding
from app.services.ai_analysis import _context


def test_market_ai_context_only_contains_current_holding_lifecycle(db_session):
    current = Holding(
        code="600999",
        name="当前持仓",
        quantity=100,
        cost_price=10,
        current_price=11,
        total_asset=100000,
    )
    db_session.add(current)
    db_session.flush()
    db_session.add_all(
        [
            ExpectationSnapshot(
                trade_date="2026-08-05",
                code="600999",
                name="当前持仓",
                stage="intraday",
                base_expectation="STRONG",
                expectation_result="MATCHED",
            ),
            ExpectationSnapshot(
                trade_date="2026-08-05",
                code="600888",
                name="已删除持仓",
                stage="intraday",
                base_expectation="WEAK",
                expectation_result="INVALID",
            ),
            ActionRecommendation(
                trade_date="2026-08-05",
                target_key=f"holding:{current.id}",
                holding_id=current.id,
                code=current.code,
                name=current.name,
                level="WARN",
                state="PROTECT",
                action="保护利润",
                expires_at=datetime.now() + timedelta(minutes=15),
            ),
            ActionRecommendation(
                trade_date="2026-08-05",
                target_key="holding:88:old",
                holding_id=88,
                code="600888",
                name="已删除持仓",
                level="REDUCE",
                state="EXIT_REQUIRED",
                action="全部退出",
                expires_at=datetime.now() + timedelta(minutes=15),
            ),
        ]
    )
    db_session.commit()

    payload = _context(db_session, "market", "today-dingtalk")

    assert [item["code"] for item in payload["holdings"]] == ["600999"]
    assert set(payload["expectations"]) == {"600999"}
    assert [item["code"] for item in payload["active_alerts"]] == ["600999"]
