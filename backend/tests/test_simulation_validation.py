from datetime import datetime, timedelta
from types import SimpleNamespace

from app.services.simulation_validation import build_walk_forward_folds


def _trade(index: int, pnl: float):
    opened = datetime(2026, 7, 1, 10, 0) + timedelta(days=index)
    return SimpleNamespace(
        id=index + 1,
        opened_at=opened,
        closed_at=opened + timedelta(hours=4),
        realized_pnl=pnl,
        return_pct=pnl / 10,
    )


def test_walk_forward_never_uses_test_trade_in_training_window():
    rows = [_trade(index, 10 if index % 2 == 0 else -5) for index in range(12)]
    folds = build_walk_forward_folds(rows, minimum_train_samples=6, test_fold_size=3)
    assert len(folds) == 2
    assert folds[0]["training"]["sample_count"] == 6
    assert folds[0]["out_of_sample"]["sample_count"] == 3
    assert folds[0]["training_end_at"] < folds[0]["test_start_at"]
    assert folds[1]["training"]["sample_count"] == 9
    assert folds[1]["out_of_sample"]["sample_count"] == 3


def test_walk_forward_reports_no_fold_before_minimum_training_samples():
    rows = [_trade(index, 10) for index in range(5)]
    assert build_walk_forward_folds(rows, minimum_train_samples=6, test_fold_size=2) == []


def test_validation_endpoint_reports_insufficient_without_fabricating_samples(client):
    account = client.post(
        "/api/simulation/accounts",
        json={"name": "验证账户", "initial_cash": 20000},
    ).json()
    response = client.get(f"/api/simulation/accounts/{account['id']}/validation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "insufficient_samples"
    assert payload["formal_point_in_time_samples"] == 0
    assert payload["folds"] == []
