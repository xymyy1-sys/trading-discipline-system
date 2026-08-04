from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.trading import (
    SimulationAccount,
    SimulationClosedTrade,
    SimulationDailyEquity,
    SimulationShadowDecision,
)


@dataclass(frozen=True)
class SimulationRiskGuard:
    state: str
    position_multiplier: float
    block_new_entries: bool
    reason: str
    drawdown_pct: float
    daily_loss_pct: float
    consecutive_formal_losses: int


def evaluate_risk_guard(
    *,
    drawdown_pct: float,
    daily_loss_pct: float,
    consecutive_formal_losses: int,
    last_formal_loss_at: datetime | None,
    evaluated_at: datetime,
) -> SimulationRiskGuard:
    drawdown = abs(min(drawdown_pct, 0.0))
    daily_loss = abs(min(daily_loss_pct, 0.0))
    cooldown_active = bool(
        last_formal_loss_at
        and evaluated_at - last_formal_loss_at < timedelta(hours=24)
    )
    if drawdown >= 8.0:
        return SimulationRiskGuard(
            "STOPPED", 0.0, True,
            f"账户最大回撤已达{drawdown:.2f}%，超过8%停机线；只允许退出，不再开仓。",
            drawdown, daily_loss, consecutive_formal_losses,
        )
    if daily_loss >= 3.0:
        return SimulationRiskGuard(
            "DAILY_STOP", 0.0, True,
            f"当日权益亏损已达{daily_loss:.2f}%，超过3%日内停机线；当日只允许退出。",
            drawdown, daily_loss, consecutive_formal_losses,
        )
    if consecutive_formal_losses >= 4 and cooldown_active:
        return SimulationRiskGuard(
            "LOSS_STREAK_STOP", 0.0, True,
            f"正式策略连续亏损{consecutive_formal_losses}笔，进入24小时冷静期；只允许退出。",
            drawdown, daily_loss, consecutive_formal_losses,
        )
    if drawdown >= 5.0 or consecutive_formal_losses >= 2:
        triggers = []
        if drawdown >= 5.0:
            triggers.append(f"回撤{drawdown:.2f}%")
        if consecutive_formal_losses >= 2:
            triggers.append(f"正式策略连续亏损{consecutive_formal_losses}笔")
        return SimulationRiskGuard(
            "DE_RISK", 0.5, False,
            "、".join(triggers) + "，新开仓位减半，退出纪律不变。",
            drawdown, daily_loss, consecutive_formal_losses,
        )
    return SimulationRiskGuard(
        "NORMAL", 1.0, False, "风险预算正常，按证据与失效距离决定仓位。",
        drawdown, daily_loss, consecutive_formal_losses,
    )


def account_risk_guard(
    db: Session,
    account: SimulationAccount,
    evaluated_at: datetime,
) -> SimulationRiskGuard:
    equities = db.query(SimulationDailyEquity).filter(
        SimulationDailyEquity.account_id == account.id,
    ).order_by(SimulationDailyEquity.trade_date.desc()).all()
    drawdown_pct = min((float(row.drawdown_pct or 0) for row in equities), default=0.0)
    latest = equities[0] if equities else None
    daily_loss_pct = (
        float(latest.daily_pnl or 0) / max(float(latest.total_equity or 0) - float(latest.daily_pnl or 0), 1) * 100
        if latest and latest.trade_date == evaluated_at.date().isoformat()
        else 0.0
    )
    closed = db.query(SimulationClosedTrade).filter(
        SimulationClosedTrade.account_id == account.id,
    ).order_by(SimulationClosedTrade.closed_at.desc(), SimulationClosedTrade.id.desc()).limit(20).all()
    order_ids = [int(row.entry_order_id) for row in closed if row.entry_order_id]
    decisions = {
        int(row.order_id): row
        for row in db.query(SimulationShadowDecision).filter(
            SimulationShadowDecision.account_id == account.id,
            SimulationShadowDecision.order_id.in_(order_ids or [0]),
        ).all()
        if row.order_id is not None
    }
    consecutive_losses = 0
    last_loss_at: datetime | None = None
    for row in closed:
        decision = decisions.get(int(row.entry_order_id or 0))
        if decision is None or decision.source_kind == "autonomous_exploration_sample":
            continue
        if float(row.realized_pnl or 0) >= 0:
            break
        consecutive_losses += 1
        if last_loss_at is None:
            last_loss_at = row.closed_at
    return evaluate_risk_guard(
        drawdown_pct=drawdown_pct,
        daily_loss_pct=daily_loss_pct,
        consecutive_formal_losses=consecutive_losses,
        last_formal_loss_at=last_loss_at,
        evaluated_at=evaluated_at,
    )
