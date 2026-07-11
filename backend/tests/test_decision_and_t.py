from app.api.helpers.decision import build_expectation_snapshot, build_t_eligibility, create_t_plan
from app.api.helpers.volume_price import build_volume_price_snapshot
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


def test_volume_price_snapshot_detects_vwap_breakdown(db_session):
    snapshot = build_volume_price_snapshot(
        db_session,
        "600003",
        name="量价测试",
        quote={
            "price": 9.5,
            "change_pct": -4.0,
            "open": 10.1,
            "prev_close": 10.0,
            "high": 10.8,
            "low": 9.4,
            "amount": 12.0,
            "volume": 100_000_000,
            "turnover": 8.2,
            "note": "东方财富实时行情",
        },
    )

    assert snapshot.pattern in {"冲高回落跌破VWAP", "跌破VWAP"}
    assert snapshot.price_vs_vwap < 0
    assert snapshot.high_drawdown > 10
    assert snapshot.data_quality == "realtime"
    assert snapshot.evidence


def test_decision_card_includes_volume_price(client, monkeypatch):
    quote = {
        "price": 10.5,
        "change_pct": 2.0,
        "open": 10.1,
        "prev_close": 10.0,
        "high": 10.8,
        "low": 10.0,
        "amount": 8.0,
        "volume": 80_000_000,
        "turnover": 6.5,
        "note": "东方财富实时行情",
    }

    monkeypatch.setattr("app.api.helpers.decision.quote_for_code", lambda code: quote)

    response = client.get("/api/stocks/600004/decision-card")

    assert response.status_code == 200
    payload = response.json()
    assert payload["volume_price"]["code"] == "600004"
    assert payload["volume_price"]["pattern"]


def test_expectation_create_and_update_routes(client, monkeypatch):
    quote = {
        "price": 9.8,
        "change_pct": -2.0,
        "open": 9.7,
        "prev_close": 10.0,
        "high": 10.0,
        "low": 9.6,
        "amount": 5.0,
        "note": "东方财富实时行情",
    }

    monkeypatch.setattr("app.api.helpers.decision.quote_for_code", lambda code: quote)

    created = client.post(
        "/api/expectations",
        json={"code": "600005", "name": "预期路由", "base_hint": "强预期 主线前排", "stage": "开盘确认"},
    )

    assert created.status_code == 200
    snapshot_id = created.json()["id"]

    updated = client.put(
        f"/api/expectations/{snapshot_id}",
        json={"stage": "五分钟确认", "suggestion": "人工校准后先降风险。", "evidence": ["五分钟承接不足。"]},
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["stage"] == "五分钟确认"
    assert payload["suggestion"] == "人工校准后先降风险。"
    assert payload["evidence"] == ["五分钟承接不足。"]
