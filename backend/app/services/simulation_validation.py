from __future__ import annotations

from collections import Counter
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    SimulationAccount,
    SimulationClosedTrade,
    SimulationEvidenceSnapshot,
    SimulationShadowDecision,
)
from app.services.simulation_calibration import (
    _snapshot_evidence_reason,
    _shadow_provenance_reason,
)


MINIMUM_TRAIN_SAMPLES = 20
TEST_FOLD_SIZE = 5
EXPLORATION_SOURCE_KINDS = {"autonomous_exploration_sample"}


def _metrics(rows: list[SimulationClosedTrade]) -> dict[str, Any]:
    pnls = [float(row.realized_pnl or 0) for row in rows]
    returns = [float(row.return_pct or 0) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "sample_count": len(rows),
        "win_rate": round(len(wins) / len(rows) * 100, 2) if rows else 0.0,
        "average_return_pct": round(sum(returns) / len(returns), 4) if rows else 0.0,
        "median_return_pct": round(float(median(returns)), 4) if rows else 0.0,
        "profit_loss_ratio": round(average_win / abs(average_loss), 4)
        if average_win > 0 and average_loss < 0
        else 0.0,
        "total_realized_pnl": round(sum(pnls), 2),
    }


def build_walk_forward_folds(
    rows: list[SimulationClosedTrade],
    *,
    minimum_train_samples: int = MINIMUM_TRAIN_SAMPLES,
    test_fold_size: int = TEST_FOLD_SIZE,
) -> list[dict[str, Any]]:
    """Evaluate only trades strictly after each expanding training window."""
    ordered = sorted(rows, key=lambda row: (row.closed_at, row.id or 0))
    folds: list[dict[str, Any]] = []
    start = max(minimum_train_samples, 1)
    size = max(test_fold_size, 1)
    fold_number = 1
    for test_start in range(start, len(ordered), size):
        training = ordered[:test_start]
        testing = ordered[test_start:test_start + size]
        if not testing:
            break
        folds.append({
            "fold": fold_number,
            "training_end_at": training[-1].closed_at,
            "test_start_at": testing[0].opened_at,
            "test_end_at": testing[-1].closed_at,
            "training": _metrics(training),
            "out_of_sample": _metrics(testing),
        })
        fold_number += 1
    return folds


def validation_report(db: Session, account: SimulationAccount) -> dict[str, Any]:
    closed = db.query(SimulationClosedTrade).filter(
        SimulationClosedTrade.account_id == account.id,
    ).order_by(SimulationClosedTrade.closed_at.asc(), SimulationClosedTrade.id.asc()).all()
    snapshot_ids = [
        int(row.entry_decision_evidence_snapshot_id)
        for row in closed if row.entry_decision_evidence_snapshot_id is not None
    ]
    snapshots = {
        int(row.id): row
        for row in db.query(SimulationEvidenceSnapshot).filter(
            SimulationEvidenceSnapshot.account_id == account.id,
            SimulationEvidenceSnapshot.id.in_(snapshot_ids or [0]),
        ).all()
    }
    order_ids = [int(row.entry_order_id) for row in closed if row.entry_order_id]
    decisions = {
        int(row.order_id): row
        for row in db.query(SimulationShadowDecision).filter(
            SimulationShadowDecision.account_id == account.id,
            SimulationShadowDecision.order_id.in_(order_ids or [0]),
        ).order_by(SimulationShadowDecision.id.desc()).all()
        if row.order_id is not None
    }
    formal: list[SimulationClosedTrade] = []
    exploration: list[SimulationClosedTrade] = []
    exclusions: Counter[str] = Counter()
    for row in closed:
        snapshot = snapshots.get(int(row.entry_decision_evidence_snapshot_id or 0))
        reason = _snapshot_evidence_reason(row, snapshot)
        if reason:
            exclusions[reason] += 1
            continue
        assert snapshot is not None
        decision = decisions.get(int(row.entry_order_id or 0))
        if decision is not None and decision.source_kind in EXPLORATION_SOURCE_KINDS:
            exploration.append(row)
            continue
        reason = _shadow_provenance_reason(row, snapshot, decision)
        if reason:
            exclusions[reason] += 1
            continue
        formal.append(row)
    folds = build_walk_forward_folds(formal)
    out_of_sample = [
        row
        for index, row in enumerate(sorted(formal, key=lambda item: (item.closed_at, item.id or 0)))
        if index >= MINIMUM_TRAIN_SAMPLES
    ]
    status = "ready" if folds and len(out_of_sample) >= TEST_FOLD_SIZE else "insufficient_samples"
    return {
        "account_id": account.id,
        "generated_at": shanghai_now_naive(),
        "status": status,
        "minimum_train_samples": MINIMUM_TRAIN_SAMPLES,
        "test_fold_size": TEST_FOLD_SIZE,
        "total_closed_samples": len(closed),
        "formal_point_in_time_samples": len(formal),
        "exploration_samples": len(exploration),
        "excluded_samples": sum(exclusions.values()),
        "exclusion_reasons": [f"{reason}（{count}笔）" for reason, count in exclusions.most_common(8)],
        "formal_overall": _metrics(formal),
        "out_of_sample_overall": _metrics(out_of_sample),
        "folds": folds,
        "limitations": [
            "只纳入当时已冻结且早于成交时点的真实证据；缺失历史数据不会用今日数据回填。",
            "探索交易单独统计，不进入正式策略训练与样本外成绩。",
            "滚动验证采用扩展训练窗和后续测试窗；测试窗结果不会反向改写此前信号。",
        ],
    }
