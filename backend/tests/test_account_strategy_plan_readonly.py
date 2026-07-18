from __future__ import annotations

from app.api.helpers.plan_calc import _next_trade_date
from app.models.trading import AccountState, Holding, NextDayPlan, StrategyTemplate


def _forbid(label: str):
    def fail(*_args, **_kwargs):
        raise AssertionError(f"GET endpoint attempted {label}")

    return fail


def test_account_and_holding_summary_gets_do_not_seed_or_commit(
    client,
    db_session,
    monkeypatch,
):
    from app.api.routes import holdings as routes

    holding = Holding(
        code="600001",
        name="只读账户样本",
        quantity=100,
        cost_price=9,
        current_price=10,
        total_asset=100_000,
    )
    db_session.add(holding)
    db_session.commit()

    monkeypatch.setattr(routes, "_account_state", _forbid("account seed"))
    monkeypatch.setattr(routes, "_refresh_holding_prices", _forbid("quote provider"))
    monkeypatch.setattr(db_session, "add", _forbid("Session.add"))
    monkeypatch.setattr(db_session, "commit", _forbid("Session.commit"))

    asset = client.get("/api/account/asset")
    summary = client.get("/api/holdings/summary")
    export = client.get("/api/exports/holdings.csv")

    assert asset.status_code == 200
    assert asset.json() == {"total_asset": 100_000.0, "updated_at": None}
    assert summary.status_code == 200
    assert summary.json()["total_asset"] == 100_000.0
    assert export.status_code == 200
    assert "600001" in export.text
    assert db_session.query(AccountState).count() == 0


def test_strategy_get_returns_transient_defaults_without_seeding(
    client,
    db_session,
    monkeypatch,
):
    monkeypatch.setattr(db_session, "add", _forbid("Session.add"))
    monkeypatch.setattr(db_session, "commit", _forbid("Session.commit"))

    response = client.get("/api/strategies/templates")

    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 12
    assert all(row["id"] < 0 and row["version"] == 0 for row in rows)
    assert db_session.query(StrategyTemplate).count() == 0


def test_saving_transient_strategy_is_an_explicit_materialisation(client, db_session):
    transient = client.get("/api/strategies/templates").json()[0]
    transient["position_limit"] = 0.3

    response = client.put(
        f"/api/strategies/templates/{transient['id']}",
        json=transient,
    )

    assert response.status_code == 200
    assert response.json()["id"] > 0
    assert response.json()["version"] == 1
    assert response.json()["position_limit"] == 0.3
    assert db_session.query(StrategyTemplate).count() == 1


def test_next_day_plan_get_ignores_legacy_refresh_query_and_stays_pure(
    client,
    db_session,
    monkeypatch,
):
    from app.api.routes import plans as routes

    plan = NextDayPlan(
        plan_date=_next_trade_date(),
        code="600002",
        name="只读计划",
        current_price=10,
    )
    db_session.add(plan)
    db_session.commit()

    monkeypatch.setattr(
        routes,
        "_refresh_existing_holding_plans",
        _forbid("plan refresh/provider"),
    )
    monkeypatch.setattr(db_session, "add", _forbid("Session.add"))
    monkeypatch.setattr(db_session, "commit", _forbid("Session.commit"))

    response = client.get("/api/next-day-plans?refresh=true")

    assert response.status_code == 200
    assert response.json()[0]["code"] == "600002"
    assert response.json()[0]["current_price"] == 10


def test_next_day_plan_refresh_requires_explicit_post(client, db_session, monkeypatch):
    from app.api.routes import plans as routes

    plan = NextDayPlan(
        plan_date=_next_trade_date(),
        code="600003",
        name="显式刷新计划",
        current_price=10,
    )
    db_session.add(plan)
    db_session.commit()
    calls: list[list[int]] = []

    def fake_refresh(rows, _db):
        calls.append([row.id for row in rows])
        return {"600003": "已显式刷新"}

    monkeypatch.setattr(routes, "_refresh_existing_holding_plans", fake_refresh)

    response = client.post("/api/next-day-plans/refresh")

    assert response.status_code == 200
    assert calls == [[plan.id]]
    assert response.json()[0]["price_note"] == "已显式刷新"
