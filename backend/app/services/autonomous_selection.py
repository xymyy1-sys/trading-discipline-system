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
    WatchlistEntry,
)
from app.services.market_data import MarketDataProvider


CAPTURE_TYPE = "ai_universe_selection"
CAPTURE_TARGET = "all_a"
NOTIFICATION_CAPTURE_TYPE = "ai_trader_notification"
AUTOMATION_KEY = "codex-ai-paper-trader-v1"
MAX_SELECTION_AGE = timedelta(minutes=12)
MAX_SELECTED = 12


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


def rank_full_market_rows(
    rows: Iterable[dict[str, Any]],
    *,
    feedback: dict[str, float] | None = None,
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
        industry = str(raw.get("f100") or "未分类").strip() or "未分类"
        if price <= 0 or volume_lots <= 0 or amount < 80_000_000:
            continue
        vwap = amount / (volume_lots * 100)
        if vwap <= 0:
            continue
        vwap_deviation = (price / vwap - 1) * 100
        # Avoid both falling knives and the user's recurrent straight-line chase.
        if not (-3.5 <= change <= 6.0 and 0.6 <= turnover <= 14 and 0.7 <= volume_ratio <= 6):
            continue
        if not (-1.0 <= vwap_deviation <= 2.8):
            continue

        liquidity_score = _bounded((math.log10(amount) - 7.9) * 12, 0, 18)
        activity_score = 18 - abs(volume_ratio - 2.0) * 5
        turnover_score = 14 - abs(turnover - 5.0) * 1.5
        price_score = 17 - abs(change - 1.8) * 3
        vwap_score = 18 - abs(vwap_deviation - 0.6) * 5
        flow_score = _bounded(main_flow_pct * 1.8, -8, 8) if main_flow else 0
        chase_penalty = max(0.0, change - 4.5) * 5 + max(0.0, vwap_deviation - 2.0) * 7
        result_feedback = float(feedback.get(code, 0))
        score = liquidity_score + activity_score + turnover_score + price_score + vwap_score + flow_score + result_feedback - chase_penalty
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

    rows, source, provider_at, scanned = MarketDataProvider()._fetch_limit_up_catcher_rows()
    regime = _latest_regime(db, trade_date)
    gate = _regime_gate(regime)
    items = rank_full_market_rows(
        rows,
        feedback=_feedback_by_code(db),
        minimum_score=float(gate["minimum_score"]),
    )
    payload = {
        "trade_date": trade_date,
        "source": source,
        "provider_at": provider_at.isoformat(),
        "total_scanned": scanned,
        "candidate_count": len(items),
        "gate": gate,
        "items": items,
        "method": "全A股→流动性→非极端活跃→VWAP承接→订单流估算→行业去重→真实交易反馈微调",
        "scope_note": "候选范围为东方财富全A实时行情，不依赖观察池、涨停池、抓涨停或断板反包。",
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
    return [(_normalize_code(str(item.get("code") or "")), str(item.get("name") or "")) for item in payload.get("items", []) if item.get("code")]


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
            entry_reason=json.dumps({"score": item["score"], "style": item["style"], "reasons": item["reasons"], "feedback": item["feedback_adjustment"]}, ensure_ascii=False),
        ))
    db.commit()
    return replacements
