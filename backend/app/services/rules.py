from app.schemas.trading import MarketGradeOut, PreTradeCheckIn, PreTradeCheckOut, RiskPositionIn, RiskPositionOut


def calculate_risk_position(payload: RiskPositionIn) -> RiskPositionOut:
    if payload.net_asset <= 0 or payload.entry_price <= 0:
        raise ValueError("net asset and entry price must be positive")
    if payload.stop_price <= 0 or payload.stop_price >= payload.entry_price:
        raise ValueError("stop price must be positive and below entry price")
    if not 0 < payload.risk_ratio <= 0.05:
        raise ValueError("risk ratio must be between 0 and 5%")
    loss_per_share = payload.entry_price - payload.stop_price
    risk_budget = payload.net_asset * payload.risk_ratio
    risk_quantity = int(risk_budget / loss_per_share)
    risk_based_value = risk_quantity * payload.entry_price
    caps = {
        "risk_budget": risk_based_value,
        "script_limit": payload.net_asset * payload.script_limit,
        "market_limit": payload.net_asset * payload.market_limit,
        "single_stock_limit": payload.net_asset * payload.single_stock_limit,
        "sector_limit": payload.net_asset * payload.sector_limit,
        "liquidity_limit": payload.net_asset * payload.liquidity_limit,
    }
    binding_limit, final_value = min(caps.items(), key=lambda item: item[1])
    raw_quantity = int(final_value / payload.entry_price)
    lot_size = max(1, payload.lot_size)
    quantity = raw_quantity // lot_size * lot_size
    final_value = round(quantity * payload.entry_price, 2)
    warnings = []
    if quantity == 0:
        warnings.append("calculated quantity is below one trading lot")
    if loss_per_share / payload.entry_price > 0.1:
        warnings.append("stop distance exceeds 10%; verify the trade structure")
    return RiskPositionOut(
        risk_budget=round(risk_budget, 2), loss_per_share=round(loss_per_share, 4),
        risk_based_value=round(risk_based_value, 2), final_position_value=final_value,
        final_position_ratio=round(final_value / payload.net_asset, 4), quantity=quantity,
        binding_limit=binding_limit, caps={key: round(value, 2) for key, value in caps.items()}, warnings=warnings,
    )


ROLE_LIMITS = {
    "龙一": (0.3, 0.4),
    "龙二": (0.25, 0.4),
    "明确前排强势股": (0.2, 0.3),
    "高辨识度容量核心": (0.6, 1.0),
    "补涨股": (0.15, 0.2),
    "后排跟风股": (0.0, 0.0),
    "杂毛/无主线股": (0.0, 0.0),
}

MARKET_LIMITS = {
    "A": ("50%-80%", 0.4),
    "B": ("30%-50%", 0.3),
    "C": ("0%-30%", 0.2),
    "D": ("0%-10%", 0.1),
}


def grade_market(
    turnover_score: int,
    limit_up_count: int,
    leader_state: str,
    loss_effect: str,
    theme_persistence_days: int,
) -> MarketGradeOut:
    score = 0
    reasons: list[str] = []
    warnings: list[str] = []

    if theme_persistence_days >= 2:
        score += 25
        reasons.append("主线连续 2 天以上反复走强")
    if turnover_score >= 70:
        score += 20
        reasons.append("成交额与活跃度支持进攻")
    if limit_up_count >= 50:
        score += 20
        reasons.append("涨停家数处于较强区间")
    if leader_state in {"强势", "涨停", "断板承接"}:
        score += 25
        reasons.append("龙头状态未明显破位")
    if loss_effect in {"明显", "扩散"}:
        score -= 35
        warnings.append("亏钱效应扩散，降低总仓位")
    if leader_state in {"放量滞涨", "连续大跌", "炸板无承接"}:
        score -= 30
        warnings.append("核心标的走弱，谨慎开新仓")

    if score >= 75:
        grade = "A"
    elif score >= 50:
        grade = "B"
    elif score >= 25:
        grade = "C"
    else:
        grade = "D"

    total_limit, single_limit = MARKET_LIMITS[grade]
    return MarketGradeOut(
        grade=grade,
        total_position_limit=total_limit,
        single_position_limit=f"{int(single_limit * 100)}%以内",
        reasons=reasons or ["市场信号不足，按保守档处理"],
        risk_warnings=warnings,
    )


def run_pre_trade_check(payload: PreTradeCheckIn) -> PreTradeCheckOut:
    warnings: list[str] = []
    required_actions: list[str] = []
    score = 100

    first_position, max_position = ROLE_LIMITS.get(payload.target_role, (0.0, 0.0))
    market_max = MARKET_LIMITS.get(payload.market_grade, MARKET_LIMITS["C"])[1]
    allowed = min(max_position, market_max)

    if payload.mode == "板块共振集中进攻模式" and payload.target_role == "高辨识度容量核心":
        allowed = max_position
        required_actions.append("仓位超过 60% 前必须先填写集中进攻退出卡")

    if not payload.is_mainline:
        score -= 35
        warnings.append("不属于当前主线，默认不做")
    if payload.target_role in {"后排跟风股", "杂毛/无主线股"}:
        score -= 45
        warnings.append("标的地位不符合系统，只能排除")
    if not payload.has_sector_response:
        score -= 20
        warnings.append("板块没有资金响应，不能只靠消息或幻想交易")
    if not payload.has_volume_price_confirm:
        score -= 20
        warnings.append("量价没有确认，等待有效买点")
    if not payload.buy_point.strip():
        score -= 15
        required_actions.append("买入前写明买点类型")
    if payload.stop_loss_price <= 0 or payload.stop_loss_price >= payload.current_price:
        score -= 25
        warnings.append("止损价不可执行")
    if payload.position_ratio > allowed:
        score -= 30
        warnings.append(f"计划仓位 {payload.position_ratio:.0%} 超过当前允许上限 {allowed:.0%}")
    if payload.market_grade == "D":
        score -= 35
        warnings.append("D 档防守环境原则不开新仓")

    if score >= 80 and not warnings:
        decision = "可买"
    elif score >= 55:
        decision = "等确认"
    else:
        decision = "不买"

    return PreTradeCheckOut(
        decision=decision,
        score=max(score, 0),
        allowed_position_ratio=allowed,
        warnings=warnings,
        required_actions=required_actions,
    )


def profit_guard_price(cost_price: float, current_price: float) -> float | None:
    if cost_price <= 0:
        return None
    profit_ratio = (current_price - cost_price) / cost_price
    if profit_ratio >= 0.15:
        return round(current_price * 0.95, 2)
    if profit_ratio >= 0.10:
        return round(current_price * 0.96, 2)
    if profit_ratio >= 0.05:
        return round(current_price * 0.97, 2)
    return None
