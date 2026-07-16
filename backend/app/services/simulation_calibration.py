from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
from statistics import median
from typing import Any

from sqlalchemy.orm import Session

from app.models.trading import (
    SimulationAccount,
    SimulationClosedTrade,
    SimulationDailyEquity,
    SimulationEvidenceSnapshot,
    SimulationShadowDecision,
)
from app.core.trading_clock import shanghai_now_naive


MINIMUM_TOTAL_SAMPLES = 30
MINIMUM_SLICE_SAMPLES = 10
MINIMUM_STRATEGY_SAMPLES = 20
_USABLE_QUALITY = {"realtime", "real", "live", "真实", "实时"}
_MARKET_USABLE_QUALITY = _USABLE_QUALITY | {"complete", "完整"}
_AUTOMATED_ACCOUNT_TYPES = {"shadow", "automation", "automated"}

# A calibration sample is stricter than a screen display.  Every strategy has
# an explicit evidence contract.  The values are (frozen JSON field, source id
# key, source time key, time field inside the JSON, maximum age in seconds).
_SOURCE_REQUIREMENTS: dict[str, tuple[tuple[str, str, str, str, int], ...]] = {
    "expectation_volume_price": (
        ("market_json", "market_regime_snapshot_id", "market_captured_at", "captured_at", 15 * 60),
        ("expectation_json", "expectation_snapshot_id", "expectation_captured_at", "created_at", 36 * 60 * 60),
        ("volume_price_json", "volume_price_snapshot_id", "volume_price_captured_at", "captured_at", 15 * 60),
    ),
    "holding_execution": (
        ("market_json", "market_regime_snapshot_id", "market_captured_at", "captured_at", 15 * 60),
        ("expectation_json", "expectation_snapshot_id", "expectation_captured_at", "created_at", 36 * 60 * 60),
        ("volume_price_json", "volume_price_snapshot_id", "volume_price_captured_at", "captured_at", 15 * 60),
        ("sector_json", "position_execution_state_id", "position_execution_updated_at", "updated_at", 15 * 60),
    ),
    "limit_up": (
        ("market_json", "market_regime_snapshot_id", "market_captured_at", "captured_at", 15 * 60),
        ("volume_price_json", "volume_price_snapshot_id", "volume_price_captured_at", "captured_at", 15 * 60),
    ),
}
_SHADOW_SOURCE_KIND = {
    "expectation_volume_price": "expectation_volume_pair",
    "holding_execution": "position_execution_state",
    "limit_up": "limit_up_plan_confirmation",
}


def _quality_is_usable(value: str | None) -> bool:
    # Calibration is deliberately a whitelist.  New provider states such as
    # ``stale``/``future``/``degraded`` must not silently become training
    # samples merely because they were not known when this code was written.
    return str(value or "").strip().lower() in _USABLE_QUALITY


def _json_dict(raw: str | None) -> dict[str, Any] | None:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) and value else None


def _json_list(raw: str | None) -> list[Any] | None:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) and value else None


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return shanghai_now_naive(value)
    if value in (None, ""):
        return None
    try:
        return shanghai_now_naive(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _normalized_code(value: str | None) -> str:
    digits = "".join(character for character in str(value or "") if character.isdigit())
    return digits[-6:].zfill(6) if digits else ""


def _source_payload_quality_ok(field: str, payload: dict[str, Any]) -> bool:
    quality = str(payload.get("data_quality") or "").strip().lower()
    if field == "expectation_json":
        return True
    if field == "market_json":
        return quality in _MARKET_USABLE_QUALITY
    return quality in _USABLE_QUALITY


def _snapshot_evidence_reason(
    row: SimulationClosedTrade,
    snapshot: SimulationEvidenceSnapshot | None,
) -> str | None:
    """Return the first point-in-time evidence contract violation, if any."""

    if snapshot is None:
        return "缺少入场决策证据快照"
    if snapshot.account_id != row.account_id:
        return "证据快照账户不匹配"
    if str(snapshot.strategy_source or "") != str(row.strategy_source or ""):
        return "证据快照策略来源不匹配"
    if _normalized_code(snapshot.code) != _normalized_code(row.code):
        return "证据快照标的不匹配"
    if not _quality_is_usable(snapshot.data_quality):
        return "入场行情质量不合格"

    decision_at = _as_datetime(snapshot.captured_at)
    quote_at = _as_datetime(snapshot.quote_time)
    opened_at = _as_datetime(row.opened_at)
    if decision_at is None:
        return "入场决策缺少生成时间"
    if opened_at is None or decision_at > opened_at:
        return "入场决策晚于模拟成交"
    if quote_at is None:
        return "入场行情缺少交易所时间"
    quote_age = (decision_at - quote_at).total_seconds()
    if quote_age < 0:
        return "入场行情来自未来"
    if quote_age > 2 * 60:
        return "入场行情已陈旧"

    quote_payload = _json_dict(snapshot.quote_json)
    if quote_payload is None:
        return "入场行情JSON缺失或损坏"
    payload_quote_at = _as_datetime(quote_payload.get("provider_event_at"))
    if payload_quote_at is None or abs((payload_quote_at - quote_at).total_seconds()) > 1:
        return "入场行情JSON与冻结时间版本不一致"
    try:
        price = float(quote_payload.get("price") or 0)
    except (TypeError, ValueError):
        price = 0
    if price <= 0:
        return "入场行情JSON缺少有效价格"

    versions = _json_dict(snapshot.source_versions_json)
    if versions is None:
        return "来源版本JSON缺失或损坏"
    strategy = str(row.strategy_source or "")
    requirements = _SOURCE_REQUIREMENTS.get(strategy)
    if requirements is None:
        return "策略没有校准证据契约"
    for field, id_key, time_key, payload_time_key, max_age in requirements:
        payload = _json_dict(getattr(snapshot, field, None))
        if payload is None:
            return f"{field}缺失或损坏"
        source_id = versions.get(id_key)
        try:
            valid_source_id = int(source_id or 0) > 0
        except (TypeError, ValueError):
            valid_source_id = False
        if not valid_source_id or str(payload.get("id") or "") != str(source_id):
            return f"{field}来源ID缺失或版本不一致"
        source_at = _as_datetime(versions.get(time_key))
        payload_at = _as_datetime(payload.get(payload_time_key))
        if source_at is None or payload_at is None:
            return f"{field}缺少可审计时间"
        if abs((source_at - payload_at).total_seconds()) > 1:
            return f"{field}来源时间与冻结JSON不一致"
        age = (decision_at - source_at).total_seconds()
        if age < 0:
            return f"{field}使用了未来证据"
        if age > max_age:
            return f"{field}证据已陈旧"
        if field != "expectation_json" and str(payload.get("trade_date") or "") != snapshot.trade_date:
            return f"{field}不是决策当日证据"
        if not _source_payload_quality_ok(field, payload):
            return f"{field}数据质量不合格"
    return None


def _shadow_provenance_reason(
    row: SimulationClosedTrade,
    snapshot: SimulationEvidenceSnapshot,
    decision: SimulationShadowDecision | None,
) -> str | None:
    if decision is None:
        return "入场委托没有自动影子决策来源"
    if decision.account_id != row.account_id or decision.order_id != row.entry_order_id:
        return "自动影子决策与入场委托不匹配"
    if decision.status != "ORDER_CREATED":
        return "自动影子决策状态不能证明曾生成入场委托"
    strategy = str(row.strategy_source or "")
    if decision.strategy_source != strategy:
        return "自动影子策略来源不匹配"
    if decision.source_kind != _SHADOW_SOURCE_KIND.get(strategy):
        return "自动影子信号类型与策略不匹配"
    if _normalized_code(decision.code) != _normalized_code(row.code):
        return "自动影子决策标的不匹配"
    if not decision.rule_version or not decision.source_version or not int(decision.source_id or 0):
        return "自动影子决策缺少规则或来源版本"
    if _json_list(decision.evidence_json) is None:
        return "自动影子决策证据JSON缺失或损坏"

    decision_at = _as_datetime(snapshot.captured_at)
    evaluated_at = _as_datetime(decision.evaluated_at)
    source_at = _as_datetime(decision.source_at)
    if decision_at is None or evaluated_at is None or source_at is None:
        return "自动影子决策缺少可审计时间"
    if source_at > evaluated_at or evaluated_at > decision_at:
        return "自动影子决策使用了未来证据"
    if (decision_at - evaluated_at).total_seconds() > 30:
        return "自动影子决策与冻结快照不是同一决策时点"
    source_age = (evaluated_at - source_at).total_seconds()
    max_source_age = 2 * 60 if strategy == "limit_up" else 15 * 60
    if source_age > max_source_age:
        return "自动影子信号来源已陈旧"
    if decision.trade_date != snapshot.trade_date:
        return "自动影子决策交易日不匹配"
    return None


def _metrics(rows: list[SimulationClosedTrade]) -> dict[str, Any]:
    returns = [float(row.return_pct or 0) for row in rows]
    pnls = [float(row.realized_pnl or 0) for row in rows]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    average_win = sum(wins) / len(wins) if wins else 0.0
    average_loss = sum(losses) / len(losses) if losses else 0.0
    return {
        "sample_count": len(rows),
        "win_rate": round(len(wins) / len(rows) * 100, 2) if rows else 0.0,
        "average_return_pct": round(sum(returns) / len(returns), 4) if returns else 0.0,
        "median_return_pct": round(float(median(returns)), 4) if returns else 0.0,
        "profit_loss_ratio": round(average_win / abs(average_loss), 4)
        if average_win > 0 and average_loss < 0
        else 0.0,
        "total_realized_pnl": round(sum(pnls), 2),
    }


def _gap_family(value: str | None) -> str:
    normalized = str(value or "unknown").strip().lower()
    if any(token in normalized for token in ("weak", "invalid", "negative", "below", "弱", "证伪", "负")):
        return "弱于预期"
    if any(token in normalized for token in ("strong", "positive", "above", "强", "超预期")):
        return "强于预期"
    if any(token in normalized for token in ("match", "neutral", "符合", "中性")):
        return "符合预期"
    return "数据未分类"


def _candidate(
    *,
    target: str,
    field: str,
    direction: str,
    suggestion: str,
    reason: str,
    sample_count: int,
    support_metric: str,
) -> dict[str, Any]:
    return {
        "target": target,
        "field": field,
        "direction": direction,
        "suggestion": suggestion,
        "reason": reason,
        "sample_count": sample_count,
        "support_metric": support_metric,
    }


def simulation_calibration_proposal(
    db: Session,
    account: SimulationAccount,
) -> dict[str, Any]:
    """Build a guarded calibration candidate from forward-only paper trades.

    This function deliberately does not mutate live rules.  It only admits
    completed round trips whose entry decision can be traced to a usable,
    point-in-time evidence snapshot.  The resulting candidate must still be
    reviewed through the existing calibration/apply/rollback workflow.
    """

    closed = (
        db.query(SimulationClosedTrade)
        .filter(SimulationClosedTrade.account_id == account.id)
        .order_by(SimulationClosedTrade.closed_at.asc(), SimulationClosedTrade.id.asc())
        .all()
    )
    evidence_ids = [
        int(row.entry_decision_evidence_snapshot_id)
        for row in closed
        if row.entry_decision_evidence_snapshot_id is not None
    ]
    snapshots = {
        int(row.id): row
        for row in (
            db.query(SimulationEvidenceSnapshot)
            .filter(
                SimulationEvidenceSnapshot.account_id == account.id,
                SimulationEvidenceSnapshot.id.in_(evidence_ids or [0]),
            )
            .all()
        )
    }
    entry_order_ids = [int(row.entry_order_id) for row in closed if row.entry_order_id]
    shadow_decisions = {
        int(row.order_id): row
        for row in (
            db.query(SimulationShadowDecision)
            .filter(
                SimulationShadowDecision.account_id == account.id,
                SimulationShadowDecision.order_id.in_(entry_order_ids or [0]),
            )
            .order_by(SimulationShadowDecision.id.desc())
            .all()
        )
        if row.order_id is not None
    }
    candidate_generation_allowed = bool(
        str(account.account_type or "").strip().lower() in _AUTOMATED_ACCOUNT_TYPES
        and str(account.automation_key or "").strip()
    )
    usable: list[SimulationClosedTrade] = []
    exclusion_counts: dict[str, int] = defaultdict(int)
    by_strategy: dict[str, list[SimulationClosedTrade]] = defaultdict(list)
    by_regime: dict[str, list[SimulationClosedTrade]] = defaultdict(list)
    by_gap: dict[str, list[SimulationClosedTrade]] = defaultdict(list)
    for row in closed:
        snapshot = snapshots.get(int(row.entry_decision_evidence_snapshot_id or 0))
        evidence_reason = _snapshot_evidence_reason(row, snapshot)
        if evidence_reason is not None:
            exclusion_counts[evidence_reason] += 1
            continue
        assert snapshot is not None
        if not candidate_generation_allowed:
            exclusion_counts["手工账户仅展示统计，不进入自动规则校准"] += 1
            continue
        provenance_reason = _shadow_provenance_reason(
            row,
            snapshot,
            shadow_decisions.get(int(row.entry_order_id or 0)),
        )
        if provenance_reason is not None:
            exclusion_counts[provenance_reason] += 1
            continue
        usable.append(row)
        by_strategy[str(row.strategy_source or "unknown")].append(row)
        by_regime[str(snapshot.market_regime or "UNKNOWN")].append(row)
        by_gap[_gap_family(snapshot.expectation_gap_band)].append(row)

    # Manual accounts remain useful as a diary, so their closed trades are
    # reported descriptively.  They never enter candidate slices or produce a
    # machine-rule recommendation.
    overall = _metrics(usable if candidate_generation_allowed else closed)
    if not candidate_generation_allowed:
        by_strategy = defaultdict(list)
        for row in closed:
            by_strategy[str(row.strategy_source or "unknown")].append(row)
    candidates: list[dict[str, Any]] = []

    if len(usable) >= MINIMUM_TOTAL_SAMPLES:
        for strategy, rows in sorted(by_strategy.items()):
            metric = _metrics(rows)
            if (
                metric["sample_count"] >= MINIMUM_STRATEGY_SAMPLES
                and metric["average_return_pct"] <= 0
                and (metric["win_rate"] < 45 or metric["profit_loss_ratio"] < 1)
            ):
                candidates.append(_candidate(
                    target=strategy,
                    field="entry_confirmation_gate",
                    direction="tighten",
                    suggestion="提高入场确认门槛，并在下一轮影子实验中保留原规则作为对照组。",
                    reason="该策略已有足量前向闭环样本，但平均收益不为正，且胜率或盈亏比未过门槛。",
                    sample_count=metric["sample_count"],
                    support_metric=(
                        f"胜率 {metric['win_rate']:.2f}% · 盈亏比 {metric['profit_loss_ratio']:.2f} · "
                        f"平均收益 {metric['average_return_pct']:+.2f}%"
                    ),
                ))

        weak = _metrics(by_gap.get("弱于预期", []))
        matched_rows = by_gap.get("符合预期", []) + by_gap.get("强于预期", [])
        matched = _metrics(matched_rows)
        if (
            weak["sample_count"] >= MINIMUM_SLICE_SAMPLES
            and matched["sample_count"] >= MINIMUM_SLICE_SAMPLES
            and weak["average_return_pct"] + 1.0 < matched["average_return_pct"]
        ):
            candidates.append(_candidate(
                target="预期×量价",
                field="negative_expectation_gap_gate",
                direction="tighten",
                suggestion="负预期差默认禁止新增模拟仓位；只有量价修复与板块共振同时确认时才进入下一轮实验。",
                reason="弱于预期组的前向收益显著落后于符合/强于预期组。",
                sample_count=weak["sample_count"] + matched["sample_count"],
                support_metric=(
                    f"弱预期平均 {weak['average_return_pct']:+.2f}% · "
                    f"对照组平均 {matched['average_return_pct']:+.2f}%"
                ),
            ))

        risk_regimes = []
        normal_regimes = []
        for regime, rows in by_regime.items():
            if regime in {"EXTREME_SHRINK_DECLINE", "VOLUME_SELL_OFF"}:
                risk_regimes.extend(rows)
            else:
                normal_regimes.extend(rows)
        risk = _metrics(risk_regimes)
        normal = _metrics(normal_regimes)
        if (
            risk["sample_count"] >= MINIMUM_SLICE_SAMPLES
            and normal["sample_count"] >= MINIMUM_SLICE_SAMPLES
            and risk["average_return_pct"] + 1.0 < normal["average_return_pct"]
        ):
            candidates.append(_candidate(
                target="市场环境闸门",
                field="risk_regime_position_gate",
                direction="tighten",
                suggestion="极致缩量普跌或放量杀跌时冻结新增影子仓位，只验证减仓、反抽与止损策略。",
                reason="高风险市场组的前向收益显著落后于其他环境。",
                sample_count=risk["sample_count"] + normal["sample_count"],
                support_metric=(
                    f"高风险环境平均 {risk['average_return_pct']:+.2f}% · "
                    f"其他环境平均 {normal['average_return_pct']:+.2f}%"
                ),
            ))

    equities = (
        db.query(SimulationDailyEquity)
        .filter(SimulationDailyEquity.account_id == account.id)
        .order_by(SimulationDailyEquity.trade_date.asc())
        .all()
    )
    maximum_drawdown = abs(min((float(row.drawdown_pct or 0) for row in equities), default=0.0))
    if len(usable) >= MINIMUM_TOTAL_SAMPLES and maximum_drawdown >= 12:
        candidates.append(_candidate(
            target="组合风险",
            field="total_risk_budget",
            direction="tighten",
            suggestion="先降低单笔和同题材风险预算，再继续积累新规则样本。",
            reason="模拟账户最大回撤超过校准观察线，不能只优化胜率而忽略组合尾部风险。",
            sample_count=len(usable),
            support_metric=f"最大回撤 {maximum_drawdown:.2f}%",
        ))

    if not candidate_generation_allowed:
        status = "MANUAL_STATISTICS_ONLY"
        summary = (
            f"手工模拟闭环 {len(closed)} 笔仅用于复盘统计；"
            "自动参数候选只接受带 automation_key 的系统影子账户。"
        )
    elif len(usable) < MINIMUM_TOTAL_SAMPLES:
        status = "SAMPLE_INSUFFICIENT"
        summary = f"可追溯前向闭环样本 {len(usable)}/{MINIMUM_TOTAL_SAMPLES}，继续采样，禁止调参。"
    elif candidates:
        status = "READY_FOR_REVIEW"
        summary = f"形成 {len(candidates)} 项校准候选；必须人工审核并以新旧规则并行影子验证。"
    else:
        status = "NO_CHANGE_RECOMMENDED"
        summary = "样本门槛已满足，但没有出现足以支持调整规则的稳定分层差异。"

    return {
        "account_id": account.id,
        "generated_at": datetime.now(timezone.utc),
        "status": status,
        "eligible": status == "READY_FOR_REVIEW",
        "candidate_generation_allowed": candidate_generation_allowed,
        "statistics_only": not candidate_generation_allowed,
        "minimum_samples": MINIMUM_TOTAL_SAMPLES,
        "statistical_sample_count": len(closed),
        "usable_sample_count": len(usable),
        "excluded_sample_count": len(closed) - len(usable),
        "exclusion_reasons": [
            f"{reason}：{count} 笔" for reason, count in sorted(exclusion_counts.items())
        ],
        "summary": summary,
        "overall": overall,
        "by_strategy": [
            {"key": key, **_metrics(rows)} for key, rows in sorted(by_strategy.items())
        ],
        "by_market_regime": [
            {"key": key, **_metrics(rows)} for key, rows in sorted(by_regime.items())
        ],
        "by_expectation_gap": [
            {"key": key, **_metrics(rows)} for key, rows in sorted(by_gap.items())
        ],
        "maximum_drawdown_pct": round(maximum_drawdown, 4),
        "candidates": candidates,
        "evidence": [
            "只统计完成买入与卖出的前向闭环交易。",
            "自动候选只使用系统影子账户及与入场委托一一对应的自动决策来源。",
            "每笔样本必须有策略所需的市场、预期、量价或执行证据，且来源版本完整、未陈旧、未晚于决策。",
            "按策略、市场环境和预期差分层，避免把行情红利误判为规则能力。",
        ],
        "limitations": [
            "候选不会自动修改真实规则，也不会触发真实交易。",
            "手工模拟交易只展示绩效统计，不参与自动规则候选。",
            "单一模拟账户不是随机对照实验；规则变更必须保留原规则对照组。",
            "样本不足、证据缺失或数据质量不合格时禁止外推。",
        ],
        "requires_manual_confirmation": True,
        "auto_apply_allowed": False,
    }
