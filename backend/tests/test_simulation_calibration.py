from datetime import datetime, timedelta
import json

from app.models.trading import (
    SimulationAccount,
    SimulationClosedTrade,
    SimulationEvidenceSnapshot,
    SimulationShadowDecision,
)


def _account(db_session, *, manual: bool = False) -> SimulationAccount:
    row = SimulationAccount(
        name="前向校准账户",
        initial_cash=200000,
        cash=200000,
        account_type="manual" if manual else "shadow",
        automation_key=None if manual else "test-shadow-forward-v1",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def _closed_trade(
    db_session,
    account: SimulationAccount,
    *,
    index: int,
    gap_band: str,
    return_pct: float,
    quality: str = "realtime",
    source_fault: str = "",
    include_shadow_provenance: bool = True,
) -> None:
    captured_at = datetime(2026, 6, 1, 9, 35) + timedelta(days=index)
    code = f"60{index:04d}"
    market_at = captured_at + timedelta(minutes=1) if source_fault == "future_market" else captured_at
    volume_at = captured_at - timedelta(minutes=16) if source_fault == "stale_volume" else captured_at
    market_id = 10_000 + index
    expectation_id = 20_000 + index
    volume_id = 30_000 + index
    quote_payload = {
        "price": 10,
        "provider_event_at": captured_at.isoformat(),
        "note": "实时行情",
    }
    market_payload = {
        "id": market_id,
        "trade_date": captured_at.date().isoformat(),
        "captured_at": market_at.isoformat(),
        "data_quality": "realtime",
        "regime_code": "NEUTRAL_DIVERGENCE",
    }
    expectation_payload = {
        "id": expectation_id,
        "trade_date": captured_at.date().isoformat(),
        "created_at": captured_at.isoformat(),
        "expectation_gap_score": -8 if gap_band == "weak" else 2,
        "expectation_result": gap_band,
    }
    volume_payload = {
        "id": volume_id,
        "trade_date": captured_at.date().isoformat(),
        "captured_at": volume_at.isoformat(),
        "data_quality": "realtime",
        "pattern": "VWAP_CONFIRMED",
    }
    versions = {
        "market_regime_snapshot_id": market_id,
        "market_captured_at": market_at.isoformat(),
        "expectation_snapshot_id": expectation_id,
        "expectation_captured_at": captured_at.isoformat(),
        "volume_price_snapshot_id": volume_id,
        "volume_price_captured_at": volume_at.isoformat(),
        "position_execution_state_id": None,
        "position_execution_updated_at": None,
    }
    snapshot = SimulationEvidenceSnapshot(
        account_id=account.id,
        code=code,
        name=f"样本{index}",
        strategy_source="expectation_volume_price",
        trade_date=captured_at.date().isoformat(),
        version=1,
        captured_at=captured_at,
        quote_time=captured_at,
        data_quality=quality,
        market_regime="NEUTRAL_DIVERGENCE",
        expectation_gap_score=-8 if gap_band == "weak" else 2,
        expectation_gap_band=gap_band,
        volume_price_state="VWAP_CONFIRMED",
        sector_state="FLOW_CONFIRMED",
        content_hash=f"{index:064x}",
        quote_json=json.dumps(quote_payload),
        market_json=json.dumps(market_payload),
        expectation_json=json.dumps(expectation_payload),
        volume_price_json="{}" if source_fault == "missing_volume" else json.dumps(volume_payload),
        source_versions_json=json.dumps(versions),
    )
    db_session.add(snapshot)
    db_session.flush()
    entry_price = 10.0
    exit_price = entry_price * (1 + return_pct / 100)
    entry_order_id = 1000 + index
    db_session.add(SimulationClosedTrade(
        account_id=account.id,
        lot_id=index + 1,
        code=code,
        name=f"样本{index}",
        strategy_source="expectation_volume_price",
        entry_decision_evidence_snapshot_id=snapshot.id,
        entry_order_id=entry_order_id,
        entry_fill_id=2000 + index,
        closing_order_id=3000 + index,
        closing_fill_id=4000 + index,
        quantity=100,
        entry_average_price=entry_price,
        exit_average_price=exit_price,
        entry_gross_amount=entry_price * 100,
        exit_gross_amount=exit_price * 100,
        total_costs=0,
        realized_pnl=(exit_price - entry_price) * 100,
        return_pct=return_pct,
        opened_at=captured_at + timedelta(minutes=1),
        closed_at=captured_at + timedelta(days=1),
        holding_days=1,
    ))
    if include_shadow_provenance:
        db_session.add(SimulationShadowDecision(
            account_id=account.id,
            signal_key=f"test-shadow-{index}",
            strategy_source="expectation_volume_price",
            source_kind="expectation_volume_pair",
            source_id=expectation_id,
            rule_version="shadow-v1",
            source_version=f"e{expectation_id}:v{volume_id}",
            trade_date=captured_at.date().isoformat(),
            source_at=captured_at,
            evaluated_at=captured_at,
            code=code,
            name=f"样本{index}",
            intent="ENTER",
            side="BUY",
            quantity=100,
            status="ORDER_CREATED",
            reason="预期和量价共振",
            order_id=entry_order_id,
            evidence_json=json.dumps(["预期差确认", "量价确认"]),
        ))


def test_simulation_calibration_refuses_small_or_untraceable_samples(client, db_session):
    account = _account(db_session)
    for index in range(8):
        _closed_trade(
            db_session,
            account,
            index=index,
            gap_band="weak",
            return_pct=-1,
            quality="missing" if index < 3 else "realtime",
        )
    db_session.commit()

    response = client.get(f"/api/simulation/accounts/{account.id}/calibration-proposal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "SAMPLE_INSUFFICIENT"
    assert payload["eligible"] is False
    assert payload["usable_sample_count"] == 5
    assert payload["excluded_sample_count"] == 3
    assert payload["auto_apply_allowed"] is False
    assert payload["candidates"] == []


def test_simulation_calibration_builds_review_candidate_from_forward_slices(client, db_session):
    account = _account(db_session)
    for index in range(40):
        weak = index < 20
        _closed_trade(
            db_session,
            account,
            index=index,
            gap_band="weak" if weak else "matched",
            return_pct=-2.0 if weak else 1.0,
        )
    db_session.commit()

    response = client.get(f"/api/simulation/accounts/{account.id}/calibration-proposal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "READY_FOR_REVIEW"
    assert payload["eligible"] is True
    assert payload["usable_sample_count"] == 40
    assert payload["requires_manual_confirmation"] is True
    assert payload["auto_apply_allowed"] is False
    fields = {item["field"] for item in payload["candidates"]}
    assert "entry_confirmation_gate" in fields
    assert "negative_expectation_gap_gate" in fields
    assert payload["by_expectation_gap"]


def test_simulation_calibration_rejects_missing_stale_and_future_sources(client, db_session):
    account = _account(db_session)
    faults = ["missing_volume", "stale_volume", "future_market"]
    for index, fault in enumerate(faults):
        _closed_trade(
            db_session,
            account,
            index=index,
            gap_band="weak",
            return_pct=-1,
            source_fault=fault,
        )
    db_session.commit()

    payload = client.get(
        f"/api/simulation/accounts/{account.id}/calibration-proposal"
    ).json()

    assert payload["usable_sample_count"] == 0
    assert payload["excluded_sample_count"] == 3
    reasons = " ".join(payload["exclusion_reasons"])
    assert "volume_price_json缺失或损坏" in reasons
    assert "volume_price_json证据已陈旧" in reasons
    assert "market_json使用了未来证据" in reasons


def test_manual_account_is_statistics_only_and_never_builds_rule_candidate(client, db_session):
    account = _account(db_session, manual=True)
    for index in range(40):
        _closed_trade(
            db_session,
            account,
            index=index,
            gap_band="weak" if index < 20 else "matched",
            return_pct=-2 if index < 20 else 1,
            include_shadow_provenance=False,
        )
    db_session.commit()

    payload = client.get(
        f"/api/simulation/accounts/{account.id}/calibration-proposal"
    ).json()

    assert payload["status"] == "MANUAL_STATISTICS_ONLY"
    assert payload["candidate_generation_allowed"] is False
    assert payload["statistics_only"] is True
    assert payload["statistical_sample_count"] == 40
    assert payload["overall"]["sample_count"] == 40
    assert payload["usable_sample_count"] == 0
    assert payload["eligible"] is False
    assert payload["candidates"] == []


def test_shadow_account_rejects_manual_order_without_automation_provenance(client, db_session):
    account = _account(db_session)
    _closed_trade(
        db_session,
        account,
        index=0,
        gap_band="matched",
        return_pct=1,
        include_shadow_provenance=False,
    )
    db_session.commit()

    payload = client.get(
        f"/api/simulation/accounts/{account.id}/calibration-proposal"
    ).json()

    assert payload["usable_sample_count"] == 0
    assert payload["candidate_generation_allowed"] is True
    assert any("没有自动影子决策来源" in item for item in payload["exclusion_reasons"])
