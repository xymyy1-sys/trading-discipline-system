from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.execution import build_position_execution_state
from app.core.trading_clock import shanghai_today
from app.models.trading import Holding, TTradePlan
from app.schemas.trading import TEligibilityOut, TTradePlanIn, TTradePlanOut


def _today() -> str:
    return shanghai_today().isoformat()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def normalize_t_type(value: str | None) -> str:
    value = str(value or "NO_T").upper()
    if value == "INVERSE_T":
        return "REVERSE_T"
    if value in {"POSITIVE_T", "REVERSE_T", "NO_T"}:
        return value
    return "NO_T"


def _t_plan_out(row: TTradePlan) -> TTradePlanOut:
    return TTradePlanOut(
        id=row.id,
        holding_id=row.holding_id,
        trade_date=row.trade_date,
        code=row.code,
        name=row.name,
        t_type=normalize_t_type(row.t_type),
        planned_sell_price=row.planned_sell_price,
        planned_sell_quantity=row.planned_sell_quantity,
        buyback_price_low=row.buyback_price_low,
        buyback_price_high=row.buyback_price_high,
        buyback_conditions=_json_list(row.buyback_conditions_json),
        cancel_conditions=_json_list(row.cancel_conditions_json),
        status=row.status,
        actual_sell_price=row.actual_sell_price,
        actual_buyback_price=row.actual_buyback_price,
        actual_quantity=row.actual_quantity,
        actual_sell_quantity=row.actual_sell_quantity,
        actual_buyback_quantity=row.actual_buyback_quantity,
        execution_note=row.execution_note,
        cost_reduction=row.cost_reduction,
        evidence=_json_list(row.evidence_json),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class TTradingEngine:
    """V2.2 T-trading rules: positive T, reverse T, or explicit no-T."""

    def __init__(self, db: Session):
        self.db = db

    def build_eligibility(self, holding: Holding) -> TEligibilityOut:
        # Eligibility is a read model. Opening the decision card must not append
        # execution events or recommendations, especially after market close.
        execution = build_position_execution_state(self.db, holding, persist=False)
        forbidden: list[str] = []
        evidence: list[str] = []
        current = holding.current_price
        sellable = int(execution.sellable_quantity or 0)
        today_buy = int(execution.today_buy_quantity or 0)
        yesterday_quantity = int(execution.yesterday_quantity or sellable)
        suggested_qty = max(0, int(sellable * 0.25 // 100) * 100) if sellable >= 100 else 0

        if execution.state in {"EXIT_REQUIRED", "REDUCE_REQUIRED"}:
            forbidden.append(f"当前执行状态为 {execution.state}，先处理降风险，不做T。")
        if not execution.t_eligible:
            forbidden.append("持仓执行状态机判定当前不具备做T资格。")
        if sellable <= 0:
            forbidden.append("没有昨日可卖底仓。")
        if execution.profit_snapshot and execution.profit_snapshot.current_profit_pct < 0:
            forbidden.append("当前浮亏，做T容易演变为补仓摊低成本。")

        eligible = not forbidden and suggested_qty > 0
        has_profit_protection = bool(execution.profit_snapshot and execution.profit_snapshot.protection_level != "NONE")
        has_reversal_setup = any(
            keyword in item
            for item in execution.evidence
            for keyword in ("高点回撤", "冲高回落", "利润回撤", "冲击涨停")
        )
        reverse_candidate = (
            eligible
            and execution.state in {"PROFIT_PROTECTION", "DIVERGENCE_HOLD", "PROFIT_EXPANSION", "NORMAL_HOLD"}
            and has_profit_protection
            and has_reversal_setup
            and execution.data_quality == "realtime"
            and execution.volume_price_state in {"HIGH_DRAWDOWN", "VWAP_STRONG", "REPAIR_CONFIRMED", "VOLUME_PRICE_NEUTRAL"}
            and not any("跌破真实分钟VWAP" in item or "时间止损" in item for item in execution.evidence)
        )

        if eligible:
            evidence.append("原持仓逻辑未证伪，且仍有可卖底仓。")
            if reverse_candidate:
                evidence.append("允许倒T候选：先卖出昨日可卖底仓的一小部分，只有缩量回踩并重新修复后才接回。")
            else:
                evidence.append("只允许正T小比例卖出，等待重新确认后接回。")

        if reverse_candidate:
            buyback_low = round(max(execution.structure_stop_price, current * 0.965), 2) if current else 0
            buyback_high = round(current * 0.985, 2) if current else 0
            suggested_sell_price = round(current, 2)
            conditions = [
                "先卖出计划数量，不先低吸；接回必须等回踩缩量企稳。",
                "重新站回真实分钟VWAP，或至少连续3根1分钟K线不再创新低。",
                "主动卖出额下降，主动买入重新占优。",
                "所属板块订单流方向估算停止下降或重新转强。",
            ]
        else:
            buyback_low = round(current * 0.975, 2) if current else 0
            buyback_high = round(current * 0.99, 2) if current else 0
            suggested_sell_price = round(max(current * 1.02, current), 2) if current else 0
            conditions = [
                "回踩VWAP附近缩量企稳。",
                "5分钟内重新站回VWAP。",
                "所属板块订单流方向估算停止下降或重新转强。",
                "主动买入重新占优，不能继续创新低。",
            ]

        return TEligibilityOut(
            holding_id=int(holding.id),
            code=holding.code,
            name=holding.name,
            t_type="REVERSE_T" if reverse_candidate else "POSITIVE_T" if eligible else "NO_T",
            eligible=eligible,
            sellable_quantity=sellable,
            today_buy_quantity=today_buy,
            yesterday_quantity=yesterday_quantity,
            suggested_quantity=suggested_qty,
            suggested_sell_price=suggested_sell_price,
            buyback_price_low=buyback_low,
            buyback_price_high=buyback_high,
            buyback_conditions=conditions,
            forbidden_reasons=forbidden,
            evidence=evidence or execution.evidence[:3],
            current_action=execution.recommended_action,
        )

    def create_plan(self, holding: Holding, payload: TTradePlanIn | None = None) -> TTradePlanOut:
        eligibility = self.build_eligibility(holding)
        payload_provided = payload is not None
        payload = payload or TTradePlanIn()
        if not eligibility.eligible:
            t_type = "NO_T"
            quantity = 0
            evidence = eligibility.forbidden_reasons
        else:
            requested_type = payload.t_type if payload_provided else eligibility.t_type
            t_type = normalize_t_type(requested_type if requested_type != "NO_T" else eligibility.t_type)
            if t_type not in {"POSITIVE_T", "REVERSE_T"}:
                t_type = eligibility.t_type
            quantity = payload.planned_sell_quantity or eligibility.suggested_quantity
            evidence = eligibility.evidence
        row = TTradePlan(
            holding_id=int(holding.id),
            trade_date=_today(),
            code=holding.code,
            name=holding.name,
            t_type=t_type,
            planned_sell_price=payload.planned_sell_price or eligibility.suggested_sell_price,
            planned_sell_quantity=quantity,
            buyback_price_low=payload.buyback_price_low or eligibility.buyback_price_low,
            buyback_price_high=payload.buyback_price_high or eligibility.buyback_price_high,
            buyback_conditions_json=_json_dumps(payload.buyback_conditions or eligibility.buyback_conditions),
            cancel_conditions_json=_json_dumps(payload.cancel_conditions or [
                "跌破结构止损或反抽VWAP失败。",
                "板块订单流方向估算继续走弱。",
                "接回条件未满足，T仓卖出转为永久减仓。",
                "倒T低吸后未确认修复，不允许继续加第二笔。",
            ]),
            status="planned" if t_type != "NO_T" else "forbidden",
            evidence_json=_json_dumps(evidence),
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return _t_plan_out(row)

    def update_plan(self, row: TTradePlan, payload: Any) -> TTradePlanOut:
        changes = payload.model_dump(exclude_unset=True)
        sell_quantity = int(changes.get("actual_sell_quantity", row.actual_sell_quantity) or 0)
        buyback_quantity = int(changes.get("actual_buyback_quantity", row.actual_buyback_quantity) or 0)
        sell_price = float(changes.get("actual_sell_price", row.actual_sell_price) or 0)
        buyback_price = float(changes.get("actual_buyback_price", row.actual_buyback_price) or 0)

        if sell_quantity < 0 or buyback_quantity < 0:
            raise ValueError("execution quantity cannot be negative")
        if sell_quantity > row.planned_sell_quantity:
            raise ValueError("actual sell quantity exceeds the guarded plan quantity")
        if buyback_quantity > sell_quantity:
            raise ValueError("buyback quantity cannot exceed the executed sell quantity")
        if sell_quantity and sell_price <= 0:
            raise ValueError("actual sell price is required when a sell is recorded")
        if buyback_quantity and buyback_price <= 0:
            raise ValueError("actual buyback price is required when a buyback is recorded")

        requested_status = changes.get("status")
        allowed_statuses = {"planned", "sold_wait_buyback", "partially_bought_back", "completed", "cancelled", "converted_to_reduction", "forbidden"}
        if requested_status and requested_status not in allowed_statuses:
            raise ValueError("unsupported T plan status")
        current_status = row.status
        allowed_transitions = {
            "planned": {"planned", "sold_wait_buyback", "cancelled"},
            "sold_wait_buyback": {"sold_wait_buyback", "partially_bought_back", "completed", "converted_to_reduction"},
            "partially_bought_back": {"partially_bought_back", "completed", "converted_to_reduction"},
            "completed": {"completed"},
            "cancelled": {"cancelled"},
            "converted_to_reduction": {"converted_to_reduction"},
            "forbidden": {"forbidden"},
        }

        inferred_status = current_status
        if sell_quantity > 0 and buyback_quantity == 0:
            inferred_status = "sold_wait_buyback"
        elif sell_quantity > 0 and 0 < buyback_quantity < sell_quantity:
            inferred_status = "partially_bought_back"
        elif sell_quantity > 0 and buyback_quantity == sell_quantity:
            inferred_status = "completed"
        target_status = requested_status or inferred_status
        if target_status not in allowed_transitions.get(current_status, {current_status}):
            raise ValueError(f"invalid T plan transition: {current_status} -> {target_status}")
        if target_status == "completed" and (sell_quantity <= 0 or buyback_quantity != sell_quantity):
            raise ValueError("completed T plan requires equal executed sell and buyback quantities")
        if target_status == "converted_to_reduction" and sell_quantity <= buyback_quantity:
            raise ValueError("permanent reduction requires an unbought sold quantity")

        changes["status"] = target_status
        for key, value in changes.items():
            if value is not None:
                setattr(row, key, value)
        if row.actual_sell_price and row.actual_buyback_price and row.actual_buyback_quantity:
            row.actual_quantity = row.actual_buyback_quantity
            row.cost_reduction = round((row.actual_sell_price - row.actual_buyback_price) * row.actual_buyback_quantity, 2)
        self.db.commit()
        self.db.refresh(row)
        return _t_plan_out(row)


def build_t_eligibility(db: Session, holding: Holding) -> TEligibilityOut:
    return TTradingEngine(db).build_eligibility(holding)


def create_t_plan(db: Session, holding: Holding, payload: TTradePlanIn | None = None) -> TTradePlanOut:
    return TTradingEngine(db).create_plan(holding, payload)


def update_t_plan(db: Session, row: TTradePlan, payload: Any) -> TTradePlanOut:
    return TTradingEngine(db).update_plan(row, payload)
