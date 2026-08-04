from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.helpers.quotes import _normalize_code
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    DataCaptureSnapshot,
    Holding,
    MarketRegimeSnapshot,
    SimulationAccount,
    SimulationClosedTrade,
    SimulationShadowDecision,
    WatchlistEntry,
)
from app.services.market_data import MarketDataProvider


CAPTURE_TYPE = "ai_universe_selection"
CAPTURE_TARGET = "all_a"
NOTIFICATION_CAPTURE_TYPE = "ai_trader_notification"
AUTOMATION_KEY = "codex-ai-paper-trader-v1"
MAX_SELECTION_AGE = timedelta(minutes=12)
MAX_SELECTED = 12
MAX_EXPLORATION_SELECTED = 50
EXPLORATION_MIN_SCORE = 68
REFERENCE_BONUSES = {"抓涨停": 6.0, "断板反包": 5.0, "强板块核心": 8.0}


def _is_supported_mainboard_code(code: str) -> bool:
    """Return the user's explicitly allowed Shanghai/Shenzhen main-board scope."""

    normalized = _normalize_code(code)
    return normalized.startswith(("600", "601", "603", "605", "000", "001", "002", "003"))


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if math.isfinite(result) else 0.0


def _bounded(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _latest_regime(db: Session, trade_date: str) -> MarketRegimeSnapshot | None:
    return (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date == trade_date)
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )


def _regime_gate(regime: MarketRegimeSnapshot | None) -> dict[str, Any]:
    if regime is None or str(regime.data_quality or "").lower() not in {"real", "realtime", "live"}:
        return {"allow_entry": False, "minimum_score": 82, "max_entries": 0, "reason": "全市场状态缺少当日真实证据"}
    code = str(regime.regime_code or "").upper()
    risk = str(regime.risk_level or "")
    loss = int(regime.loss_score or 0)
    opportunity = int(regime.opportunity_score or 0)
    blocked = any(token in code for token in ("PANIC", "WEAK_CONTRACTION")) or risk in {"高", "极高"}
    if blocked or loss >= opportunity + 20:
        return {"allow_entry": False, "minimum_score": 86, "max_entries": 0, "reason": f"市场风险闸门关闭：{regime.regime_name}"}
    if loss > opportunity:
        return {"allow_entry": True, "minimum_score": 80, "max_entries": 1, "reason": f"亏钱效应占优，仅允许一只小仓确认：{regime.regime_name}"}
    return {"allow_entry": True, "minimum_score": 72, "max_entries": 3, "reason": f"市场允许选择性试仓：{regime.regime_name}"}


def _feedback_by_code(db: Session) -> dict[str, float]:
    account = db.query(SimulationAccount).filter(SimulationAccount.automation_key == AUTOMATION_KEY).first()
    if account is None:
        return {}
    values: dict[str, list[float]] = {}
    rows = (
        db.query(SimulationClosedTrade)
        .filter(SimulationClosedTrade.account_id == account.id)
        .order_by(SimulationClosedTrade.closed_at.desc())
        .limit(300)
        .all()
    )
    for row in rows:
        values.setdefault(_normalize_code(row.code), []).append(float(row.return_pct or 0))
    result: dict[str, float] = {}
    for code, returns in values.items():
        recent = returns[:5]
        mean = sum(recent) / len(recent)
        # Deliberately small: forward results may correct ranking, but may not
        # overwhelm today's price/volume evidence after only one sample.
        result[code] = _bounded(mean * 0.8, -10, 5)
    return result


def _source_feedback(db: Session) -> dict[str, dict[str, float]]:
    """Learn a deliberately small adjustment for each candidate source.

    Only completed round trips are used.  A source needs three samples before
    it may change ranking, preventing one lucky trade from dominating the
    all-market evidence score.
    """
    account = db.query(SimulationAccount).filter(SimulationAccount.automation_key == AUTOMATION_KEY).first()
    if account is None:
        return {}
    trades = (
        db.query(SimulationClosedTrade)
        .filter(SimulationClosedTrade.account_id == account.id, SimulationClosedTrade.entry_order_id.isnot(None))
        .order_by(SimulationClosedTrade.closed_at.desc())
        .limit(300)
        .all()
    )
    order_ids = [int(row.entry_order_id) for row in trades if row.entry_order_id]
    decisions = {
        int(row.order_id): row
        for row in db.query(SimulationShadowDecision).filter(
            SimulationShadowDecision.account_id == account.id,
            SimulationShadowDecision.order_id.in_(order_ids or [-1]),
        ).all()
        if row.order_id
    }
    values: dict[str, list[float]] = {name: [] for name in REFERENCE_BONUSES}
    for trade in trades:
        decision = decisions.get(int(trade.entry_order_id or 0))
        if decision is None:
            continue
        try:
            evidence = json.loads(decision.evidence_json or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = []
        joined = " ".join(str(value) for value in evidence)
        for source_name in values:
            if f"来源标签={source_name}" in joined or f"来源标签={source_name}," in joined:
                values[source_name].append(float(trade.return_pct or 0))
    result: dict[str, dict[str, float]] = {}
    for source_name, returns in values.items():
        recent = returns[:30]
        mean = sum(recent) / len(recent) if recent else 0.0
        result[source_name] = {
            "sample_count": len(recent),
            "mean_return_pct": round(mean, 3),
            "score_adjustment": round(_bounded(mean * 0.5, -4, 4), 2) if len(recent) >= 3 else 0.0,
        }
    return result


def _reference_signals(
    rows: Iterable[dict[str, Any]],
    provider: MarketDataProvider,
) -> tuple[dict[str, list[str]], dict[str, Any]]:
    """Collect existing screen results without turning them into buy signals."""
    signals: dict[str, list[str]] = {}
    catcher_count = 0
    for row in rows:
        code = _normalize_code(str(row.get("f12") or ""))
        price = _number(row.get("f2"))
        volume_lots = _number(row.get("f5"))
        amount = _number(row.get("f6"))
        if not code or price <= 0 or volume_lots <= 0 or amount <= 0:
            continue
        average = amount / (volume_lots * 100)
        if (
            _number(row.get("f10")) > 3
            and 0 < _number(row.get("f3")) <= 5
            and 3 <= _number(row.get("f8")) <= 8
            and price > average
        ):
            signals.setdefault(code, []).append("抓涨停")
            catcher_count += 1

    status: dict[str, Any] = {
        "抓涨停": {"status": "ok", "matched_count": catcher_count},
        "断板反包": {"status": "data_gap", "matched_count": 0},
    }
    try:
        result = provider.break_repackage(False)
        status["断板反包"] = {
            "status": str(result.data_status),
            "matched_count": len(result.items),
            "evaluation_date": result.evaluation_date,
        }
        for item in result.items:
            code = _normalize_code(item.code)
            if code:
                signals.setdefault(code, []).append("断板反包")
    except Exception as exc:  # A reference module may never block the base scan.
        status["断板反包"] = {"status": "data_gap", "matched_count": 0, "reason": exc.__class__.__name__}
    return signals, status


def _strong_sector_core_signals(
    provider: MarketDataProvider,
    *,
    sectors_per_type: int = 3,
    cores_per_sector: int = 4,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Discover liquid main-board cores inside objectively strong boards.

    Board strength remains discovery evidence only.  The returned securities
    still have to pass the independent quote score and later minute/VWAP gate.
    Fetch failures are kept in the audit payload rather than silently turning
    a partial universe into "no opportunity".
    """

    signals: dict[str, list[dict[str, Any]]] = {}
    status: dict[str, Any] = {"status": "ok", "sectors": [], "errors": []}
    for flow_type in ("行业资金流", "概念资金流"):
        try:
            boards = provider._fetch_direct_eastmoney_sector_flow_raw(flow_type, "今日")
        except Exception as exc:
            status["errors"].append({"flow_type": flow_type, "reason": exc.__class__.__name__})
            continue
        eligible = [
            row for row in boards
            if _number(row.get("change_pct")) >= 0.8
            and _number(row.get("net_inflow")) > 0
            and _number(row.get("strength")) >= 60
            and str(row.get("board_code") or "").strip()
        ]
        eligible.sort(
            key=lambda row: (
                -_number(row.get("strength")),
                -_number(row.get("change_pct")),
                -_number(row.get("net_inflow")),
            )
        )
        for sector_rank, board in enumerate(eligible[:sectors_per_type], start=1):
            board_code = str(board.get("board_code") or "")
            sector_name = str(board.get("name") or "未知板块")
            sector_audit: dict[str, Any] = {
                "flow_type": flow_type,
                "rank": sector_rank,
                "name": sector_name,
                "board_code": board_code,
                "strength": int(_number(board.get("strength"))),
                "change_pct": round(_number(board.get("change_pct")), 3),
                "estimated_net_inflow_yi": round(_number(board.get("net_inflow")), 3),
                "core_count": 0,
            }
            try:
                components = provider._fetch_sector_constituents_raw(board_code)
            except Exception as exc:
                sector_audit["status"] = "data_gap"
                sector_audit["reason"] = exc.__class__.__name__
                status["sectors"].append(sector_audit)
                continue

            candidates = [
                row for row in components
                if _is_supported_mainboard_code(str(row.get("code") or ""))
                and "ST" not in str(row.get("name") or "").upper()
                and "退" not in str(row.get("name") or "")
                and _number(row.get("price")) > 0
                and _number(row.get("amount")) >= 1.0
                and row.get("theme_quote_eligible") is not False
            ]
            # A core is not synonymous with the fastest riser.  Take a small
            # union of capacity, order-flow and price leaders so a large liquid
            # bellwether cannot be displaced by an illiquid rear-row spike.
            selected: list[tuple[dict[str, Any], str]] = []
            seen: set[str] = set()
            dimensions = (
                ("容量核心", lambda row: _number(row.get("amount"))),
                ("资金核心", lambda row: _number(row.get("main_inflow"))),
                ("强度核心", lambda row: _number(row.get("change_pct"))),
            )
            for role, key in dimensions:
                for component in sorted(candidates, key=key, reverse=True)[:2]:
                    code = _normalize_code(str(component.get("code") or ""))
                    if not code or code in seen:
                        continue
                    seen.add(code)
                    selected.append((component, role))
                    if len(selected) >= cores_per_sector:
                        break
                if len(selected) >= cores_per_sector:
                    break
            for component, role in selected:
                code = _normalize_code(str(component.get("code") or ""))
                signals.setdefault(code, []).append({
                    "source": "强板块核心",
                    "sector": sector_name,
                    "flow_type": flow_type,
                    "sector_rank": sector_rank,
                    "sector_strength": sector_audit["strength"],
                    "sector_change_pct": sector_audit["change_pct"],
                    "sector_estimated_net_inflow_yi": sector_audit["estimated_net_inflow_yi"],
                    "role": role,
                    "component_change_pct": round(_number(component.get("change_pct")), 3),
                    "component_amount_yi": round(_number(component.get("amount")), 3),
                    "component_estimated_main_inflow_yi": round(_number(component.get("main_inflow")), 3),
                })
            sector_audit["status"] = "ok"
            sector_audit["core_count"] = len(selected)
            sector_audit["core_codes"] = [_normalize_code(str(item.get("code") or "")) for item, _ in selected]
            status["sectors"].append(sector_audit)
    if not status["sectors"]:
        status["status"] = "data_gap"
    elif status["errors"] or any(row.get("status") != "ok" for row in status["sectors"]):
        status["status"] = "partial"
    status["core_security_count"] = len(signals)
    return signals, status


def _sector_coverage_audit(
    rows: Iterable[dict[str, Any]],
    sector_signals: dict[str, list[dict[str, Any]]],
    formal_items: list[dict[str, Any]],
    exploration_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Explain which strong-board cores were covered and why others were not."""

    raw_by_code = {_normalize_code(str(row.get("f12") or "")): row for row in rows}
    formal_codes = {str(item.get("code") or "") for item in formal_items}
    exploration_codes = {str(item.get("code") or "") for item in exploration_items}
    details: list[dict[str, Any]] = []
    for code, contexts in sector_signals.items():
        raw = raw_by_code.get(code)
        if code in formal_codes:
            status, reasons = "正式候选", []
        elif code in exploration_codes:
            status, reasons = "探索候选", ["尚未达到正式策略分数或市场闸门要求"]
        elif raw is None:
            status, reasons = "行情缺口", ["全A实时行情中未匹配到该板块核心股"]
        else:
            status = "未入选"
            price = _number(raw.get("f2"))
            change = _number(raw.get("f3"))
            volume_lots = _number(raw.get("f5"))
            amount = _number(raw.get("f6"))
            turnover = _number(raw.get("f8"))
            volume_ratio = _number(raw.get("f10"))
            average = amount / (volume_lots * 100) if volume_lots > 0 else 0
            deviation = (price / average - 1) * 100 if price > 0 and average > 0 else None
            reasons = []
            if price <= 0 or volume_lots <= 0 or amount < 50_000_000:
                reasons.append("价格、成交量或成交额证据不完整")
            if not (-5 <= change <= 6):
                reasons.append(f"涨幅{change:+.2f}%处于下跌刀口或追高区")
            if not (0.4 <= turnover <= 16):
                reasons.append(f"换手率{turnover:.2f}%不在可验证区间")
            if not (0.5 <= volume_ratio <= 8):
                reasons.append(f"量比{volume_ratio:.2f}不在可验证区间")
            if deviation is None:
                reasons.append("无法计算累计分时均价")
            elif not (-1.5 <= deviation <= 3.2):
                reasons.append(f"相对分时均价{deviation:+.2f}%不满足承接/防追高条件")
            if not reasons:
                status = "量价跟踪"
                reasons.append("已纳入分钟量价跟踪；综合评分或行业去重尚未进入交易候选")
        first = contexts[0] if contexts else {}
        details.append({
            "code": code,
            "name": str((raw or {}).get("f14") or ""),
            "sector": str(first.get("sector") or ""),
            "role": str(first.get("role") or ""),
            "status": status,
            "reasons": reasons,
            "captured_price": round(_number((raw or {}).get("f2")), 3) or None,
            "captured_change_pct": round(_number((raw or {}).get("f3")), 3) if raw else None,
            "contexts": contexts,
        })
    status_counts: dict[str, int] = {}
    for item in details:
        status_counts[item["status"]] = status_counts.get(item["status"], 0) + 1
    return {
        "strong_sector_core_count": len(details),
        "covered_count": sum(1 for item in details if item["status"] in {"正式候选", "探索候选", "量价跟踪"}),
        "status_counts": status_counts,
        "items": details,
        "note": "板块强度只用于补全候选发现；未通过个股预期、分钟量价和防追高验证的核心股不会下单。",
    }


def _missed_opportunity_audit(
    db: Session,
    trade_date: str,
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Compare the first same-day coverage decision with the latest quote.

    This is an opportunity-cost audit, not permission to backfill a trade.
    It lets calibration distinguish a healthy anti-chase rejection from a
    systematic failure to cover a strong-board main-board leader.
    """

    first = (
        db.query(DataCaptureSnapshot)
        .filter(
            DataCaptureSnapshot.data_type == CAPTURE_TYPE,
            DataCaptureSnapshot.target_code == CAPTURE_TARGET,
            DataCaptureSnapshot.trade_date == trade_date,
            DataCaptureSnapshot.status == "ok",
        )
        .order_by(DataCaptureSnapshot.captured_at.asc(), DataCaptureSnapshot.id.asc())
        .first()
    )
    if first is None:
        return {"sample_count": 0, "items": [], "note": "等待同交易日下一次扫描形成机会成本对照。"}
    try:
        payload = json.loads(first.normalized_value_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"sample_count": 0, "items": [], "note": "首个候选快照无法解析，未伪造机会成本。"}
    initial = ((payload.get("sector_coverage_audit") or {}).get("items") or []) if isinstance(payload, dict) else []
    current_by_code = {_normalize_code(str(row.get("f12") or "")): row for row in rows}
    compared: list[dict[str, Any]] = []
    for item in initial:
        code = _normalize_code(str(item.get("code") or ""))
        initial_price = _number(item.get("captured_price"))
        current_price = _number((current_by_code.get(code) or {}).get("f2"))
        if not code or initial_price <= 0 or current_price <= 0:
            continue
        move = (current_price / initial_price - 1) * 100
        compared.append({
            "code": code,
            "name": str(item.get("name") or (current_by_code.get(code) or {}).get("f14") or ""),
            "sector": str(item.get("sector") or ""),
            "initial_status": str(item.get("status") or ""),
            "initial_reasons": list(item.get("reasons") or []),
            "initial_price": round(initial_price, 3),
            "latest_price": round(current_price, 3),
            "move_since_first_scan_pct": round(move, 3),
            "interpretation": "漏选后继续上涨" if move >= 2 else "防追高有效/价格回落" if move <= -2 else "价格尚未显著脱离",
        })
    compared.sort(key=lambda item: -abs(float(item["move_since_first_scan_pct"])))
    omitted = [item for item in compared if item["initial_status"] not in {"正式候选", "探索候选"}]
    return {
        "sample_count": len(compared),
        "omitted_sample_count": len(omitted),
        "omitted_continued_rise_count": sum(1 for item in omitted if item["move_since_first_scan_pct"] >= 2),
        "omitted_pullback_count": sum(1 for item in omitted if item["move_since_first_scan_pct"] <= -2),
        "items": compared[:20],
        "note": "只比较当时可见快照与后续价格，不补录交易；收盘后进入策略校准样本。",
    }


def rank_full_market_rows(
    rows: Iterable[dict[str, Any]],
    *,
    feedback: dict[str, float] | None = None,
    reference_signals: dict[str, list[str]] | None = None,
    sector_signals: dict[str, list[dict[str, Any]]] | None = None,
    source_feedback: dict[str, dict[str, float]] | None = None,
    minimum_score: float = 72,
    limit: int = MAX_SELECTED,
) -> list[dict[str, Any]]:
    """Rank all-A quotes independently from every existing candidate pool.

    This is a *candidate* rank, not a buy instruction.  It favours liquid,
    moderately active names trading close to/above cumulative VWAP, and
    penalises late-stage chasing.  Eastmoney order-flow fields are explicitly
    labelled estimates and never make a candidate tradable by themselves.
    """

    feedback = feedback or {}
    reference_signals = reference_signals or {}
    sector_signals = sector_signals or {}
    source_feedback = source_feedback or {}
    ranked: list[dict[str, Any]] = []
    for raw in rows:
        code = _normalize_code(str(raw.get("f12") or ""))
        name = str(raw.get("f14") or "").strip()
        if not code or not name or "ST" in name.upper() or "退" in name:
            continue
        price = _number(raw.get("f2"))
        change = _number(raw.get("f3"))
        volume_lots = _number(raw.get("f5"))
        amount = _number(raw.get("f6"))
        turnover = _number(raw.get("f8"))
        volume_ratio = _number(raw.get("f10"))
        main_flow = _number(raw.get("f62"))
        main_flow_pct = _number(raw.get("f184"))
        sector_contexts = list(sector_signals.get(code, []))
        source_tags = list(dict.fromkeys(reference_signals.get(code, []) + (["强板块核心"] if sector_contexts else [])))
        industry = str(raw.get("f100") or "未分类").strip() or "未分类"
        minimum_amount = 50_000_000 if source_tags else 80_000_000
        if price <= 0 or volume_lots <= 0 or amount < minimum_amount:
            continue
        vwap = amount / (volume_lots * 100)
        if vwap <= 0:
            continue
        vwap_deviation = (price / vwap - 1) * 100
        # Avoid both falling knives and the user's recurrent straight-line chase.
        bounds_ok = -3.5 <= change <= 6.0 and 0.6 <= turnover <= 14 and 0.7 <= volume_ratio <= 6
        reference_bounds_ok = source_tags and -5 <= change <= 6 and 0.4 <= turnover <= 16 and 0.5 <= volume_ratio <= 8
        if not (bounds_ok or reference_bounds_ok):
            continue
        if not ((-1.0 <= vwap_deviation <= 2.8) or (source_tags and -1.5 <= vwap_deviation <= 3.2)):
            continue

        liquidity_score = _bounded((math.log10(amount) - 7.9) * 12, 0, 18)
        activity_score = 18 - abs(volume_ratio - 2.0) * 5
        turnover_score = 14 - abs(turnover - 5.0) * 1.5
        price_score = 17 - abs(change - 1.8) * 3
        vwap_score = 18 - abs(vwap_deviation - 0.6) * 5
        flow_score = _bounded(main_flow_pct * 1.8, -8, 8) if main_flow else 0
        chase_penalty = max(0.0, change - 4.5) * 5 + max(0.0, vwap_deviation - 2.0) * 7
        result_feedback = float(feedback.get(code, 0))
        source_contributions = []
        reference_bonus = 0.0
        for source_name in source_tags:
            learned = float((source_feedback.get(source_name) or {}).get("score_adjustment") or 0)
            contribution = REFERENCE_BONUSES.get(source_name, 0) + learned
            reference_bonus += contribution
            source_contributions.append({"source": source_name, "base": REFERENCE_BONUSES.get(source_name, 0), "learned": learned})
        reference_bonus = min(reference_bonus, 10.0)
        score = liquidity_score + activity_score + turnover_score + price_score + vwap_score + flow_score + result_feedback + reference_bonus - chase_penalty
        score = round(_bounded(score, 0, 100), 1)
        if score < minimum_score:
            continue

        if change < 0 <= vwap_deviation:
            style = "水下修复"
        elif change <= 2.2 and vwap_deviation <= 1.2:
            style = "回踩承接"
        else:
            style = "趋势确认"
        reasons = [
            f"成交额{amount / 100_000_000:.2f}亿，具备基本流动性",
            f"量比{volume_ratio:.2f}、换手{turnover:.2f}%，活跃但未达到极端拥挤",
            f"现价相对累计分时均价{vwap_deviation:+.2f}%（{style}）",
        ]
        if main_flow:
            reasons.append(f"大单方向估算{main_flow / 100_000_000:+.2f}亿（非账户真实流水）")
        for source_name in source_tags:
            reasons.append(f"{source_name}模块共振（候选证据，不单独触发买入）")
        if sector_contexts:
            context = sector_contexts[0]
            reasons.append(
                f"{context.get('sector') or '强板块'}第{context.get('sector_rank') or '-'}位的"
                f"{context.get('role') or '主板核心'}；仍需个股量价确认"
            )
        risks = []
        if change >= 4.5 or vwap_deviation >= 2:
            risks.append("接近追高区，必须等待回踩或下一采样继续确认")
        if main_flow < 0:
            risks.append("大单方向估算为流出，不能仅凭价格强势买入")
        ranked.append({
            "code": code,
            "name": name,
            "industry": industry,
            "score": score,
            "style": style,
            "price": round(price, 3),
            "change_pct": round(change, 3),
            "amount_yi": round(amount / 100_000_000, 3),
            "volume_ratio": round(volume_ratio, 3),
            "turnover_rate": round(turnover, 3),
            "vwap": round(vwap, 3),
            "price_vs_vwap": round(vwap_deviation, 3),
            "estimated_main_flow_yi": round(main_flow / 100_000_000, 3),
            "estimated_main_flow_pct": round(main_flow_pct, 3),
            "feedback_adjustment": round(result_feedback, 2),
            "source_tags": source_tags,
            "source_contributions": source_contributions,
            "sector_contexts": sector_contexts,
            "reasons": reasons,
            "risks": risks,
            "invalidation": "跌破累计分时均价且主动卖出增强，或市场风险闸门关闭",
            "next_plan": "先进入分钟量价验证；不追直线拉升，回踩不破或增量突破后才允许虚拟委托",
        })

    ranked.sort(key=lambda item: (-float(item["score"]), -float(item["amount_yi"]), item["code"]))
    selected: list[dict[str, Any]] = []
    industry_counts: dict[str, int] = {}
    for item in ranked:
        industry = str(item["industry"])
        if industry_counts.get(industry, 0) >= 2:
            continue
        selected.append(item)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        if len(selected) >= limit:
            break
    for index, item in enumerate(selected, start=1):
        item["rank"] = index
    return selected


def latest_autonomous_selection(
    db: Session,
    *,
    trade_date: str | None = None,
    max_age: timedelta | None = None,
) -> dict[str, Any] | None:
    query = db.query(DataCaptureSnapshot).filter(
        DataCaptureSnapshot.data_type == CAPTURE_TYPE,
        DataCaptureSnapshot.target_code == CAPTURE_TARGET,
        DataCaptureSnapshot.status == "ok",
        DataCaptureSnapshot.is_complete.is_(True),
    )
    if trade_date:
        query = query.filter(DataCaptureSnapshot.trade_date == trade_date)
    row = query.order_by(DataCaptureSnapshot.captured_at.desc(), DataCaptureSnapshot.id.desc()).first()
    if row is None:
        return None
    captured_at = shanghai_now_naive(row.captured_at)
    if max_age is not None and shanghai_now_naive() - captured_at > max_age:
        return None
    try:
        payload = json.loads(row.normalized_value_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    payload["snapshot_id"] = row.id
    payload["captured_at"] = captured_at.isoformat()
    return payload


def refresh_autonomous_selection(
    db: Session,
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    captured_at = shanghai_now_naive(now)
    trade_date = captured_at.date().isoformat()
    cached = latest_autonomous_selection(db, trade_date=trade_date, max_age=MAX_SELECTION_AGE)
    if cached is not None and not force:
        return cached

    provider = MarketDataProvider()
    rows, source, provider_at, scanned = provider._fetch_limit_up_catcher_rows()
    reference_signals, reference_status = _reference_signals(rows, provider)
    sector_signals, sector_core_status = _strong_sector_core_signals(provider)
    source_feedback = _source_feedback(db)
    regime = _latest_regime(db, trade_date)
    gate = _regime_gate(regime)
    items = rank_full_market_rows(
        rows,
        feedback=_feedback_by_code(db),
        reference_signals=reference_signals,
        sector_signals=sector_signals,
        source_feedback=source_feedback,
        minimum_score=float(gate["minimum_score"]),
    )
    exploration_items = rank_full_market_rows(
        rows,
        feedback=_feedback_by_code(db),
        reference_signals=reference_signals,
        sector_signals=sector_signals,
        source_feedback=source_feedback,
        minimum_score=EXPLORATION_MIN_SCORE,
        limit=MAX_EXPLORATION_SELECTED,
    )
    coverage_audit = _sector_coverage_audit(rows, sector_signals, items, exploration_items)
    payload = {
        "trade_date": trade_date,
        "source": source,
        "provider_at": provider_at.isoformat(),
        "total_scanned": scanned,
        "candidate_count": len(items),
        "gate": gate,
        "items": items,
        "exploration_items": exploration_items,
        "exploration_policy": {
            "minimum_score": EXPLORATION_MIN_SCORE,
            "maximum_daily_entries": 1,
            "position_ratio": 0.50,
            "purpose": "正常策略未成交时取得有统计意义的前向样本；单独标记，不伪装成正式信号",
        },
        "reference_sources": reference_status,
        "source_feedback": source_feedback,
        "strong_sector_core": sector_core_status,
        "sector_coverage_audit": coverage_audit,
        "sector_core_watch_items": [
            {"code": item["code"], "name": item["name"], "sector": item["sector"], "status": item["status"]}
            for item in coverage_audit["items"]
        ],
        "missed_opportunity_audit": _missed_opportunity_audit(db, trade_date, rows),
        "method": "全A股→强板块主板核心覆盖→流动性→非极端活跃→VWAP承接→订单流估算→行业去重→真实交易反馈微调",
        "scope_note": "候选范围为东方财富全A实时行情，并补充行业/概念强板块的沪深主板容量、资金与强度核心；所有板块标签、观察池、抓涨停和断板反包都只用于发现与归因，不能直接触发买入。",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row = DataCaptureSnapshot(
        trade_date=trade_date,
        captured_at=captured_at,
        source=source[:64],
        data_type=CAPTURE_TYPE,
        target_code=CAPTURE_TARGET,
        target_name="AI全市场独立选股",
        raw_value_json=json.dumps({"provider_at": provider_at.isoformat(), "total_scanned": scanned}, ensure_ascii=False),
        normalized_value_json=encoded,
        quality="realtime",
        is_complete=True,
        status="ok",
        raw_payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    payload["snapshot_id"] = row.id
    payload["captured_at"] = captured_at.isoformat()
    return payload


def autonomous_selection_targets(db: Session, trade_date: str) -> list[tuple[str, str]]:
    payload = latest_autonomous_selection(db, trade_date=trade_date, max_age=MAX_SELECTION_AGE)
    if not payload:
        return []
    # Minute evidence must also cover the bounded exploration universe and
    # strong-board main-board cores.  The latter are tracking targets only;
    # their presence here can never create an order by itself.  If the
    # normal market gate is closed, ``items`` can be empty; collecting only that
    # list would make the fallback rule impossible to verify and it would stay
    # permanently in cash.  Keep the extra workload bounded and de-duplicated.
    targets: list[tuple[str, str]] = []
    seen: set[str] = set()
    combined = (
        list(payload.get("items") or [])
        + list(payload.get("exploration_items") or [])[:20]
        + list(payload.get("sector_core_watch_items") or [])[:24]
    )
    for item in combined:
        code = _normalize_code(str(item.get("code") or ""))
        if not code or code in seen:
            continue
        seen.add(code)
        targets.append((code, str(item.get("name") or "")))
    return targets


def merge_autonomous_candidates_into_watchlist(db: Session, snapshot_date: str, max_replacements: int = 3) -> int:
    """Use forward-tested autonomous ranking to improve, not enlarge, the pool."""
    payload = latest_autonomous_selection(db, trade_date=snapshot_date)
    if not payload:
        return 0
    holdings = {row.code for row in db.query(Holding).all()}
    manual = {row.code for row in db.query(WatchlistEntry).filter(WatchlistEntry.source == "manual", WatchlistEntry.status == "active").all()}
    autos = db.query(WatchlistEntry).filter(
        WatchlistEntry.source == "auto",
        WatchlistEntry.status == "active",
        WatchlistEntry.snapshot_date == snapshot_date,
    ).order_by(WatchlistEntry.snapshot_rank.asc()).all()
    if not autos:
        return 0
    existing = {row.code for row in autos}
    candidates = [item for item in payload.get("items", []) if item.get("code") not in holdings | manual | existing]
    replacements = min(max_replacements, len(candidates), len(autos))
    if replacements <= 0:
        return 0
    for old, item in zip(reversed(autos[-replacements:]), candidates[:replacements]):
        old.status = "removed"
        old.exit_reason = "每日收盘由AI全市场独立评分的更高质量候选替换"
        old.exited_at = shanghai_now_naive()
        db.add(old)
        db.add(WatchlistEntry(
            code=str(item["code"]),
            name=str(item["name"]),
            status="active",
            source="auto",
            snapshot_date=snapshot_date,
            category="AI全市场策略候选",
            snapshot_rank=int(old.snapshot_rank),
            entry_reason=json.dumps({"score": item["score"], "style": item["style"], "reasons": item["reasons"], "feedback": item["feedback_adjustment"], "source_tags": item.get("source_tags", []), "source_contributions": item.get("source_contributions", [])}, ensure_ascii=False),
        ))
    db.commit()
    return replacements
