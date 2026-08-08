from app.models.trading import SimulationAccount, SimulationRuleRelease
from app.services import simulation_rule_promotion


def test_rule_candidate_is_frozen_before_forward_validation(db_session, monkeypatch):
    account = SimulationAccount(
        name="规则晋级测试账户",
        initial_cash=20_000,
        cash=20_000,
        account_type="shadow",
        automation_key="promotion-test",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    monkeypatch.setattr(
        simulation_rule_promotion,
        "simulation_calibration_proposal",
        lambda _db, _account: {
            "status": "READY_FOR_REVIEW",
            "candidates": [{"field": "entry_confirmation_gate", "direction": "tighten"}],
        },
    )

    created = simulation_rule_promotion.advance_rule_promotion(db_session, account)

    assert created is not None
    assert created.status == "TRAINING"
    assert created.baseline_closed_trade_id == 0
    assert created.forward_control_samples == 0
    assert created.forward_candidate_samples == 0
    assert db_session.query(SimulationRuleRelease).count() == 1
    version, parameters = simulation_rule_promotion.active_rule_parameters(db_session, account.id)
    assert version == "shadow-v5-multi-entry"
    assert parameters["entry_price_vs_vwap_max"] == 2.0


def test_training_candidate_does_not_promote_without_future_samples(db_session, monkeypatch):
    account = SimulationAccount(
        name="空样本测试账户",
        initial_cash=20_000,
        cash=20_000,
        account_type="shadow",
        automation_key="promotion-empty-test",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)
    row = SimulationRuleRelease(
        account_id=account.id,
        rule_version="shadow-auto-test",
        baseline_rule_version="shadow-v5-multi-entry",
        candidate_hash="a" * 64,
        status="TRAINING",
        parameters_json="{}",
    )
    db_session.add(row)
    db_session.commit()
    monkeypatch.setattr(
        simulation_rule_promotion,
        "simulation_calibration_proposal",
        lambda _db, _account: {"status": "INSUFFICIENT_SAMPLES", "candidates": []},
    )

    result = simulation_rule_promotion.advance_rule_promotion(db_session, account)

    assert result is not None
    assert result.status == "TRAINING"
    assert result.validated_at is None
    assert result.forward_control_samples == 0


def test_validated_rule_requires_manual_approval_before_activation(db_session):
    account = SimulationAccount(
        name="manual-approval-account",
        initial_cash=20_000,
        cash=20_000,
        account_type="shadow",
        automation_key="manual-approval-test",
    )
    db_session.add(account)
    db_session.flush()
    previous = SimulationRuleRelease(
        account_id=account.id,
        rule_version="shadow-active-old",
        baseline_rule_version="shadow-v5-multi-entry",
        candidate_hash="b" * 64,
        status="ACTIVE",
        parameters_json='{"entry_price_vs_vwap_max": 1.8}',
    )
    candidate = SimulationRuleRelease(
        account_id=account.id,
        rule_version="shadow-ready-new",
        baseline_rule_version="shadow-active-old",
        candidate_hash="c" * 64,
        status="READY_FOR_APPROVAL",
        parameters_json='{"entry_price_vs_vwap_max": 1.2}',
        forward_control_samples=30,
        forward_candidate_samples=30,
    )
    db_session.add_all([previous, candidate])
    db_session.commit()

    approved = simulation_rule_promotion.approve_rule_release(db_session, account, candidate.id)

    assert simulation_rule_promotion.MINIMUM_FORWARD_CONTROL == 30
    assert simulation_rule_promotion.MINIMUM_FORWARD_CANDIDATE == 30
    assert approved.status == "ACTIVE"
    db_session.refresh(previous)
    assert previous.status == "SUPERSEDED"
    version, parameters = simulation_rule_promotion.active_rule_parameters(db_session, account.id)
    assert version == "shadow-ready-new"
    assert parameters["entry_price_vs_vwap_max"] == 1.2
