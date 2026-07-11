from __future__ import annotations

import json
from datetime import datetime, timedelta
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
    Holding,
    IntradayEvidenceEvent,
    PositionExecutionState,
    ProfitProtectionSnapshot,
    VolumePriceSnapshot,
)
from app.schemas.trading import (
    ActionRecommendationOut,
    ExpectationSnapshotOut,
    IntradayEvidenceEventOut,
    PositionExecutionStateOut,
    ProfitProtectionSnapshotOut,
    VolumePriceSnapshotOut,
)


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
) -> list[dict[str, Any]]:
    now = datetime.now()
    events: list[dict[str, Any]] = []
    if vwap and current < vwap:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "VWAP_BROKEN",
            "severity": "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "evidence": [f"当前价 {current:.2f} 跌破估算 VWAP {vwap:.2f}。"],
        })
    if volume_price_state in {"VOLUME_PRICE_WEAKENING", "HIGH_DRAWDOWN"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": volume_price_state,
            "severity": "critical" if volume_price_state == "VOLUME_PRICE_WEAKENING" else "warning",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
            "evidence": evidence[:3] or ["量价结构转弱。"],
        })
    if expectation_result in {"WEAKER", "SLIGHTLY_WEAKER"} and volume_price_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"}:
        events.append({
            "captured_at": now,
            "scope": "stock",
            "target_code": holding.code,
            "target_name": holding.name,
            "event_type": "EXPECTATION_VOLUME_BREAKDOWN",
            "severity": "critical",
            "value": round(current, 2),
            "previous_value": round(vwap, 2),
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
            "evidence": [str(getattr(seesaw, "theme_flow_summary", "") or "板块资金从峰值回落。")],
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
            "evidence": evidence[:3] or [str(getattr(seesaw, "signal", "") or "持仓风险升高。")],
        })
    return events


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
    previous_max_profit = float(previous_snapshot.maximum_profit_pct or 0) if previous_snapshot else 0.0
    previous_max_price = float(previous_snapshot.maximum_price or 0) if previous_snapshot else 0.0
    max_profit_pct = max(previous_max_profit, high_profit_pct, current_profit_pct)
    maximum_price = max(previous_max_price, high, current)
    profit_drawdown_pct = max(0.0, max_profit_pct - current_profit_pct)
    protection_level, allowed_drawdown = _protection_level(max_profit_pct)
    floor_profit_pct = max_profit_pct * (1 - allowed_drawdown) if protection_level != "NONE" else 0.0
    profit_protection_price = round(holding.cost_price * (1 + floor_profit_pct / 100), 2) if floor_profit_pct and holding.cost_price else 0.0
    hard_stop_price = round(holding.cost_price * _script_hard_stop_ratio(holding.position_type), 2) if holding.cost_price else 0.0
    support_candidates = [value for value in [vwap, open_price, low, holding.cost_price * 0.97 if holding.cost_price else 0] if value and value > 0]
    structure_stop_price = round(max(min(support_candidates), hard_stop_price), 2) if support_candidates else hard_stop_price
    trailing_stop_price = round(maximum_price * 0.95, 2) if maximum_price and protection_level in {"LEVEL_2", "LEVEL_3", "LEVEL_4"} else 0.0

    evidence: list[str] = [
        f"当前盈亏 {current_profit_pct:+.2f}%，最大浮盈 {max_profit_pct:+.2f}%，利润回撤 {profit_drawdown_pct:.2f} 个百分点。",
        f"结构止损 {structure_stop_price:.2f}，硬止损 {hard_stop_price:.2f}，利润保护线 {profit_protection_price:.2f}。",
    ]
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
    volume_state = _volume_price_state(volume_pattern, current, vwap, high_drawdown_pct)

    if protection_level != "NONE":
        evidence.append(f"已进入{protection_level}利润保护，不能无条件放任盈利大幅回吐。")
    if expectation_result in {"WEAKER", "SLIGHTLY_WEAKER"}:
        score_add = 2 if expectation_result == "WEAKER" or expectation_gap_score <= -18 else 1
        negative_score += score_add
        evidence.append(f"阶段预期结果 {expectation_result}，预期差 {expectation_gap_score}，执行侧不允许补仓摊低。")
        invalid_conditions.append("预期低于阈值且未出现量价修复前，禁止加仓或做T接回。")
    elif expectation_result in {"STRONGER", "SLIGHTLY_STRONGER"}:
        counter_evidence.append(f"阶段预期结果 {expectation_result}，暂未构成预期证伪。")
    if volume_state == "VOLUME_PRICE_WEAKENING":
        negative_score += 2
        evidence.append("量价形态为冲高回落跌破VWAP，优先按风险信号处理。")
        invalid_conditions.append("冲高回落跌破VWAP后，不能用主观预期继续扛单。")
    elif volume_state == "VWAP_BREAKDOWN":
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
    if vwap:
        if current < vwap:
            negative_score += 1
            evidence.append(f"当前价 {current:.2f} 跌破估算 VWAP {vwap:.2f}。")
        else:
            counter_evidence.append(f"当前仍在估算 VWAP {vwap:.2f} 上方。")
    else:
        counter_evidence.append("缺少分钟成交数据，VWAP 为估算缺口，不把该项作为确定性卖点。")
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
    if expectation_result in {"WEAKER", "SLIGHTLY_WEAKER"} and volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"} and not hard_exit:
        state = "EXPECTATION_VOLUME_BREAKDOWN"
        action = "减仓50%" if current_profit_pct >= 0 else "只留观察仓"
        reduce_ratio = max(reduce_ratio, 0.50 if current_profit_pct >= 0 else 0.75)
        level = "REDUCE" if current_profit_pct >= 0 else "EXIT"
    if current_profit_pct < 0 and state == "NORMAL_HOLD":
        state = "LOSS_OBSERVATION"
        action = "观察但禁止加仓"
    t_forbidden = bool(
        hard_exit
        or state in {"EXIT_REQUIRED", "REDUCE_REQUIRED", "EXPECTATION_VOLUME_BREAKDOWN"}
        or current < structure_stop_price
        or volume_state in {"VWAP_BREAKDOWN", "VOLUME_PRICE_WEAKENING"}
        or expectation_result in {"WEAKER", "SLIGHTLY_WEAKER"}
        or (seesaw and getattr(seesaw, "risk_level", "") in {"高", "中高"})
    )
    t_eligible = not t_forbidden and int(holding.quantity or 0) > 0 and current_profit_pct >= 0 and protection_level != "NONE"
    t_type = "POSITIVE_T" if t_eligible else "NO_T"
    if t_forbidden:
        evidence.append("当前禁止做T：做T不能用于挽救已经证伪或需要降风险的交易。")
        t_type = "NO_T"
    recommended_position_ratio = max(0.0, position_ratio * (1 - reduce_ratio))
    volume_price_state = volume_state
    data_quality = "realtime" if _is_realtime_note(str(quote.get("note") or "")) else "degraded" if quote else "manual"
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
    )

    snapshot = ProfitProtectionSnapshot(
        holding_id=int(holding.id),
        code=holding.code,
        captured_at=now,
        current_profit_pct=round(current_profit_pct, 2),
        maximum_profit_pct=round(max_profit_pct, 2),
        profit_drawdown_pct=round(profit_drawdown_pct, 2),
        maximum_price=round(maximum_price, 2),
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
    if state_row is None:
        state_row = PositionExecutionState(holding_id=int(holding.id), code=holding.code, name=holding.name, trade_date=_trade_date())
    state_row.state = state
    state_row.expectation_state = (
        expectation_result
        or ("EXPECTATION_INVALIDATED" if state == "EXIT_REQUIRED" else "SLIGHTLY_WEAKER" if reduce_ratio else "MATCHED")
    )
    state_row.volume_price_state = volume_price_state
    state_row.sector_state = sector_state
    state_row.current_quantity = int(holding.quantity or 0)
    state_row.sellable_quantity = int(holding.quantity or 0)
    state_row.today_buy_quantity = 0
    state_row.current_position_ratio = round(position_ratio, 4)
    state_row.recommended_position_ratio = round(recommended_position_ratio, 4)
    state_row.recommended_action = action
    state_row.recommended_reduce_ratio = reduce_ratio
    state_row.structure_stop_price = structure_stop_price
    state_row.hard_stop_price = hard_stop_price
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
        for event in events:
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

    return _execution_state_out(state_row, snapshot, recommendation, persisted_events or events)


def _execution_state_out(
    state: PositionExecutionState,
    snapshot: ProfitProtectionSnapshot,
    recommendation: ActionRecommendation,
    events: list[IntradayEvidenceEvent | dict[str, Any]],
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
        current_position_ratio=state.current_position_ratio,
        recommended_position_ratio=state.recommended_position_ratio,
        recommended_action=state.recommended_action,
        recommended_reduce_ratio=state.recommended_reduce_ratio,
        structure_stop_price=state.structure_stop_price,
        hard_stop_price=state.hard_stop_price,
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
            protection_level=snapshot.protection_level,
            protection_floor=snapshot.protection_floor,
            triggered=snapshot.triggered,
            recommended_action=snapshot.recommended_action,
        ),
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
