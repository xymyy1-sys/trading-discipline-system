from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.api.helpers.decision import EXPECTATION_DEFAULTS
from app.models.trading import ExpectationSnapshot, Holding, NextDayPlan, VolumePriceSnapshot


def next_trading_date(value: date | None = None) -> str:
    target = (value or date.today()) + timedelta(days=1)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target.isoformat()


def generate_next_day_expectations(db: Session) -> int:
    """Upsert a closing baseline for holdings, plans, limit-up stocks and theme leaders."""
    from app.services.market_data import MarketDataProvider

    targets: dict[str, dict] = {}
    for holding in db.query(Holding).all():
        targets[holding.code] = {"name": holding.name, "hint": holding.position_type or "持仓股", "evidence": ["来源：当前持仓"]}
    for plan in db.query(NextDayPlan).order_by(NextDayPlan.updated_at.desc()).limit(500).all():
        targets.setdefault(plan.code, {"name": plan.name, "hint": plan.holding_category or plan.plan_type or "计划股", "evidence": ["来源：次日计划"]})
    provider = MarketDataProvider()
    try:
        ladder = provider.limit_up_ladder()
    except Exception:
        ladder = None
    for group in (ladder.groups if ladder else []):
        for stock in group.stocks:
            hint = "强预期" if stock.consecutive_limit_days >= 2 and stock.break_count == 0 else "修复" if stock.break_count >= 2 else "主线前排"
            targets[stock.code] = {
                "name": stock.name, "hint": hint,
                "evidence": [f"收盘封板：{stock.consecutive_limit_days}板、封单{stock.sealed_amount:.2f}亿、炸板{stock.break_count}次、换手{stock.turnover:.1f}%"],
            }
    try:
        radar = provider.theme_radar()
    except Exception:
        radar = None
    for theme in (radar.themes[:20] if radar else []):
        for stock in theme.core_stocks:
            if stock.code:
                targets.setdefault(stock.code, {"name": stock.name, "hint": "主线前排", "evidence": [f"{theme.name}排名第{theme.rank}，强度{theme.score}分"]})

    latest_volume: dict[str, VolumePriceSnapshot] = {}
    if targets:
        for row in db.query(VolumePriceSnapshot).filter(VolumePriceSnapshot.code.in_(targets)).order_by(VolumePriceSnapshot.captured_at.desc()).all():
            latest_volume.setdefault(row.code, row)
    trade_date = next_trading_date()
    count = 0
    for code, target in targets.items():
        hint = str(target["hint"])
        base = "STRONG" if any(word in hint for word in ("强预期", "主线前排", "打板")) else "REPAIR" if "修复" in hint else "NEUTRAL"
        volume = latest_volume.get(code)
        evidence = list(target["evidence"])
        if volume:
            evidence.append(f"收盘量价：{volume.pattern}，涨幅{volume.change_pct:+.2f}%，高点回撤{volume.high_drawdown:.2f}%")
            if volume.pattern in {"冲高回落跌破VWAP", "跌破VWAP"} and base == "STRONG":
                base = "REPAIR"
        low, high = EXPECTATION_DEFAULTS[base]
        row = db.query(ExpectationSnapshot).filter(
            ExpectationSnapshot.trade_date == trade_date,
            ExpectationSnapshot.code == code,
            ExpectationSnapshot.stage == "次日盘前预期",
        ).first()
        if row is None:
            row = ExpectationSnapshot(trade_date=trade_date, code=code, name=str(target["name"]), stage="次日盘前预期")
        row.base_expectation = base
        row.expected_open_low = low
        row.expected_open_high = high
        row.outperform_threshold = high + 1
        row.underperform_threshold = low - 1
        row.severe_underperform_threshold = min(low - 3, -3)
        row.actual_open_pct = 0
        row.actual_change_pct = 0
        row.expectation_gap_score = 0
        row.expectation_result = "MATCHED"
        row.state_transition = "WAITING_VALIDATION"
        row.confidence = 0.78 if volume and volume.vwap_reliable else 0.62
        row.evidence_json = json.dumps(evidence, ensure_ascii=False)
        row.counter_evidence_json = "[]"
        row.suggestion = "次日先用集合竞价验证开盘区间，再以开盘5分钟、VWAP和量价变化持续修正预期差。"
        row.created_at = datetime.now()
        db.add(row)
        count += 1
    db.commit()
    return count
