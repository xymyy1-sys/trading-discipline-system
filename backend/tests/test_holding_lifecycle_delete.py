from datetime import datetime, timedelta, timezone

from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.models.trading import (
    AccountState,
    ActionRecommendation,
    DataCaptureSnapshot,
    Holding,
    HoldingSyncBaseline,
    NextDayPlan,
    PositionExecutionState,
    ProfitProtectionSnapshot,
    TTradePlan,
    TradeLog,
)


def test_delete_then_readd_starts_a_fresh_holding_lifecycle(client, db_session, monkeypatch):
    from app.api.routes import holdings as holdings_routes

    old_holding = Holding(
        id=1,
        code="600584",
        name="旧长电科技",
        quantity=200,
        cost_price=94.75,
        current_price=101.11,
        total_asset=100_000,
        position_type="旧持仓类型",
        next_discipline="旧纪律",
    )
    db_session.add_all([
        AccountState(id=1, total_asset=100_000),
        old_holding,
        HoldingSyncBaseline(
            code="600584",
            name="旧长电科技",
            quantity=200,
            cost_price=94.75,
            current_price=101.11,
            total_asset=100_000,
            position_type="旧持仓类型",
            next_discipline="旧纪律",
        ),
        PositionExecutionState(
            holding_id=77,
            code="600584",
            name="旧长电科技",
            trade_date=shanghai_today().isoformat(),
            state="EXIT_REQUIRED",
            recommended_action="全部退出",
        ),
        ProfitProtectionSnapshot(
            holding_id=77,
            code="600584",
            maximum_profit_pct=30,
            maximum_price=130,
            triggered=True,
        ),
        NextDayPlan(
            plan_date="2000-01-01",
            plan_type="holding",
            holding_id=1,
            code="600584",
            name="历史计划",
        ),
        NextDayPlan(
            plan_date=shanghai_today().isoformat(),
            plan_type="holding",
            holding_id=1,
            code="600584",
            name="当前计划",
        ),
        TTradePlan(
            holding_id=1,
            trade_date=shanghai_today().isoformat(),
            code="600584",
            name="旧长电科技",
            status="planned",
        ),
        ActionRecommendation(
            trade_date=shanghai_today().isoformat(),
            target_key="holding:1",
            holding_id=1,
            code="600584",
            name="旧长电科技",
            level="HIGH",
            state="EXIT_REQUIRED",
            action="全部退出",
            expires_at=shanghai_now_naive() + timedelta(minutes=15),
        ),
        TradeLog(
            code="600584",
            name="旧长电科技",
            traded_at=datetime.now(timezone.utc) - timedelta(days=10),
            side="买入",
            price=90,
            quantity=100,
            amount=9_000,
            total_asset=100_000,
            position_ratio=0.09,
            cost_price=90,
            stop_loss_price=86.4,
            reason="历史交易审计",
        ),
    ])
    db_session.commit()
    monkeypatch.setattr(holdings_routes, "_refresh_holding_prices", lambda rows, db: {})

    deleted = client.delete("/api/holdings/1")
    assert deleted.status_code == 200
    assert db_session.get(Holding, 1) is None
    assert db_session.query(HoldingSyncBaseline).filter_by(code="600584").first() is None
    assert db_session.query(TradeLog).filter_by(code="600584").count() == 1
    assert db_session.query(NextDayPlan).filter_by(name="历史计划").count() == 1
    assert db_session.query(NextDayPlan).filter_by(name="当前计划").count() == 0
    t_plan = db_session.query(TTradePlan).one()
    assert t_plan.status == "cancelled"
    assert "持仓已删除" in t_plan.execution_note
    recommendation = db_session.query(ActionRecommendation).filter_by(target_key="holding:1").one()
    assert recommendation.expires_at is not None
    assert recommendation.expires_at <= shanghai_now_naive()
    assert db_session.query(ActionRecommendation).filter_by(target_key="holding:1").count() == 1
    active_alerts = client.get("/api/alerts/active?include_acknowledged=true")
    assert active_alerts.status_code == 200
    assert all(item["target_key"] != "holding:1" for item in active_alerts.json())
    reset = db_session.query(DataCaptureSnapshot).filter_by(
        data_type="holding_lifecycle_reset", target_code="600584"
    ).one()
    assert reset.status == "deleted"

    # A sync while the stock is absent creates a zero baseline.  A later
    # manual add must replace it instead of disappearing on the next rebuild.
    absent_sync = client.post("/api/holdings/sync-from-trades")
    assert absent_sync.status_code == 200, absent_sync.text
    zero_baseline = db_session.query(HoldingSyncBaseline).filter_by(code="600584").one()
    assert zero_baseline.quantity == 0

    created = client.post("/api/holdings", json={
        "code": "600584",
        "name": "新长电科技",
        "quantity": 50,
        "cost_price": 120,
        "current_price": 121,
        "total_asset": 100_000,
        "position_type": "全新持仓",
        "next_discipline": "全新纪律",
    })
    assert created.status_code == 200, created.text
    fresh = created.json()
    assert fresh["id"] > 77
    assert fresh["quantity"] == 50
    assert fresh["cost_price"] == 120
    assert fresh["position_type"] == "全新持仓"
    fresh_id = int(fresh["id"])
    fresh_baseline = db_session.query(HoldingSyncBaseline).filter_by(code="600584").one()
    assert fresh_baseline.quantity == 50
    assert fresh_baseline.cost_price == 120
    lifecycle_start = db_session.query(DataCaptureSnapshot).filter_by(
        data_type="holding_lifecycle_start", target_code="600584"
    ).one()
    assert lifecycle_start.status == "active"

    review = client.get("/api/stocks/600584/intraday-review")
    assert review.status_code == 200, review.text
    assert review.json()["latest_state"] != "EXIT_REQUIRED"
    assert review.json()["latest_action"] != "全部退出"
    risk = client.get("/api/account/risk")
    assert risk.status_code == 200, risk.text
    assert risk.json()["degraded_position_count"] == 0
    candidates = client.get("/api/candidates")
    assert candidates.status_code == 200, candidates.text
    fresh_candidate = next(item for item in candidates.json() if item["code"] == "600584")
    assert fresh_candidate["execution_state"] == ""

    # A later explicit trade sync may rebuild the row, but trades from before
    # the deletion cutoff must not be replayed into this new lifecycle.  A
    # rebuild is not another lifecycle boundary: the fresh identity and all
    # state associated with it must remain stable across repeated syncs.
    db_session.add_all([
        PositionExecutionState(
            holding_id=fresh_id,
            code="600584",
            name="新长电科技",
            trade_date=shanghai_today().isoformat(),
            state="NORMAL_HOLD",
            recommended_action="继续持有",
        ),
        NextDayPlan(
            plan_date=shanghai_today().isoformat(),
            plan_type="holding",
            holding_id=fresh_id,
            code="600584",
            name="新生命周期计划",
        ),
    ])
    db_session.commit()
    for _ in range(2):
        synced = client.post("/api/holdings/sync-from-trades")
        assert synced.status_code == 200, synced.text
    rows = db_session.query(Holding).filter_by(code="600584").all()
    assert len(rows) == 1
    assert rows[0].id == fresh_id
    assert rows[0].id != old_holding.id
    assert rows[0].quantity == 50
    assert rows[0].cost_price == 120
    assert rows[0].position_type == "全新持仓"
    assert rows[0].next_discipline == "全新纪律"
    assert db_session.query(PositionExecutionState).filter_by(
        holding_id=fresh_id,
        state="NORMAL_HOLD",
    ).count() == 1
    assert db_session.query(NextDayPlan).filter_by(
        holding_id=fresh_id,
        name="新生命周期计划",
    ).count() == 1
