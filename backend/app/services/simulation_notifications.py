from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    DataCaptureSnapshot,
    ExpectationSnapshot,
    SimulationAccount,
    SimulationDailyEquity,
    SimulationFill,
    SimulationOrder,
    SimulationPosition,
    SimulationShadowDecision,
)
from app.services.dingtalk import dingtalk_status, send_dingtalk_markdown
from app.services.simulation_shadow import AI_TRADER_AUTOMATION_KEY
from app.services.simulation_learning import build_ai_trader_daily_learning
from app.services.trading_calendar import next_a_share_trading_day


TYPE = "ai_trader_notification"


def _sent(db: Session, key: str) -> bool:
    return db.query(DataCaptureSnapshot.id).filter(
        DataCaptureSnapshot.data_type == TYPE,
        DataCaptureSnapshot.target_code == key,
        DataCaptureSnapshot.status == "sent",
    ).first() is not None


def _record(db: Session, key: str, title: str, payload: dict, now: datetime) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    db.add(DataCaptureSnapshot(
        trade_date=now.date().isoformat(),
        captured_at=now,
        source="dingtalk",
        data_type=TYPE,
        target_code=key,
        target_name=title[:64],
        raw_value_json=encoded,
        normalized_value_json=encoded,
        quality="delivered",
        is_complete=True,
        status="sent",
        raw_payload_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    ))
    db.commit()


def notify_ai_trader_fills(db: Session, *, now: datetime | None = None) -> int:
    """Notify every newly matched virtual fill exactly once."""
    status = dingtalk_status()
    if not status.get("enabled") or not status.get("configured"):
        return 0
    evaluated_at = shanghai_now_naive(now)
    account = db.query(SimulationAccount).filter(SimulationAccount.automation_key == AI_TRADER_AUTOMATION_KEY).first()
    if account is None:
        return 0
    sent = 0
    fills = db.query(SimulationFill).filter(
        SimulationFill.account_id == account.id,
    ).order_by(SimulationFill.filled_at.asc(), SimulationFill.id.asc()).all()
    for fill in fills:
        key = f"fill:{fill.id}"
        if _sent(db, key):
            continue
        order = db.get(SimulationOrder, fill.order_id)
        decision = db.query(SimulationShadowDecision).filter(
            SimulationShadowDecision.order_id == fill.order_id,
        ).first()
        action = "虚拟买入" if fill.side == "BUY" else "虚拟卖出"
        reason = decision.reason if decision else (order.client_note if order else "成交记录缺少关联决策说明")
        follow_up = (
            "下一采样继续验证分时均价、主动买卖额和市场风险闸门；失效则撤销乐观预期，不追涨加仓。"
            if fill.side == "BUY"
            else "保留卖出后的价格轨迹，与不操作结果对照；若快速修复也记录为卖点改进样本。"
        )
        title = f"AI模拟盘{action}：{fill.name}"
        text = (
            f"### {title}\n\n"
            f"- 标的：{fill.name}（{fill.code}）\n"
            f"- 成交：{fill.quantity}股 × {fill.price:.2f}元，金额{fill.gross_amount:.2f}元\n"
            f"- 策略来源：{fill.strategy_source}\n"
            f"- 交易理由：{reason}\n"
            f"- 后续计划：{follow_up}\n\n"
            f"> 这是2万元模拟账户的虚拟成交，不是实盘委托或收益承诺。"
        )
        send_dingtalk_markdown(title, text)
        _record(db, key, title, {"fill_id": fill.id, "reason": reason, "follow_up": follow_up}, evaluated_at)
        sent += 1
    return sent


def notify_ai_trader_close_review(db: Session, trade_date: str, *, now: datetime | None = None) -> bool:
    """Send one concise end-of-day account/strategy review."""
    status = dingtalk_status()
    if not status.get("enabled") or not status.get("configured"):
        return False
    account = db.query(SimulationAccount).filter(SimulationAccount.automation_key == AI_TRADER_AUTOMATION_KEY).first()
    if account is None:
        return False
    key = f"close:{trade_date}"
    if _sent(db, key):
        return False
    equity = db.query(SimulationDailyEquity).filter(
        SimulationDailyEquity.account_id == account.id,
        SimulationDailyEquity.trade_date == trade_date,
    ).first()
    if equity is None:
        return False
    positions = db.query(SimulationPosition).filter(
        SimulationPosition.account_id == account.id,
        SimulationPosition.quantity > 0,
    ).order_by(SimulationPosition.market_value.desc()).all()
    fills = db.query(SimulationFill).filter(
        SimulationFill.account_id == account.id,
        SimulationFill.trade_date == trade_date,
    ).all()
    evaluated_at = shanghai_now_naive(now)
    learning = build_ai_trader_daily_learning(
        db,
        account,
        trade_date,
        now=evaluated_at,
    )
    position_text = "；".join(f"{row.name}{row.quantity}股" for row in positions) or "空仓"
    next_date = next_a_share_trading_day(date.fromisoformat(trade_date)).isoformat()
    expectations = {
        row.code: row for row in db.query(ExpectationSnapshot).filter(
            ExpectationSnapshot.trade_date == next_date,
            ExpectationSnapshot.stage == "次日盘前预期",
            ExpectationSnapshot.code.in_([row.code for row in positions] or ["__none__"]),
        ).all()
    }
    position_plan_lines = []
    for position in positions:
        expectation = expectations.get(position.code)
        if expectation is None:
            position_plan_lines.append(
                f"- {position.name}：次日预期尚未形成，开盘前禁止生成确定性买卖结论。"
            )
            continue
        base_labels = {"STRONG": "强势延续", "REPAIR": "修复", "WEAK": "偏弱", "NEUTRAL": "中性"}
        base = base_labels.get(expectation.base_expectation, expectation.base_expectation or "待验证")
        position_plan_lines.append(
            f"- {position.name}：基础预期{base}，合理开盘"
            f"{expectation.expected_open_low:+.1f}%～{expectation.expected_open_high:+.1f}%；"
            "竞价选分支后，继续用开盘5分钟与VWAP验证，证伪才降级。"
        )
    plan_text = "\n".join(position_plan_lines) or "- 当前空仓，次日不为凑交易而开仓。"
    grade = "A" if equity.daily_pnl > 0 and equity.drawdown_pct <= 2 else "B" if equity.drawdown_pct <= 4 else "C"
    corrections = list(learning.get("corrections") or [])[:3]
    correction_text = "\n".join(f"- {item}" for item in corrections) or "- 今日没有形成足以修改规则的新证据，继续保留原规则采样。"
    title = f"AI模拟盘{trade_date}收盘复盘"
    text = (
        f"### {title}\n\n"
        f"- 总资产：{equity.total_equity:.2f}元；当日盈亏：{equity.daily_pnl:+.2f}元\n"
        f"- 累计收益：{equity.return_pct:+.2f}%；回撤：{equity.drawdown_pct:.2f}%\n"
        f"- 当日虚拟成交：{len(fills)}笔；收盘持仓：{position_text}\n"
        f"- 当前策略评级：{grade}\n"
        f"- 下一交易日：{next_date}\n\n"
        f"#### 明日持仓预期\n{plan_text}\n\n"
        f"#### 今日模型学习\n"
        f"- 已写入可计算成交样本：{learning.get('evaluated_sample_count', 0)}笔；"
        f"正式闭环样本：{learning.get('formal_closed_sample_count', 0)}/30。\n"
        f"{correction_text}\n\n"
        f"- 校准纪律：逐笔保存最大有利/不利波动及不操作对照；样本不足时只记账，不自动迎合单日结果改参。\n\n"
        f"> 全部为前向模拟记录，不回填历史成交。"
    )
    send_dingtalk_markdown(title, text)
    _record(db, key, title, {
        "equity_id": equity.id,
        "fill_count": len(fills),
        "grade": grade,
        "learning_sample_count": learning.get("evaluated_sample_count", 0),
        "formal_closed_sample_count": learning.get("formal_closed_sample_count", 0),
        "corrections": corrections,
    }, evaluated_at)
    return True
