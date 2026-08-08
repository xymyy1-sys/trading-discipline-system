from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    SimulationAccount,
    SimulationClosedTrade,
    SimulationEvidenceSnapshot,
    SimulationRuleRelease,
    SimulationShadowDecision,
)
from app.services.simulation_calibration import (
    _shadow_provenance_reason,
    _snapshot_evidence_reason,
    simulation_calibration_proposal,
)


DEFAULT_RULE_PARAMETERS: dict[str, Any] = {
    "entry_price_vs_vwap_min": 0.10,
    "entry_price_vs_vwap_max": 2.00,
    "entry_volume_acceleration_min": -35.0,
    "entry_buy_sell_ratio_min": 1.05,
    "blocked_market_regimes": [],
    "default_risk_budget_ratio": 0.03,
}
MINIMUM_FORWARD_CONTROL = 30
MINIMUM_FORWARD_CANDIDATE = 30


def _loads(raw: str, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback
    return value


def active_rule_parameters(db: Session, account_id: int) -> tuple[str, dict[str, Any]]:
    row = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.account_id == account_id,
        SimulationRuleRelease.status == "ACTIVE",
    ).order_by(SimulationRuleRelease.activated_at.desc(), SimulationRuleRelease.id.desc()).first()
    if row is None:
        return "shadow-v5-multi-entry", dict(DEFAULT_RULE_PARAMETERS)
    return row.rule_version, {**DEFAULT_RULE_PARAMETERS, **_loads(row.parameters_json, {})}


def approve_rule_release(
    db: Session,
    account: SimulationAccount,
    release_id: int,
) -> SimulationRuleRelease:
    """Activate a validated paper-trading rule only after explicit approval."""
    row = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.id == release_id,
        SimulationRuleRelease.account_id == account.id,
    ).first()
    if row is None:
        raise ValueError("规则候选不存在")
    if row.status != "READY_FOR_APPROVAL":
        raise ValueError("只有已通过前向验证的规则才能确认启用")
    previous = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.account_id == account.id,
        SimulationRuleRelease.status == "ACTIVE",
    ).all()
    for active in previous:
        active.status = "SUPERSEDED"
        active.rolled_back_at = shanghai_now_naive()
        active.rollback_reason = f"由人工确认的新版本{row.rule_version}替代"
        db.add(active)
    row.status = "ACTIVE"
    row.activated_at = shanghai_now_naive()
    row.activation_closed_trade_id = (
        db.query(SimulationClosedTrade.id).filter(
            SimulationClosedTrade.account_id == account.id,
        ).order_by(SimulationClosedTrade.id.desc()).scalar() or row.baseline_closed_trade_id
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _candidate_parameters(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    params = dict(DEFAULT_RULE_PARAMETERS)
    for item in candidates:
        field = str(item.get("field") or "")
        if field == "entry_confirmation_gate":
            params.update({
                "entry_price_vs_vwap_min": 0.20,
                "entry_price_vs_vwap_max": 1.50,
                "entry_volume_acceleration_min": -20.0,
                "entry_buy_sell_ratio_min": 1.10,
            })
        elif field == "risk_regime_position_gate":
            params["blocked_market_regimes"] = ["EXTREME_SHRINK_DECLINE", "VOLUME_SELL_OFF"]
        elif field == "total_risk_budget":
            params["default_risk_budget_ratio"] = 0.02
        elif field == "negative_expectation_gap_gate":
            params["block_negative_expectation_entry"] = True
    return params


def _metrics(rows: list[SimulationClosedTrade]) -> dict[str, float | int]:
    returns = [float(row.return_pct or 0) for row in rows]
    pnls = [float(row.realized_pnl or 0) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return {
        "sample_count": len(rows),
        "win_rate": round(len(wins) / len(rows) * 100, 2) if rows else 0.0,
        "average_return_pct": round(sum(returns) / len(rows), 4) if rows else 0.0,
        "profit_loss_ratio": round(avg_win / abs(avg_loss), 4) if avg_win > 0 and avg_loss < 0 else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "pnl_drawdown": round(drawdown, 2),
    }


def _passes(snapshot: SimulationEvidenceSnapshot | None, params: dict[str, Any]) -> bool:
    if snapshot is None:
        return False
    if snapshot.market_regime in set(params.get("blocked_market_regimes") or []):
        return False
    if params.get("block_negative_expectation_entry") and int(snapshot.expectation_gap_score or 0) < 0:
        return False
    volume = _loads(snapshot.volume_price_json, {})
    try:
        deviation = float(volume.get("price_vs_vwap") or 0)
        acceleration = float(volume.get("volume_acceleration") or 0)
        buy = float(volume.get("active_buy_amount") or 0)
        sell = float(volume.get("active_sell_amount") or 0)
    except (TypeError, ValueError):
        return False
    if not float(params["entry_price_vs_vwap_min"]) <= deviation <= float(params["entry_price_vs_vwap_max"]):
        return False
    if acceleration < float(params["entry_volume_acceleration_min"]):
        return False
    if buy <= 0 or sell <= 0 or buy < sell * float(params["entry_buy_sell_ratio_min"]):
        return False
    return True


def advance_rule_promotion(db: Session, account: SimulationAccount) -> SimulationRuleRelease | None:
    """Create, forward-test, promote, and if necessary roll back safe rules.

    Only tightening candidates are eligible.  The candidate is evaluated on
    trades closed strictly after creation, so training rows can never leak
    into its forward score.
    """
    proposal = simulation_calibration_proposal(db, account)
    active = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.account_id == account.id,
        SimulationRuleRelease.status == "ACTIVE",
    ).order_by(SimulationRuleRelease.id.desc()).first()
    training = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.account_id == account.id,
        SimulationRuleRelease.status == "TRAINING",
    ).order_by(SimulationRuleRelease.id.desc()).first()
    awaiting_approval = db.query(SimulationRuleRelease).filter(
        SimulationRuleRelease.account_id == account.id,
        SimulationRuleRelease.status == "READY_FOR_APPROVAL",
    ).order_by(SimulationRuleRelease.id.desc()).first()
    if training is None and awaiting_approval is None and active is None and proposal.get("status") == "READY_FOR_REVIEW":
        candidates = list(proposal.get("candidates") or [])
        params = _candidate_parameters(candidates)
        encoded = json.dumps(params, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        latest_id = db.query(SimulationClosedTrade.id).filter(
            SimulationClosedTrade.account_id == account.id,
        ).order_by(SimulationClosedTrade.id.desc()).scalar() or 0
        training = SimulationRuleRelease(
            account_id=account.id,
            rule_version=f"shadow-auto-{digest[:10]}",
            baseline_rule_version="shadow-v5-multi-entry",
            candidate_hash=digest,
            status="TRAINING",
            parameters_json=encoded,
            rationale_json=json.dumps(candidates, ensure_ascii=False),
            baseline_closed_trade_id=int(latest_id),
            created_at=shanghai_now_naive(),
        )
        db.add(training)
        db.commit()
        db.refresh(training)
        return training
    if training is not None:
        rows = db.query(SimulationClosedTrade).filter(
            SimulationClosedTrade.account_id == account.id,
            SimulationClosedTrade.id > training.baseline_closed_trade_id,
        ).order_by(SimulationClosedTrade.id.asc()).all()
        snapshots = {
            row.id: row for row in db.query(SimulationEvidenceSnapshot).filter(
                SimulationEvidenceSnapshot.id.in_([
                    int(item.entry_decision_evidence_snapshot_id or 0) for item in rows
                ] or [0])
            ).all()
        }
        decisions = {
            int(row.order_id): row
            for row in db.query(SimulationShadowDecision).filter(
                SimulationShadowDecision.account_id == account.id,
                SimulationShadowDecision.order_id.in_([
                    int(item.entry_order_id or 0) for item in rows
                ] or [0]),
            ).order_by(SimulationShadowDecision.id.desc()).all()
            if row.order_id is not None
        }
        formal_rows: list[SimulationClosedTrade] = []
        for row in rows:
            snapshot = snapshots.get(int(row.entry_decision_evidence_snapshot_id or 0))
            if _snapshot_evidence_reason(row, snapshot):
                continue
            assert snapshot is not None
            if _shadow_provenance_reason(
                row,
                snapshot,
                decisions.get(int(row.entry_order_id or 0)),
            ):
                continue
            formal_rows.append(row)
        params = {**DEFAULT_RULE_PARAMETERS, **_loads(training.parameters_json, {})}
        candidate_rows = [
            row for row in formal_rows
            if _passes(snapshots.get(int(row.entry_decision_evidence_snapshot_id or 0)), params)
        ]
        control_metrics = _metrics(formal_rows)
        candidate_metrics = _metrics(candidate_rows)
        training.forward_control_samples = len(formal_rows)
        training.forward_candidate_samples = len(candidate_rows)
        training.control_metrics_json = json.dumps(control_metrics, ensure_ascii=False)
        training.candidate_metrics_json = json.dumps(candidate_metrics, ensure_ascii=False)
        if len(formal_rows) >= MINIMUM_FORWARD_CONTROL and len(candidate_rows) >= MINIMUM_FORWARD_CANDIDATE:
            improves = bool(
                float(candidate_metrics["average_return_pct"]) >= float(control_metrics["average_return_pct"]) + 0.30
                and float(candidate_metrics["win_rate"]) >= float(control_metrics["win_rate"])
                and float(candidate_metrics["profit_loss_ratio"]) >= max(1.0, float(control_metrics["profit_loss_ratio"]))
                and float(candidate_metrics["pnl_drawdown"]) <= float(control_metrics["pnl_drawdown"])
            )
            training.validated_at = shanghai_now_naive()
            training.status = "READY_FOR_APPROVAL" if improves else "REJECTED"
            if improves:
                training.rollback_reason = "前向验证通过，等待用户人工确认后启用"
            else:
                training.rollback_reason = "前向样本未同时改善期望收益、胜率、盈亏比和回撤"
        db.add(training)
        db.commit()
        db.refresh(training)
        return training
    if active is not None:
        post = db.query(SimulationClosedTrade).filter(
            SimulationClosedTrade.account_id == account.id,
            SimulationClosedTrade.id > active.activation_closed_trade_id,
        ).order_by(SimulationClosedTrade.id.asc()).all()
        metrics = _metrics(post)
        if len(post) >= MINIMUM_FORWARD_CONTROL and (
            float(metrics["average_return_pct"]) <= -0.50
            or float(metrics["pnl_drawdown"]) >= max(float(account.initial_cash or 0) * 0.06, 1)
        ):
            active.status = "ROLLED_BACK"
            active.rolled_back_at = shanghai_now_naive()
            active.rollback_reason = "晋级后前向收益转负或回撤超过账户6%，自动回滚到基线规则"
            db.add(active)
            db.commit()
            db.refresh(active)
        return active
    return None
