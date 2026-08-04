from datetime import datetime, timedelta

from app.services.simulation_risk import evaluate_risk_guard


NOW = datetime(2026, 8, 4, 10, 30)


def test_drawdown_hard_stop_blocks_only_new_entries():
    guard = evaluate_risk_guard(
        drawdown_pct=-8.1, daily_loss_pct=0, consecutive_formal_losses=0,
        last_formal_loss_at=None, evaluated_at=NOW,
    )
    assert guard.state == "STOPPED"
    assert guard.block_new_entries is True
    assert guard.position_multiplier == 0


def test_two_formal_losses_halves_new_position():
    guard = evaluate_risk_guard(
        drawdown_pct=-2, daily_loss_pct=0, consecutive_formal_losses=2,
        last_formal_loss_at=NOW - timedelta(hours=2), evaluated_at=NOW,
    )
    assert guard.state == "DE_RISK"
    assert guard.block_new_entries is False
    assert guard.position_multiplier == 0.5


def test_four_losses_stop_expires_after_cooling_period():
    guard = evaluate_risk_guard(
        drawdown_pct=-2, daily_loss_pct=0, consecutive_formal_losses=4,
        last_formal_loss_at=NOW - timedelta(hours=25), evaluated_at=NOW,
    )
    assert guard.state == "DE_RISK"
    assert guard.block_new_entries is False
