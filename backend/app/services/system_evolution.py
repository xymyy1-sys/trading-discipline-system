from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    DataCaptureSnapshot,
    SimulationAccount,
    SimulationClosedTrade,
    SimulationFill,
    SimulationShadowDecision,
    SystemImprovementProposal,
)


MODULE_LABELS = {
    "limit_up_plan_confirmation": "打板预案",
    "autonomous_universe_selection": "全市场自主选股",
    "autonomous_exploration_sample": "全市场探索样本",
    "expectation_volume_pair": "预期×量价",
    "position_execution_state": "持仓执行",
    "dynamic_profit_protection": "利润保护",
    "simulation_hard_stop": "硬止损",
    "pullback_reclaim_confirmation": "回踩确认",
}

REFERENCE_MODULES = ("自动观察池", "抓涨停", "断板反包", "强板块核心")


def _json(raw: str | None, default: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return value


def decision_modules(decision: SimulationShadowDecision) -> list[str]:
    modules = [MODULE_LABELS.get(decision.source_kind, decision.source_kind or decision.strategy_source)]
    evidence = _json(decision.evidence_json, [])
    joined = " ".join(str(item) for item in evidence)
    for module in REFERENCE_MODULES:
        if module in joined:
            modules.append(module)
    return list(dict.fromkeys(item for item in modules if item))


def _module_scorecards(db: Session, account: SimulationAccount, *, trade_date: str | None = None) -> list[dict[str, Any]]:
    query = db.query(SimulationShadowDecision).filter(SimulationShadowDecision.account_id == account.id)
    if trade_date:
        query = query.filter(SimulationShadowDecision.trade_date == trade_date)
    decisions = query.order_by(SimulationShadowDecision.id.asc()).all()
    fill_by_order = {
        row.order_id: row
        for row in db.query(SimulationFill).filter(SimulationFill.account_id == account.id).all()
    }
    closed_by_entry = {
        row.entry_order_id: row
        for row in db.query(SimulationClosedTrade).filter(SimulationClosedTrade.account_id == account.id).all()
        if row.entry_order_id is not None
    }
    state: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "candidate_count": 0,
        "selected_count": 0,
        "skipped_count": 0,
        "fill_count": 0,
        "closed_trade_count": 0,
        "wins": 0,
        "returns": [],
        "skip_reasons": Counter(),
    })
    for decision in decisions:
        for module in decision_modules(decision):
            row = state[module]
            row["candidate_count"] += 1
            if decision.order_id:
                row["selected_count"] += 1
            else:
                row["skipped_count"] += 1
                if decision.reason:
                    row["skip_reasons"][decision.reason[:100]] += 1
            if decision.order_id in fill_by_order:
                row["fill_count"] += 1
            closed = closed_by_entry.get(decision.order_id)
            if closed is not None:
                result = float(closed.return_pct or 0)
                row["closed_trade_count"] += 1
                row["wins"] += int(result > 0)
                row["returns"].append(result)
    output: list[dict[str, Any]] = []
    for module, row in state.items():
        returns = row.pop("returns")
        reasons: Counter = row.pop("skip_reasons")
        closed_count = int(row["closed_trade_count"])
        output.append({
            "module_key": module,
            **row,
            "win_rate": round(row["wins"] / closed_count * 100, 1) if closed_count else None,
            "average_return_pct": round(sum(returns) / len(returns), 2) if returns else None,
            "top_skip_reasons": [
                {"reason": reason, "count": count}
                for reason, count in reasons.most_common(3)
            ],
        })
    output.sort(key=lambda item: (item["candidate_count"], item["module_key"]), reverse=True)
    return output


DIAGNOSTIC_RULES = {
    "买入后未形成有效上攻": {
        "module": "预期×量价",
        "level": "strategy",
        "title": "买点缺少二次承接确认",
        "change": "复核首次站回VWAP后的持续时间、主动买卖额和回踩承接；新增影子规则，不直接覆盖当前买点。",
        "effect": "降低冲高瞬间买入后立即回落的假突破样本。",
        "risk": "确认过严可能错过快速直线拉升。",
    },
    "买入时量能明显衰减": {
        "module": "量价证据",
        "level": "parameter",
        "title": "量能衰减时仍允许入场",
        "change": "把量能加速度作为独立否决证据，并按低吸、突破和打板策略分别校准阈值。",
        "effect": "避免用缩量脉冲误判为持续增量。",
        "risk": "极端惜售上涨可能被误过滤。",
    },
    "买入偏离VWAP过远": {
        "module": "预期×量价",
        "level": "parameter",
        "title": "追涨买点偏离分时均价过远",
        "change": "非涨停策略增加偏离VWAP上限并等待回踩；打板策略使用独立晋级与封板成交模型。",
        "effect": "减少高位情绪化追入。",
        "risk": "趋势加速日可能减少成交。",
    },
    "卖出后出现明显修复": {
        "module": "持仓执行",
        "level": "strategy",
        "title": "卖点容易落在恐慌低点",
        "change": "除硬止损外增加开盘观察窗、VWAP修复和反抽失败二次确认，并记录卖出后不操作对照。",
        "effect": "降低卖出后快速V形修复的机会成本。",
        "risk": "持续单边下跌时会扩大少量回撤。",
    },
}


def _upsert_proposal(
    db: Session,
    *,
    account: SimulationAccount,
    trade_date: str,
    diagnostic: str,
    count: int,
    sample_ids: list[int],
) -> SystemImprovementProposal:
    rule = DIAGNOSTIC_RULES[diagnostic]
    fingerprint = hashlib.sha256(
        f"{trade_date}|{rule['module']}|{rule['title']}|{','.join(map(str, sample_ids))}".encode("utf-8")
    ).hexdigest()
    existing = db.query(SystemImprovementProposal).filter(
        SystemImprovementProposal.proposal_hash == fingerprint,
    ).first()
    if existing is not None:
        return existing
    key = f"EVO-{trade_date.replace('-', '')}-{fingerprint[:8].upper()}"
    row = SystemImprovementProposal(
        proposal_key=key,
        proposal_hash=fingerprint,
        trade_date=trade_date,
        account_id=account.id,
        level=str(rule["level"]),
        module_key=str(rule["module"]),
        title=str(rule["title"]),
        problem=f"前向交易事实重复出现“{diagnostic}”，本批共{count}个样本。",
        evidence_json=json.dumps(
            [{"data_type": "ai_trade_learning", "snapshot_id": sample_id} for sample_id in sample_ids],
            ensure_ascii=False,
        ),
        proposed_change=str(rule["change"]),
        expected_effect=str(rule["effect"]),
        risks_json=json.dumps([rule["risk"], "单批样本不能直接修改生产规则。"], ensure_ascii=False),
        acceptance_json=json.dumps([
            "使用冻结证据重放，不得引用决策时点之后的数据",
            "新旧规则并行影子运行",
            "至少累计3个独立样本；参数正式晋级仍需满足现有30笔前向样本门槛",
            "最大不利波动改善且盈亏比不得恶化",
            "发布后保留自动回滚路径",
        ], ensure_ascii=False),
        sample_count=count,
        priority="P1" if count >= 3 else "P2",
        status="PROPOSED",
        created_at=shanghai_now_naive(),
        updated_at=shanghai_now_naive(),
    )
    db.add(row)
    db.flush()
    return row


def _upsert_module_proposal(
    db: Session,
    *,
    account: SimulationAccount,
    trade_date: str,
    scorecard: dict[str, Any],
) -> SystemImprovementProposal:
    module = str(scorecard["module_key"])
    sample_count = int(scorecard["closed_trade_count"])
    average_return = float(scorecard.get("average_return_pct") or 0)
    win_rate = float(scorecard.get("win_rate") or 0)
    fingerprint = hashlib.sha256(
        f"module|{account.id}|{module}|{sample_count}|{average_return:.2f}|{win_rate:.1f}".encode("utf-8")
    ).hexdigest()
    existing = db.query(SystemImprovementProposal).filter(
        SystemImprovementProposal.proposal_hash == fingerprint,
    ).first()
    if existing is not None:
        return existing
    key = f"EVO-{trade_date.replace('-', '')}-{fingerprint[:8].upper()}"
    row = SystemImprovementProposal(
        proposal_key=key,
        proposal_hash=fingerprint,
        trade_date=trade_date,
        account_id=account.id,
        level="module",
        module_key=module,
        title=f"{module}闭环结果未达到保留标准",
        problem=(
            f"该功能已形成{sample_count}笔闭环交易，胜率{win_rate:.1f}%，"
            f"平均收益{average_return:+.2f}%；需要检查候选覆盖、排序证据和入场转化链。"
        ),
        evidence_json=json.dumps([{"data_type": "module_scorecard", **scorecard}], ensure_ascii=False),
        proposed_change=(
            "先复盘该模块漏选、误选及被其他闸门过滤的样本，提出候选生成/排序改版；"
            "旧模块继续运行，新逻辑只做影子对照，禁止为提高胜率删除亏损样本。"
        ),
        expected_effect="让功能模块的改造由真实交易结果驱动，并可量化比较改版前后的覆盖率、盈亏比与回撤。",
        risks_json=json.dumps(["小样本可能受单一行情风格影响", "改进候选覆盖可能同时增加假信号"], ensure_ascii=False),
        acceptance_json=json.dumps([
            "冻结当前模块版本和本批交易样本",
            "改版必须同时报告漏选机会成本与追入后回落成本",
            "至少新增10个影子样本后比较，正式参数晋级仍执行30笔门槛",
            "平均收益或盈亏比改善，最大不利波动不得恶化",
        ], ensure_ascii=False),
        sample_count=sample_count,
        priority="P1" if sample_count >= 5 and average_return < 0 else "P2",
        status="PROPOSED",
        created_at=shanghai_now_naive(),
        updated_at=shanghai_now_naive(),
    )
    db.add(row)
    db.flush()
    return row


def generate_system_improvement_proposals(
    db: Session,
    account: SimulationAccount,
    *,
    trade_date: str,
) -> list[SystemImprovementProposal]:
    snapshots = db.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.data_type == "ai_trade_learning",
        DataCaptureSnapshot.status == "evaluated",
    ).order_by(DataCaptureSnapshot.captured_at.desc(), DataCaptureSnapshot.id.desc()).limit(120).all()
    tagged: dict[str, list[int]] = defaultdict(list)
    for snapshot in snapshots:
        payload = _json(snapshot.normalized_value_json, {})
        if not isinstance(payload, dict) or payload.get("status") != "evaluated":
            continue
        if payload.get("account_id") not in (None, account.id):
            continue
        for diagnostic in payload.get("diagnostic_tags") or []:
            if diagnostic in DIAGNOSTIC_RULES:
                tagged[str(diagnostic)].append(snapshot.id)
    created: list[SystemImprovementProposal] = []
    for diagnostic, sample_ids in tagged.items():
        # One exceptional sample is displayed in the daily review, but a code
        # change proposal requires repetition to avoid learning one day's noise.
        if len(sample_ids) < 2:
            continue
        created.append(_upsert_proposal(
            db,
            account=account,
            trade_date=trade_date,
            diagnostic=diagnostic,
            count=len(sample_ids),
            sample_ids=sample_ids[:20],
        ))
    for scorecard in _module_scorecards(db, account):
        closed_count = int(scorecard["closed_trade_count"])
        average_return = scorecard.get("average_return_pct")
        win_rate = scorecard.get("win_rate")
        if closed_count < 3 or average_return is None or win_rate is None:
            continue
        if float(average_return) < 0 or float(win_rate) < 35:
            created.append(_upsert_module_proposal(
                db,
                account=account,
                trade_date=trade_date,
                scorecard=scorecard,
            ))
    db.commit()
    return created


def system_evolution_report(
    db: Session,
    account: SimulationAccount,
    *,
    trade_date: str | None = None,
) -> dict[str, Any]:
    proposals_query = db.query(SystemImprovementProposal).filter(
        SystemImprovementProposal.account_id == account.id,
    )
    if trade_date:
        proposals_query = proposals_query.filter(SystemImprovementProposal.trade_date == trade_date)
    proposals = proposals_query.order_by(
        SystemImprovementProposal.created_at.desc(),
        SystemImprovementProposal.id.desc(),
    ).limit(100).all()
    return {
        "account_id": account.id,
        "trade_date": trade_date or "",
        "generated_at": shanghai_now_naive(),
        "module_scorecards": _module_scorecards(db, account, trade_date=trade_date),
        "proposals": [
            {
                "id": row.id,
                "proposal_key": row.proposal_key,
                "trade_date": row.trade_date,
                "level": row.level,
                "module_key": row.module_key,
                "title": row.title,
                "problem": row.problem,
                "evidence": _json(row.evidence_json, []),
                "proposed_change": row.proposed_change,
                "expected_effect": row.expected_effect,
                "risks": _json(row.risks_json, []),
                "acceptance": _json(row.acceptance_json, []),
                "sample_count": row.sample_count,
                "priority": row.priority,
                "status": row.status,
                "created_at": row.created_at,
            }
            for row in proposals
        ],
        "governance": {
            "automatic_discovery": True,
            "automatic_code_change": False,
            "required_flow": ["发现", "提案", "用户批准", "Codex开发", "影子验证", "发布或回滚"],
        },
    }
