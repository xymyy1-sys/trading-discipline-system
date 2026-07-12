from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.holdings_calc import _account_total_asset
from app.api.helpers.quotes import (
    _QUOTE_META_CACHE,
    _estimated_vwap,
    _is_realtime_note,
    _latest_a_share_quotes,
    _normalize_code,
    _quote_lookup_code,
    _safe_float,
)
from app.api.helpers.seesaw import _market_seesaw_monitor
from app.models.trading import (
    ActionRecommendation,
    ExpectationSnapshot,
    ExitCard,
    Holding,
    IntradayEvidenceEvent,
    NextDayPlan,
    PositionExecutionState,
    PositionStateHistory,
    ProfitProtectionSnapshot,
    TradeLog,
    VolumePriceSnapshot,
)
from app.schemas.trading import (
    ActionRecommendationOut,
    ExpectationSnapshotOut,
    IntradayEvidenceEventOut,
    PositionExecutionStateOut,
    PositionStateHistoryOut,
    ProfitProtectionSnapshotOut,
    VolumePriceSnapshotOut,
)

WEAK_EXPECTATION_RESULTS = {"WEAKER", "INVALID", "SLIGHTLY_WEAKER"}
STRONG_EXPECTATION_RESULTS = {"STRONGER", "SLIGHTLY_STRONGER"}


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _trade_date() -> str:
    return datetime.now().date().isoformat()


def _quote_for_holding(holding: Holding, quotes: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    quotes = quotes or {}
    lookup_code = _quote_lookup_code(holding.code, quotes)
    quote = quotes.get(lookup_code)
    if quote:
        return quote
    return _QUOTE_META_CACHE.get(str(holding.code)) or _QUOTE_META_CACHE.get(_normalize_code(holding.code)) or {}


def _latest_quotes(holdings: list[Holding]) -> dict[str, dict[str, Any]]:
    try:
        return _latest_a_share_quotes([holding.code for holding in holdings if holding.code])
    except Exception:
        return {}


def _latest_profit_snapshot(db: Session, holding_id: int) -> ProfitProtectionSnapshot | None:
    return (
        db.query(ProfitProtectionSnapshot)
        .filter(ProfitProtectionSnapshot.holding_id == holding_id)
        .order_by(ProfitProtectionSnapshot.captured_at.desc(), ProfitProtectionSnapshot.id.desc())
        .first()
    )


def _latest_expectation_snapshot(db: Session, code: str) -> ExpectationSnapshot | None:
    normalized = _normalize_code(code)
    candidates = {code, normalized, normalized.lstrip("0")}
    return (
        db.query(ExpectationSnapshot)
        .filter(ExpectationSnapshot.code.in_(list(candidates)))
        .order_by(ExpectationSnapshot.created_at.desc(), ExpectationSnapshot.id.desc())
        .first()
    )


def _latest_volume_price_snapshot(db: Session, code: str) -> VolumePriceSnapshot | None:
    normalized = _normalize_code(code)
    candidates = {code, normalized, normalized.lstrip("0")}
    return (
        db.query(VolumePriceSnapshot)
        .filter(VolumePriceSnapshot.code.in_(list(candidates)))
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .first()
    )


def _expectation_result(row: ExpectationSnapshot | ExpectationSnapshotOut | None) -> str:
    if row is None:
        return ""
    return str(getattr(row, "expectation_result", "") or "")


def _expectation_score(row: ExpectationSnapshot | ExpectationSnapshotOut | None) -> int:
    if row is None:
        return 0
    try:
        return int(getattr(row, "expectation_gap_score", 0) or 0)
    except Exception:
        return 0


def _volume_pattern(row: VolumePriceSnapshot | VolumePriceSnapshotOut | None) -> str:
    if row is None:
        return ""
    return str(getattr(row, "pattern", "") or "")


def _volume_vwap_reliable(row: VolumePriceSnapshot | VolumePriceSnapshotOut | None, quote: dict[str, Any]) -> bool:
    if row is not None:
        return bool(getattr(row, "vwap_reliable", False))
    minute_rows = quote.get("minute_bars") or quote.get("minutes") or []
    return isinstance(minute_rows, list) and len(minute_rows) >= 3


def _volume_price_state(pattern: str, current: float, vwap: float, high_drawdown_pct: float) -> str:
    if "冲高回落跌破VWAP" in pattern:
        return "VOLUME_PRICE_WEAKENING"
    if "跌破VWAP" in pattern or (vwap and current < vwap):
        return "VWAP_BREAKDOWN"
    if "冲高回落" in pattern or high_drawdown_pct >= 4:
        return "HIGH_DRAWDOWN"
    if "VWAP上方强势" in pattern:
        return "REPAIR_CONFIRMED"
    if vwap and current >= vwap:
        return "VWAP_STRONG"
    return "VOLUME_PRICE_NEUTRAL"


def _position_quantities(db: Session, holding: Holding, trade_date: str) -> tuple[int, int, int]:
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    day_start = datetime.combine(datetime.now().date(), time.min)
    day_end = datetime.combine(datetime.now().date(), time.max)
    rows = (
        db.query(TradeLog)
        .filter(TradeLog.code.in_(list(candidates)), TradeLog.traded_at >= day_start, TradeLog.traded_at <= day_end)
        .all()
    )
    buy_sides = {"买入", "加仓", "做T买回", "T买回"}
    today_buy = sum(int(row.quantity or 0) for row in rows if row.side in buy_sides)
    current_quantity = int(holding.quantity or 0)
    sellable = max(0, current_quantity - today_buy)
    return sellable, today_buy, sellable


def _protection_level(max_profit_pct: float) -> tuple[str, float]:
    if max_profit_pct >= 10:
        return "LEVEL_4", 0.30
    if max_profit_pct >= 8:
        return "LEVEL_3", 0.35
    if max_profit_pct >= 5:
        return "LEVEL_2", 0.40
    if max_profit_pct >= 3:
        return "LEVEL_1", 0.60
    return "NONE", 1.0


def _script_hard_stop_ratio(position_type: str) -> float:
    text = position_type or ""
    if "趋势" in text or "容量" in text:
        return 0.92
    if "低吸" in text:
        return 0.95
    return 0.96


def _stop_source_label(source: str) -> str:
    labels = {
        "next_day_plan": "次日计划",
        "sell_card": "卖出卡",
        "text_script": "交易剧本文本",
        "fallback_candidate": "候选价兜底",
    }
    parts = [labels.get(part, part) for part in str(source or "").split("+") if part]
    return " + ".join(parts) or labels["fallback_candidate"]


def _script_stop_levels(holding: Holding, current_stop: float, current_hard_stop: float) -> tuple[float, float, list[str], list[str]]:
    text = f"{holding.position_type or ''} {holding.next_discipline or ''}"
    evidence: list[str] = []
    sources: list[str] = []
    hard_stop = current_hard_stop or (round(holding.cost_price * _script_hard_stop_ratio(holding.position_type), 2) if holding.cost_price else 0.0)
    structure_stop = current_stop
    price_patterns = [
        (r"(?:结构止损|结构位|失败位|防守位|跌破|破位|止损)\D{0,8}(\d+(?:\.\d+)?)", "structure"),
        (r"(?:硬止损|最终止损|绝对止损)\D{0,8}(\d+(?:\.\d+)?)", "hard"),
    ]
    for pattern, target in price_patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        value = float(match.group(1))
        if value <= 0:
            continue
        if target == "hard":
            hard_stop = round(value, 2)
            evidence.append(f"按交易剧本解析硬止损 {hard_stop:.2f}。")
            sources.append("text_script")
        else:
            structure_stop = round(value, 2)
            evidence.append(f"按交易剧本解析结构止损 {structure_stop:.2f}。")
            sources.append("text_script")

    pct_match = re.search(r"(?:止损|破位|亏损)\D{0,8}(\d+(?:\.\d+)?)\s*%", text)
    if pct_match and holding.cost_price:
        pct_stop = round(holding.cost_price * (1 - float(pct_match.group(1)) / 100), 2)
        if not evidence:
            evidence.append(f"按交易剧本解析百分比止损 {pct_stop:.2f}。")
        sources.append("text_script")
        structure_stop = max(structure_stop, pct_stop) if structure_stop else pct_stop

    return structure_stop, hard_stop, evidence, sources


def _structured_stop_levels(db: Session, holding: Holding, current_stop: float, hard_stop: float) -> tuple[float, float, list[str], list[str]]:
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    evidence: list[str] = []
    sources: list[str] = []
    structure_stop = current_stop
    hard_stop_price = hard_stop
    plan = (
        db.query(NextDayPlan)
        .filter(NextDayPlan.code.in_(list(candidates)))
        .order_by(NextDayPlan.plan_date.desc(), NextDayPlan.updated_at.desc(), NextDayPlan.id.desc())
        .first()
    )
    if plan:
        if float(plan.final_risk_price or 0) > 0:
            hard_stop_price = round(float(plan.final_risk_price), 2)
            evidence.append(f"按次日计划最终风险价设定硬止损 {hard_stop_price:.2f}。")
            sources.append("next_day_plan")
        plan_candidates = [
            float(plan.reduce_price or 0),
            float(plan.trim_price or 0),
            float(plan.confirm_price or 0),
            float(plan.stop_loss_4pct or 0),
        ]
        plan_stop = max([value for value in plan_candidates if value > 0], default=0.0)
        if plan_stop > 0:
            structure_stop = round(plan_stop, 2)
            evidence.append(f"按次日计划结构位设定结构止损 {structure_stop:.2f}。")
            sources.append("next_day_plan")
    exit_card = (
        db.query(ExitCard)
        .filter(ExitCard.code.in_(list(candidates)))
        .order_by(ExitCard.created_at.desc(), ExitCard.id.desc())
        .first()
    )
    if exit_card:
        if float(exit_card.failure_price or 0) > 0:
            hard_stop_price = round(float(exit_card.failure_price), 2)
            evidence.append(f"按卖出卡失败价设定硬止损 {hard_stop_price:.2f}。")
            sources.append("sell_card")
        card_candidates = [float(exit_card.trim_price or 0), float(exit_card.confirm_price or 0)]
        card_stop = max([value for value in card_candidates if value > 0], default=0.0)
        if card_stop > 0 and (not structure_stop or card_stop > structure_stop):
            structure_stop = round(card_stop, 2)
            evidence.append(f"按卖出卡减仓/确认价设定结构止损 {structure_stop:.2f}。")
            sources.append("sell_card")
    return structure_stop, hard_stop_price, evidence, sources


def _confirmation_deadline(holding: Holding) -> time:
    text = f"{holding.position_type or ''} {holding.next_discipline or ''}"
    match = re.search(r"(\d{1,2})[:：](\d{2})\s*(?:确认|截止|不修复|未修复)", text)
    if match:
        hour = min(14, max(9, int(match.group(1))))
        minute = min(59, max(0, int(match.group(2))))
        return time(hour, minute)
    if "打板" in text or "冲板" in text or "连板" in text:
        return time(9, 45)
    if "趋势" in text or "容量" in text:
        return time(10, 30)
    return time(10, 0)


def _action_from_score(score: int, hard_exit: bool, has_profit: bool) -> tuple[str, str, float, str]:
    if hard_exit:
        return "EXIT_REQUIRED", "全部退出", 1.0, "EXIT"
    if score >= 5:
        return "EXIT_REQUIRED", "只留观察仓", 0.75, "EXIT"
    if score >= 4:
        return "REDUCE_REQUIRED", "减仓50%", 0.50, "REDUCE"
    if score >= 2:
        return "PROFIT_PROTECTION" if has_profit else "DIVERGENCE_HOLD", "减仓25%", 0.25, "PROTECT"
    if score >= 1:
        return "DIVERGENCE_HOLD", "观察但禁止加仓", 0.0, "WATCH"
    return "PROFIT_EXPANSION" if has_profit else "NORMAL_HOLD", "继续持有", 0.0, "INFO"


def _high_open_failed_breakout_event(
    holding: Holding,
    quote: dict[str, Any],
    current: float,
    vwap: float,
    high_drawdown_pct: float,
    seesaw: Any | None,
    vwap_reliable: bool,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None,
) -> dict[str, Any] | None:
    prev_close = _safe_float(quote.get("prev_close")) or _safe_float(getattr(volume_price, "prev_close", 0))
    open_price = _safe_float(quote.get("open")) or _safe_float(getattr(volume_price, "open_price", 0))
    high_price = _safe_float(quote.get("high")) or _safe_float(getattr(volume_price, "high_price", 0))
    if not prev_close or not open_price or not high_price or not current:
        return None

    open_pct = (open_price - prev_close) / prev_close * 100
    high_pct = (high_price - prev_close) / prev_close * 100
    active_sell = _safe_float(getattr(volume_price, "active_sell_amount", 0))
    active_buy = _safe_float(getattr(volume_price, "active_buy_amount", 0))
    attack_efficiency = _safe_float(getattr(volume_price, "attack_efficiency", 0))
    sector_pullback = float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0) if seesaw else 0.0
    sector_net = float(getattr(seesaw, "sector_net_inflow", 0) or 0) if seesaw else 0.0

    evidence: list[str] = []
    matched = 0
    if open_pct >= 3:
        matched += 1
        evidence.append(f"高开 {open_pct:.2f}%，存在一致性兑现压力。")
    if high_pct >= 7 or high_price >= prev_close * 1.095:
        matched += 1
        evidence.append(f"盘中最高涨幅 {high_pct:.2f}%，接近/冲击涨停区。")
    if high_drawdown_pct >= 4:
        matched += 1
        evidence.append(f"从日内高点回撤 {high_drawdown_pct:.2f}%，冲高延续性不足。")
    if vwap_reliable and vwap and current < vwap:
        matched += 1
        evidence.append(f"当前价 {current:.2f} 跌破真实分钟VWAP {vwap:.2f}。")
    if active_sell > active_buy and active_sell > 0:
        matched += 1
        evidence.append(f"主动卖出额 {active_sell:.2f} 亿高于主动买入额 {active_buy:.2f} 亿。")
    if attack_efficiency > 0 and attack_efficiency < 0.2:
        matched += 1
        evidence.append(f"上攻效率仅 {attack_efficiency:.2f}，新增成交对价格推动减弱。")
    if sector_pullback >= 20 or sector_net < 0:
        matched += 1
        evidence.append("所属板块资金从峰值回落或转为净流出。")

    if matched < 3:
        return None

    level = "YELLOW"
    severity = "warning"
    priority = 72
    if (vwap_reliable and vwap and current < vwap and high_drawdown_pct >= 4) or matched >= 5:
        level = "ORANGE"
        severity = "critical"
        priority = 88
    if (vwap_reliable and vwap and current < vwap and high_drawdown_pct >= 7 and (active_sell > active_buy or sector_pullback >= 20 or sector_net < 0)):
        level = "RED"
        severity = "critical"
        priority = 98

    action = {
        "YELLOW": "停止加仓，提高止盈保护，观察VWAP。",
        "ORANGE": "冲板失败/跌破VWAP风险，建议减仓30%-50%。",
        "RED": "交易逻辑证伪风险，建议退出，禁止补仓和摊低成本。",
    }[level]
    evidence.append(action)
    return {
        "captured_at": datetime.now(),
        "scope": "stock",
        "target_code": holding.code,
        "target_name": holding.name,
        "event_type": "HIGH_OPEN_FAILED_BREAKOUT",
        "severity": severity,
        "value": round(current, 2),
        "previous_value": round(vwap or prev_close, 2),
        "priority": priority,
        "group_key": "stock:high-open-failed-breakout",
        "evidence": [f"{level} 风险："] + evidence,
    }


def _sector_migration_signal(seesaw: Any | None) -> tuple[bool, int, list[str], float, float]:
    if not seesaw:
        return False, 0, [], 0.0, 0.0
    target = str(getattr(seesaw, "external_inflow_target", "") or "")
    if not target:
        return False, 0, [], 0.0, 0.0

    evidence: list[str] = []
    criteria = 0
    sector_net = float(getattr(seesaw, "sector_net_inflow", 0) or 0)
    flow_peak = float(getattr(seesaw, "theme_flow_peak", 0) or 0)
    pullback = float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0)
    rank = int(getattr(seesaw, "sector_rank", 0) or 0)
    risk_level = str(getattr(seesaw, "risk_level", "") or "")

    if target:
        criteria += 1
        evidence.append(f"新题材/外部吸金方向为 {target}。")
    if sector_net < 0 or pullback >= 20:
        criteria += 1
        evidence.append(f"原主线资金弱化：净流入 {sector_net:.2f} 亿，峰值回落 {pullback:.2f}%。")
    if rank > 10:
        criteria += 1
        evidence.append(f"原主线资金排名降至第 {rank}，不在前排。")
    if risk_level in {"高", "中高", "中"}:
        criteria += 1
        evidence.append(f"持仓/主线风险等级为 {risk_level}。")
    stock_triggers = [str(item) for item in list(getattr(seesaw, "stock_weakening_trigger", []) or [])]
    sector_triggers = [str(item) for item in list(getattr(seesaw, "sector_ebb_trigger", []) or [])]
    if stock_triggers:
        criteria += 1
        evidence.extend(stock_triggers[:2])
    if sector_triggers:
        evidence.extend(sector_triggers[:2])

    confidence = min(95, 45 + criteria * 10 + (10 if sector_net < 0 and pullback >= 20 else 0))
    confirmed = criteria >= 3
    if confirmed:
        evidence.insert(0, f"疑似跨板块资金迁移，可信度 {confidence}%。")
    return confirmed, confidence, evidence[:7], sector_net, flow_peak


def _build_events(
    holding: Holding,
    current: float,
    vwap: float,
    current_profit_pct: float,
    max_profit_pct: float,
    profit_drawdown_pct: float,
    seesaw: Any | None,
    evidence: list[str],
    volume_price_state: str = "",
    expectation_result: str = "",
    vwap_reliable: bool = False,
    time_stop_reasons: list[str] | None = None,
    quote: dict[str, Any] | None = None,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None = None,
    high_drawdown_pct: float = 0,
) -> list[dict[str, Any]]:
    now = datetime.now()
    events: list[dict[str, Any]] = []
    if vwap and current < vwap and vwap_reliable:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "VWAP_BROKEN",
            "severity": "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 60,
            "group_key": "stock:vwap",
            "evidence": [f"当前价 {current:.2f} 跌破真实分钟VWAP {vwap:.2f}。"],
        })
    if volume_price_state in {"VOLUME_PRICE_WEAKENING", "HIGH_DRAWDOWN"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": volume_price_state,
            "severity": "critical" if volume_price_state == "VOLUME_PRICE_WEAKENING" and vwap_reliable else "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 90 if volume_price_state == "VOLUME_PRICE_WEAKENING" else 55,
            "group_key": "stock:volume-price",
            "evidence": (evidence[:3] or ["量价结构转弱。"]) + ([] if vwap_reliable else ["VWAP缺少真实1分钟成交确认，该事件仅作观察。"]),
        })
    if vwap_reliable and expectation_result in WEAK_EXPECTATION_RESULTS and volume_price_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "EXPECTATION_VOLUME_BREAKDOWN",
            "severity": "critical",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 95,
            "group_key": "stock:expectation-volume",
            "evidence": ["预期低于阈值，同时量价跌破关键承接，执行上优先降风险。"],
        })
    if max_profit_pct >= 5 and profit_drawdown_pct >= 3:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "PROFIT_DRAWDOWN_WARNING",
            "severity": "warning",
            "value": round(current_profit_pct, 2),
            "previous_value": round(max_profit_pct, 2),
            "priority": 50,
            "group_key": "stock:profit",
            "evidence": [f"最大浮盈 {max_profit_pct:.2f}%，当前 {current_profit_pct:.2f}%，利润回撤 {profit_drawdown_pct:.2f} 个百分点。"],
        })
    if seesaw and float(getattr(seesaw, "theme_flow_pullback_pct", 0) or 0) >= 20:
        events.append({
            "captured_at": now,
            "scope": "sector",
            "target_code": holding.code,
            "target_name": str(getattr(seesaw, "holding_theme", "") or holding.name),
            "event_type": "SECTOR_FLOW_PEAK_REVERSAL",
            "severity": "warning",
            "value": round(float(getattr(seesaw, "theme_flow_current", 0) or 0), 2),
            "previous_value": round(float(getattr(seesaw, "theme_flow_peak", 0) or 0), 2),
            "priority": 55,
            "group_key": "sector:flow",
            "evidence": [str(getattr(seesaw, "theme_flow_summary", "") or "板块资金从峰值回落。")],
        })
    migration_confirmed, migration_confidence, migration_evidence, migration_value, migration_previous = _sector_migration_signal(seesaw)
    if migration_confirmed:
        events.append({
            "captured_at": now,
            "scope": "sector",
            "target_code": holding.code,
            "target_name": str(getattr(seesaw, "holding_theme", "") or holding.name),
            "event_type": "SECTOR_MIGRATION_CONFIRMED",
            "severity": "warning",
            "value": migration_value,
            "previous_value": migration_previous,
            "priority": 75 + min(20, max(0, migration_confidence - 70)),
            "group_key": "sector:migration",
            "evidence": migration_evidence,
        })
    if seesaw and str(getattr(seesaw, "risk_level", "")) in {"高", "中高"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "EXPECTATION_DOWNGRADE",
            "severity": "critical" if getattr(seesaw, "risk_level", "") == "高" else "warning",
            "value": float(getattr(seesaw, "pullback_from_high_pct", 0) or 0),
            "previous_value": 0,
            "priority": 70,
            "group_key": "stock:risk",
            "evidence": evidence[:3] or [str(getattr(seesaw, "signal", "") or "持仓风险升高。")],
        })
    for reason in time_stop_reasons or []:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "TIME_STOP_TRIGGERED",
            "severity": "critical" if vwap_reliable else "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "priority": 92,
            "group_key": "stock:time-stop",
            "evidence": [reason],
        })
    high_open_event = _high_open_failed_breakout_event(
        holding,
        quote or {},
        current,
        vwap,
        high_drawdown_pct,
        seesaw,
        vwap_reliable,
        volume_price,
    )
    if high_open_event:
        events.append(high_open_event)
    return events


def _time_stop_reasons(
    db: Session,
    holding: Holding,
    current: float,
    vwap: float,
    vwap_reliable: bool,
    quote: dict[str, Any],
    volume_state: str,
    expectation_result: str,
    now: datetime,
) -> list[str]:
    reasons: list[str] = []
    deadline = _confirmation_deadline(holding)
    if vwap_reliable and vwap > 0 and current < vwap:
        rows = quote.get("minute_bars") or quote.get("minutes") or []
        below_rows = []
        if isinstance(rows, list):
            for row in rows[-8:]:
                if not isinstance(row, dict):
                    continue
                price = _safe_float(row.get("price") or row.get("close"))
                if price > 0 and price < vwap:
                    below_rows.append(row)
        if len(below_rows) >= 5:
            reasons.append(f"真实分钟数据连续 {len(below_rows)} 根低于VWAP {vwap:.2f}，触发时间止损观察。")
        else:
            normalized = _normalize_code(holding.code)
            candidates = {holding.code, normalized, normalized.lstrip("0")}
            recent = (
                db.query(VolumePriceSnapshot)
                .filter(
                    VolumePriceSnapshot.code.in_(list(candidates)),
                    VolumePriceSnapshot.trade_date == _trade_date(),
                    VolumePriceSnapshot.captured_at >= now - timedelta(minutes=15),
                    VolumePriceSnapshot.vwap_reliable.is_(True),
                )
                .order_by(VolumePriceSnapshot.captured_at.asc(), VolumePriceSnapshot.id.asc())
                .all()
            )
            below = [row for row in recent if float(row.price or 0) < float(row.vwap or 0)]
            if len(below) >= 3 and (below[-1].captured_at - below[0].captured_at) >= timedelta(minutes=5):
                reasons.append("15分钟窗口内多次低于真实VWAP且持续超过5分钟，确认持续低于VWAP。")

    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    limit_up_price = _safe_float(quote.get("limit_up_price")) or (round(prev_close * 1.1, 2) if prev_close else 0)
    if limit_up_price and high >= limit_up_price * 0.995 and current < limit_up_price * 0.985:
        reasons.append(f"盘中冲击涨停价 {limit_up_price:.2f} 后未回封，当前 {current:.2f} 已明显脱离封板区。")

    if now.time() >= deadline and expectation_result in WEAK_EXPECTATION_RESULTS and volume_state not in {"REPAIR_CONFIRMED", "VWAP_STRONG"}:
        reasons.append(f"{deadline.strftime('%H:%M')}确认截止后预期仍偏弱且量价未修复，触发时间止损确认。")
    return reasons


def _recovery_events(db: Session, holding: Holding, volume_state: str, current: float, vwap: float) -> list[dict[str, Any]]:
    if volume_state not in {"REPAIR_CONFIRMED", "VWAP_STRONG"}:
        return []
    normalized = _normalize_code(holding.code)
    candidates = {holding.code, normalized, normalized.lstrip("0")}
    since = datetime.now() - timedelta(minutes=45)
    risk_events = (
        db.query(IntradayEvidenceEvent)
        .filter(
            IntradayEvidenceEvent.target_code.in_(list(candidates)),
            IntradayEvidenceEvent.captured_at >= since,
            IntradayEvidenceEvent.event_type.in_([
                "VWAP_BROKEN",
                "VOLUME_PRICE_WEAKENING",
                "EXPECTATION_VOLUME_BREAKDOWN",
                "TIME_STOP_TRIGGERED",
            ]),
        )
        .order_by(IntradayEvidenceEvent.captured_at.desc(), IntradayEvidenceEvent.id.desc())
        .limit(3)
        .all()
    )
    if not risk_events:
        return []
    return [{
        "captured_at": datetime.now(),
        "scope": "stock",
        "target_code": holding.code,
        "target_name": holding.name,
        "event_type": "RISK_RECOVERY_CONFIRMED",
        "severity": "info",
        "value": round(current, 2),
        "previous_value": round(vwap, 2),
        "priority": 35,
        "group_key": "stock:recovery",
        "evidence": ["前序风险事件后重新站回真实VWAP/量价修复，风险状态恢复为观察。"],
    }]


def _confirmation_policy(event_type: str) -> tuple[int, int]:
    policy = {
        "VWAP_BROKEN": (5, 2),
        "VOLUME_PRICE_WEAKENING": (5, 2),
        "HIGH_DRAWDOWN": (10, 2),
        "EXPECTATION_VOLUME_BREAKDOWN": (3, 1),
        "TIME_STOP_TRIGGERED": (3, 1),
        "SECTOR_MIGRATION_CONFIRMED": (10, 2),
        "SECTOR_FLOW_PEAK_REVERSAL": (10, 2),
        "RISK_RECOVERY_CONFIRMED": (5, 1),
        "PROFIT_DRAWDOWN_WARNING": (15, 2),
    }
    return policy.get(event_type, (5, 2))


def _dedupe_events(db: Session, events: list[dict[str, Any]], cooldown_minutes: int = 5) -> list[dict[str, Any]]:
    persisted: list[dict[str, Any]] = []
    now = datetime.now()
    seen: set[tuple[str, str]] = set()
    for event in events:
        key = (str(event.get("target_code") or ""), str(event.get("event_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        event_type = str(event.get("event_type") or "")
        event_cooldown, required_occurrences = _confirmation_policy(event_type)
        window_minutes = event_cooldown or cooldown_minutes
        recent = (
            db.query(IntradayEvidenceEvent)
            .filter(
                IntradayEvidenceEvent.target_code == key[0],
                IntradayEvidenceEvent.event_type == key[1],
                IntradayEvidenceEvent.captured_at >= now - timedelta(minutes=window_minutes),
            )
            .order_by(IntradayEvidenceEvent.captured_at.desc(), IntradayEvidenceEvent.id.desc())
            .first()
        )
        if recent is not None:
            recent.last_seen_at = now
            recent.occurrence_count = int(recent.occurrence_count or 1) + 1
            recent.confirmed = recent.occurrence_count >= required_occurrences
            continue
        event["first_seen_at"] = event.get("captured_at")
        event["last_seen_at"] = event.get("captured_at")
        event["occurrence_count"] = 1
        event["confirmed"] = required_occurrences <= 1
        persisted.append(event)
    return persisted


def _state_history_out(row: PositionStateHistory) -> PositionStateHistoryOut:
    return PositionStateHistoryOut(
        id=row.id,
        holding_id=row.holding_id,
        code=row.code,
        name=row.name,
        trade_date=row.trade_date,
        old_state=row.old_state,
        new_state=row.new_state,
        captured_at=row.captured_at,
        reason=row.reason,
        evidence=_json_list(row.evidence_json),
    )


def _recent_state_history(db: Session, holding_id: int, limit: int = 20) -> list[PositionStateHistoryOut]:
    rows = (
        db.query(PositionStateHistory)
        .filter(PositionStateHistory.holding_id == holding_id)
        .order_by(PositionStateHistory.captured_at.desc(), PositionStateHistory.id.desc())
        .limit(limit)
        .all()
    )
    return [_state_history_out(row) for row in rows]


def build_position_execution_state(
    db: Session,
    holding: Holding,
    quote: dict[str, Any] | None = None,
    seesaw: Any | None = None,
    expectation: ExpectationSnapshot | ExpectationSnapshotOut | None = None,
    volume_price: VolumePriceSnapshot | VolumePriceSnapshotOut | None = None,
    persist: bool = True,
) -> PositionExecutionStateOut:
    quote = quote or {}
    now = datetime.now()
    current = _safe_float(quote.get("price")) or float(holding.current_price or 0)
    high = _safe_float(quote.get("high")) or max(current, float(holding.current_price or 0))
    low = _safe_float(quote.get("low"))
    open_price = _safe_float(quote.get("open"))
    vwap = _estimated_vwap(quote)
    total_asset = _account_total_asset(db) or float(holding.total_asset or 0)
    market_value = current * int(holding.quantity or 0)
    position_ratio = market_value / total_asset if total_asset else 0.0
    current_profit_pct = ((current - holding.cost_price) / holding.cost_price * 100) if holding.cost_price else 0.0
    high_profit_pct = ((high - holding.cost_price) / holding.cost_price * 100) if high and holding.cost_price else current_profit_pct
    previous_snapshot = _latest_profit_snapshot(db, int(holding.id or 0))
    expectation = expectation or _latest_expectation_snapshot(db, holding.code)
    volume_price = volume_price or _latest_volume_price_snapshot(db, holding.code)
    vwap_reliable = _volume_vwap_reliable(volume_price, quote)
    previous_max_profit = float(previous_snapshot.maximum_profit_pct or 0) if previous_snapshot else 0.0
    previous_max_price = float(previous_snapshot.maximum_price or 0) if previous_snapshot else 0.0
    previous_max_at = previous_snapshot.maximum_profit_at if previous_snapshot else None
    previous_day_max = float(getattr(previous_snapshot, "day_max_profit_pct", 0) or 0) if previous_snapshot else 0.0
    previous_day_max_at = getattr(previous_snapshot, "day_max_profit_at", None) if previous_snapshot else None
    max_profit_pct = max(previous_max_profit, high_profit_pct, current_profit_pct)
    maximum_price = max(previous_max_price, high, current)
    maximum_profit_at = previous_max_at if previous_max_profit >= max(high_profit_pct, current_profit_pct) else now
    day_max_profit_pct = max(previous_day_max, high_profit_pct, current_profit_pct)
    day_max_profit_at = previous_day_max_at if previous_day_max >= max(high_profit_pct, current_profit_pct) else now
    profit_drawdown_pct = max(0.0, max_profit_pct - current_profit_pct)
    protection_level, allowed_drawdown = _protection_level(max_profit_pct)
    floor_profit_pct = max_profit_pct * (1 - allowed_drawdown) if protection_level != "NONE" else 0.0
    profit_protection_price = round(holding.cost_price * (1 + floor_profit_pct / 100), 2) if floor_profit_pct and holding.cost_price else 0.0
    hard_stop_price = round(holding.cost_price * _script_hard_stop_ratio(holding.position_type), 2) if holding.cost_price else 0.0
    support_candidates = [value for value in [vwap, open_price, low, holding.cost_price * 0.97 if holding.cost_price else 0] if value and value > 0]
    structure_stop_price = round(max(min(support_candidates), hard_stop_price), 2) if support_candidates else hard_stop_price
    structure_stop_price, hard_stop_price, structured_stop_evidence, structured_stop_sources = _structured_stop_levels(
        db, holding, structure_stop_price, hard_stop_price
    )
    structure_stop_price, hard_stop_price, script_stop_evidence, script_stop_sources = _script_stop_levels(
        holding, structure_stop_price, hard_stop_price
    )
    stop_sources = list(dict.fromkeys(structured_stop_sources + script_stop_sources))
    stop_source = "+".join(stop_sources) if stop_sources else "fallback_candidate"
    stop_source_detail = "；".join((structured_stop_evidence + script_stop_evidence)[:4])
    if not stop_source_detail:
        stop_source_detail = "按VWAP、开盘价、日内低点、成本防守位和仓位类型硬止损比例生成候选止损。"
    trailing_stop_price = round(maximum_price * 0.95, 2) if maximum_price and protection_level in {"LEVEL_2", "LEVEL_3", "LEVEL_4"} else 0.0

    evidence: list[str] = [
        f"当前盈亏 {current_profit_pct:+.2f}%，最大浮盈 {max_profit_pct:+.2f}%，利润回撤 {profit_drawdown_pct:.2f} 个百分点。",
        f"结构止损 {structure_stop_price:.2f}，硬止损 {hard_stop_price:.2f}，利润保护线 {profit_protection_price:.2f}。",
        f"止损来源：{_stop_source_label(stop_source)}。{stop_source_detail}",
    ]
    evidence.extend(structured_stop_evidence)
    evidence.extend(script_stop_evidence)
    counter_evidence: list[str] = []
    invalid_conditions: list[str] = [
        f"放量跌破结构止损 {structure_stop_price:.2f} 且 5-15 分钟不能收回。",
        "板块资金继续回落且个股反抽 VWAP 失败。",
    ]
    recovery_conditions: list[str] = [
        "重新站回 VWAP 并维持至少一个观察窗口。",
        "所属板块资金停止流出或重新回到前排。",
    ]
    negative_score = 0
    hard_exit = False
    expectation_result = _expectation_result(expectation)
    expectation_gap_score = _expectation_score(expectation)
    volume_pattern = _volume_pattern(volume_price)
    high_drawdown_pct = ((high - current) / high * 100) if high and current else 0.0
    if volume_price is not None:
        high_drawdown_pct = max(high_drawdown_pct, _safe_float(getattr(volume_price, "high_drawdown", 0)))
        vwap = _safe_float(getattr(volume_price, "vwap", 0)) or vwap
    volume_state = _volume_price_state(volume_pattern, current, vwap if vwap_reliable else 0, high_drawdown_pct)
    time_stop_reasons = _time_stop_reasons(db, holding, current, vwap, vwap_reliable, quote, volume_state, expectation_result, now)

    if protection_level != "NONE":
        evidence.append(f"已进入{protection_level}利润保护，不能无条件放任盈利大幅回吐。")
    if expectation_result in WEAK_EXPECTATION_RESULTS:
        score_add = 2 if expectation_result in {"WEAKER", "INVALID"} or expectation_gap_score <= -18 else 1
        negative_score += score_add
        evidence.append(f"阶段预期结果 {expectation_result}，预期差 {expectation_gap_score}，执行侧不允许补仓摊低。")
        invalid_conditions.append("预期低于阈值且未出现量价修复前，禁止加仓或做T接回。")
    elif expectation_result in STRONG_EXPECTATION_RESULTS:
        counter_evidence.append(f"阶段预期结果 {expectation_result}，暂未构成预期证伪。")
    if volume_state == "VOLUME_PRICE_WEAKENING" and vwap_reliable:
        negative_score += 2
        evidence.append("量价形态为冲高回落跌破VWAP，优先按风险信号处理。")
        invalid_conditions.append("冲高回落跌破VWAP后，不能用主观预期继续扛单。")
    elif volume_state == "VWAP_BREAKDOWN" and vwap_reliable:
        negative_score += 1
        evidence.append("量价状态为跌破VWAP，等待重新站回后才允许恢复观察。")
    elif volume_state == "HIGH_DRAWDOWN":
        negative_score += 1
        evidence.append(f"相对日内高点回撤 {high_drawdown_pct:.2f}%，进入高位回落观察。")
    elif volume_state == "REPAIR_CONFIRMED":
        counter_evidence.append("量价状态为VWAP上方强势，暂不按走弱处理。")
    if current <= hard_stop_price and hard_stop_price:
        negative_score += 5
        hard_exit = True
        evidence.append(f"当前价 {current:.2f} 已触发硬止损 {hard_stop_price:.2f}。")
    elif current <= structure_stop_price and structure_stop_price:
        negative_score += 2
        evidence.append(f"当前价 {current:.2f} 已接近/跌破结构止损 {structure_stop_price:.2f}。")
    if time_stop_reasons and vwap_reliable:
        negative_score += 2
        evidence.extend(time_stop_reasons)
        invalid_conditions.append("时间止损触发后，必须看到真实VWAP修复或重新回封才允许恢复计划。")
    if vwap and vwap_reliable:
        if current < vwap:
            negative_score += 1
            evidence.append(f"当前价 {current:.2f} 跌破真实分钟VWAP {vwap:.2f}。")
        else:
            counter_evidence.append(f"当前仍在真实分钟VWAP {vwap:.2f} 上方。")
    else:
        counter_evidence.append("缺少真实1分钟成交数据，VWAP 为估算缺口，不把该项作为确定性卖点。")
    if max_profit_pct >= 8 and profit_drawdown_pct >= 3:
        negative_score += 2
        evidence.append("浮盈超过 8% 后出现明显回撤，优先保护利润。")
    elif max_profit_pct >= 5 and profit_drawdown_pct >= 3:
        negative_score += 1
        evidence.append("浮盈超过 5% 后回撤超过 3 个百分点，进入减仓观察。")
    if current_profit_pct < 0 and max_profit_pct >= 5:
        negative_score += 3
        evidence.append("曾有 5% 以上浮盈但当前转亏，触发 PROFIT_TO_LOSS_RISK。")
    if seesaw:
        risk_level = str(getattr(seesaw, "risk_level", "观察") or "观察")
        sector_state = str(getattr(seesaw, "signal", "") or risk_level)
        if risk_level == "高":
            negative_score += 2
        elif risk_level == "中高":
            negative_score += 1
        sector_triggers = list(getattr(seesaw, "sector_ebb_trigger", []) or [])
        stock_triggers = list(getattr(seesaw, "stock_weakening_trigger", []) or [])
        profit_triggers = list(getattr(seesaw, "profit_drawdown_trigger", []) or [])
        evidence.extend([str(item) for item in (sector_triggers + stock_triggers + profit_triggers)[:5]])
        if not sector_triggers:
            counter_evidence.append("暂未确认所属板块进入持续退潮。")
    else:
        sector_state = "资金跷跷板数据缺口"
        counter_evidence.append("未取得板块跷跷板数据，本次建议主要依据个股价格和利润保护。")

    state, action, reduce_ratio, level = _action_from_score(negative_score, hard_exit, current_profit_pct > 0)
    if expectation_result in WEAK_EXPECTATION_RESULTS and volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"} and not hard_exit:
        state = "EXPECTATION_VOLUME_BREAKDOWN"
        action = "减仓50%" if current_profit_pct >= 0 else "只留观察仓"
        reduce_ratio = max(reduce_ratio, 0.50 if current_profit_pct >= 0 else 0.75)
        level = "REDUCE" if current_profit_pct >= 0 else "EXIT"
    if not vwap_reliable and action in {"减仓25%", "减仓50%", "只留观察仓", "全部退出"} and not hard_exit:
        state = "DEGRADED_DATA_OBSERVATION"
        action = "观察但禁止加仓"
        reduce_ratio = 0.0
        level = "WATCH"
        evidence.append("数据降级：缺少真实1分钟VWAP，不输出确定性减仓、清仓或做T信号。")
        invalid_conditions.append("未恢复真实分钟成交数据前，系统建议只能作为观察提醒。")
    if current_profit_pct < 0 and state == "NORMAL_HOLD":
        state = "LOSS_OBSERVATION"
        action = "观察但禁止加仓"
    t_forbidden = bool(
        hard_exit
        or state in {"EXIT_REQUIRED", "REDUCE_REQUIRED", "EXPECTATION_VOLUME_BREAKDOWN"}
        or current < structure_stop_price
        or (vwap_reliable and volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"})
        or expectation_result in WEAK_EXPECTATION_RESULTS
        or (seesaw and getattr(seesaw, "risk_level", "") in {"高", "中高"})
    )
    t_eligible = not t_forbidden and int(holding.quantity or 0) > 0 and current_profit_pct >= 0 and protection_level != "NONE"
    t_type = "POSITIVE_T" if t_eligible else "NO_T"
    if t_forbidden:
        evidence.append("当前禁止做T：做T不能用于挽救已经证伪或需要降风险的交易。")
        t_type = "NO_T"
    recommended_position_ratio = max(0.0, position_ratio * (1 - reduce_ratio))
    sellable_quantity, today_buy_quantity, yesterday_quantity = _position_quantities(db, holding, _trade_date())
    evidence.append(f"T+1口径：当前持仓 {int(holding.quantity or 0)} 股，今日买入 {today_buy_quantity} 股，昨日可卖 {sellable_quantity} 股。")
    volume_price_state = volume_state
    data_quality = "realtime" if _is_realtime_note(str(quote.get("note") or "")) else "degraded" if quote else "manual"
    if quote and not vwap_reliable:
        data_quality = "degraded_vwap"
    data_time = str(quote.get("time") or quote.get("updated_at") or now.strftime("%H:%M:%S"))
    events = _build_events(
        holding,
        current,
        vwap,
        current_profit_pct,
        max_profit_pct,
        profit_drawdown_pct,
        seesaw,
        evidence,
        volume_price_state=volume_price_state,
        expectation_result=expectation_result,
        vwap_reliable=vwap_reliable,
        time_stop_reasons=time_stop_reasons,
        quote=quote,
        volume_price=volume_price,
        high_drawdown_pct=high_drawdown_pct,
    )
    events.extend(_recovery_events(db, holding, volume_price_state, current, vwap))

    snapshot = ProfitProtectionSnapshot(
        holding_id=int(holding.id),
        code=holding.code,
        captured_at=now,
        current_profit_pct=round(current_profit_pct, 2),
        maximum_profit_pct=round(max_profit_pct, 2),
        profit_drawdown_pct=round(profit_drawdown_pct, 2),
        maximum_price=round(maximum_price, 2),
        maximum_profit_at=maximum_profit_at,
        day_max_profit_pct=round(day_max_profit_pct, 2),
        day_max_profit_at=day_max_profit_at,
        protection_level=protection_level,
        protection_floor=profit_protection_price,
        triggered=protection_level != "NONE",
        recommended_action=action,
    )
    recommendation = ActionRecommendation(
        trade_date=_trade_date(),
        holding_id=int(holding.id),
        code=holding.code,
        name=holding.name,
        created_at=now,
        level=level,
        state=state,
        action=action,
        recommended_ratio=reduce_ratio,
        trigger_events_json=_json_dumps([event["event_type"] for event in events]),
        evidence_json=_json_dumps(evidence),
        counter_evidence_json=_json_dumps(counter_evidence),
        invalid_conditions_json=_json_dumps(invalid_conditions),
        recovery_conditions_json=_json_dumps(recovery_conditions),
        expires_at=now + timedelta(minutes=15),
    )
    state_row = (
        db.query(PositionExecutionState)
        .filter(PositionExecutionState.holding_id == int(holding.id), PositionExecutionState.trade_date == _trade_date())
        .first()
    )
    created_state = state_row is None
    if state_row is None:
        state_row = PositionExecutionState(holding_id=int(holding.id), code=holding.code, name=holding.name, trade_date=_trade_date())
    previous_state = "" if created_state else str(state_row.state or "")
    state_row.state = state
    state_row.expectation_state = (
        expectation_result
        or ("EXPECTATION_INVALIDATED" if state == "EXIT_REQUIRED" else "SLIGHTLY_WEAKER" if reduce_ratio else "MATCHED")
    )
    state_row.volume_price_state = volume_price_state
    state_row.sector_state = sector_state
    state_row.current_quantity = int(holding.quantity or 0)
    state_row.sellable_quantity = sellable_quantity
    state_row.today_buy_quantity = today_buy_quantity
    state_row.yesterday_quantity = yesterday_quantity
    state_row.current_position_ratio = round(position_ratio, 4)
    state_row.recommended_position_ratio = round(recommended_position_ratio, 4)
    state_row.recommended_action = action
    state_row.recommended_reduce_ratio = reduce_ratio
    state_row.structure_stop_price = structure_stop_price
    state_row.hard_stop_price = hard_stop_price
    state_row.stop_source = stop_source
    state_row.stop_source_detail = stop_source_detail
    state_row.trailing_stop_price = trailing_stop_price
    state_row.profit_protection_price = profit_protection_price
    state_row.t_eligible = t_eligible
    state_row.t_type = t_type
    state_row.evidence_json = _json_dumps(evidence)
    state_row.counter_evidence_json = _json_dumps(counter_evidence)
    state_row.invalid_conditions_json = _json_dumps(invalid_conditions)
    state_row.recovery_conditions_json = _json_dumps(recovery_conditions)
    state_row.data_quality = data_quality
    state_row.data_time = data_time
    state_row.updated_at = now

    persisted_events: list[IntradayEvidenceEvent] = []
    if persist:
        db.add(snapshot)
        db.add(recommendation)
        db.flush()
        if previous_state != state:
            history = PositionStateHistory(
                holding_id=int(holding.id),
                code=holding.code,
                name=holding.name,
                trade_date=_trade_date(),
                old_state=previous_state,
                new_state=state,
                captured_at=now,
                reason=action,
                evidence_json=_json_dumps((evidence + invalid_conditions)[:10]),
            )
            db.add(history)
        for event in _dedupe_events(db, events):
            row = IntradayEvidenceEvent(
                trade_date=_trade_date(),
                captured_at=event["captured_at"],
                scope=event["scope"],
                target_code=event["target_code"],
                target_name=event["target_name"],
                event_type=event["event_type"],
                severity=event["severity"],
                value=event["value"],
                previous_value=event["previous_value"],
                priority=int(event.get("priority") or 0),
                group_key=str(event.get("group_key") or ""),
                first_seen_at=event.get("first_seen_at"),
                last_seen_at=event.get("last_seen_at"),
                occurrence_count=int(event.get("occurrence_count") or 1),
                confirmed=bool(event.get("confirmed") or False),
                evidence_json=_json_dumps(event["evidence"]),
                recommendation_id=recommendation.id,
            )
            db.add(row)
            persisted_events.append(row)
        db.add(state_row)
        db.commit()
        db.refresh(state_row)
        db.refresh(snapshot)
        db.refresh(recommendation)
        for row in persisted_events:
            db.refresh(row)

    return _execution_state_out(state_row, snapshot, recommendation, persisted_events or events, db=db)


def _execution_state_out(
    state: PositionExecutionState,
    snapshot: ProfitProtectionSnapshot,
    recommendation: ActionRecommendation,
    events: list[IntradayEvidenceEvent | dict[str, Any]],
    db: Session | None = None,
) -> PositionExecutionStateOut:
    event_out: list[IntradayEvidenceEventOut] = []
    for event in events:
        if isinstance(event, dict):
            event_out.append(IntradayEvidenceEventOut(**event))
        else:
            event_out.append(
                IntradayEvidenceEventOut(
                    id=event.id,
                    captured_at=event.captured_at,
                    scope=event.scope,
                    target_code=event.target_code,
                    target_name=event.target_name,
                    event_type=event.event_type,
                    severity=event.severity,
                    value=event.value,
                    previous_value=event.previous_value,
                    priority=getattr(event, "priority", 0),
                    group_key=getattr(event, "group_key", ""),
                    first_seen_at=getattr(event, "first_seen_at", None),
                    last_seen_at=getattr(event, "last_seen_at", None),
                    occurrence_count=getattr(event, "occurrence_count", 1),
                    confirmed=bool(getattr(event, "confirmed", False)),
                    evidence=_json_list(event.evidence_json),
                )
            )
    return PositionExecutionStateOut(
        id=state.id,
        holding_id=state.holding_id,
        code=state.code,
        name=state.name,
        trade_date=state.trade_date,
        state=state.state,
        expectation_state=state.expectation_state,
        volume_price_state=state.volume_price_state,
        sector_state=state.sector_state,
        current_quantity=state.current_quantity,
        sellable_quantity=state.sellable_quantity,
        today_buy_quantity=state.today_buy_quantity,
        yesterday_quantity=getattr(state, "yesterday_quantity", state.sellable_quantity),
        current_position_ratio=state.current_position_ratio,
        recommended_position_ratio=state.recommended_position_ratio,
        recommended_action=state.recommended_action,
        recommended_reduce_ratio=state.recommended_reduce_ratio,
        structure_stop_price=state.structure_stop_price,
        hard_stop_price=state.hard_stop_price,
        stop_source=getattr(state, "stop_source", "fallback_candidate") or "fallback_candidate",
        stop_source_detail=getattr(state, "stop_source_detail", "") or "",
        trailing_stop_price=state.trailing_stop_price,
        profit_protection_price=state.profit_protection_price,
        t_eligible=state.t_eligible,
        t_type=state.t_type,
        evidence=_json_list(state.evidence_json),
        counter_evidence=_json_list(state.counter_evidence_json),
        invalid_conditions=_json_list(state.invalid_conditions_json),
        recovery_conditions=_json_list(state.recovery_conditions_json),
        events=event_out,
        recommendation=ActionRecommendationOut(
            id=recommendation.id,
            level=recommendation.level,
            state=recommendation.state,
            action=recommendation.action,
            recommended_ratio=recommendation.recommended_ratio,
            evidence=_json_list(recommendation.evidence_json),
            counter_evidence=_json_list(recommendation.counter_evidence_json),
            invalid_conditions=_json_list(recommendation.invalid_conditions_json),
            recovery_conditions=_json_list(recommendation.recovery_conditions_json),
            created_at=recommendation.created_at,
            expires_at=recommendation.expires_at,
            acknowledged_at=recommendation.acknowledged_at,
        ),
        profit_snapshot=ProfitProtectionSnapshotOut(
            id=snapshot.id,
            holding_id=snapshot.holding_id,
            code=snapshot.code,
            captured_at=snapshot.captured_at,
            current_profit_pct=snapshot.current_profit_pct,
            maximum_profit_pct=snapshot.maximum_profit_pct,
            profit_drawdown_pct=snapshot.profit_drawdown_pct,
            maximum_price=snapshot.maximum_price,
            maximum_profit_at=getattr(snapshot, "maximum_profit_at", None),
            day_max_profit_pct=getattr(snapshot, "day_max_profit_pct", 0),
            day_max_profit_at=getattr(snapshot, "day_max_profit_at", None),
            protection_level=snapshot.protection_level,
            protection_floor=snapshot.protection_floor,
            triggered=snapshot.triggered,
            recommended_action=snapshot.recommended_action,
        ),
        state_history=_recent_state_history(db, state.holding_id) if db is not None and state.holding_id else [],
        data_quality=state.data_quality,
        data_time=state.data_time,
        updated_at=state.updated_at,
    )


def build_execution_states(db: Session, holdings: list[Holding], force_refresh: bool = False) -> list[PositionExecutionStateOut]:
    quotes = _latest_quotes(holdings)
    seesaw_by_code: dict[str, Any] = {}
    try:
        seesaw = _market_seesaw_monitor(holdings, force_refresh=force_refresh)
        seesaw_by_code = {_normalize_code(item.code): item for item in seesaw.holding_alerts}
    except Exception:
        seesaw_by_code = {}
    return [
        build_position_execution_state(
            db,
            holding,
            quote=_quote_for_holding(holding, quotes),
            seesaw=seesaw_by_code.get(_normalize_code(holding.code)),
        )
        for holding in holdings
    ]
