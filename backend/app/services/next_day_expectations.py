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
    """Upsert baselines only for current holdings and the top ten automatic watchlist names."""
    from app.api.routes.stocks import watchlist_recommendations

    targets: dict[str, dict] = {}
    for holding in db.query(Holding).all():
        targets[holding.code] = {"name": holding.name, "hint": holding.position_type or "持仓股", "evidence": ["来源：当前持仓"]}
    for plan in db.query(NextDayPlan).filter(NextDayPlan.plan_type == "limit_up_auction").all():
        targets.setdefault(plan.code, {
            "name": plan.name,
            "hint": "打板预案",
            "evidence": ["来源：有效打板预案；次日必须经集合竞价与开盘承接验证"],
        })
    try:
        recommendations = watchlist_recommendations(db)
    except Exception:
        recommendations = []
    holding_codes = set(targets)
    for item in [row for row in recommendations if row.code not in holding_codes][:10]:
        targets[item.code] = {
            "name": item.name, "hint": "强预期" if item.score >= 75 else "主线前排",
            "evidence": [f"来源：自动观察池前10；评分{item.score}，{item.theme}，{item.limit_quality}"] + item.reasons[:2],
        }

    latest_volume: dict[str, VolumePriceSnapshot] = {}
    if targets:
        for row in db.query(VolumePriceSnapshot).filter(VolumePriceSnapshot.code.in_(targets)).order_by(VolumePriceSnapshot.captured_at.desc()).all():
            latest_volume.setdefault(row.code, row)
    trade_date = next_trading_date()
    if targets:
        db.query(ExpectationSnapshot).filter(
            ExpectationSnapshot.trade_date == trade_date,
            ExpectationSnapshot.stage == "次日盘前预期",
            ~ExpectationSnapshot.code.in_(list(targets)),
        ).delete(synchronize_session=False)
    count = 0
    for code, target in targets.items():
        hint = str(target["hint"])
        base = "STRONG" if any(word in hint for word in ("强预期", "主线前排", "打板")) else "REPAIR" if "修复" in hint else "NEUTRAL"
        volume = latest_volume.get(code)
        evidence = list(target["evidence"])
        if volume:
            evidence.append(f"收盘量价：{volume.pattern}，涨幅{volume.change_pct:+.2f}%，高点回撤{volume.high_drawdown:.2f}%")
            weak_close = volume.price_vs_vwap <= -1 or volume.high_drawdown >= 4
            if volume.pattern in {"冲高回落跌破VWAP", "跌破VWAP"} or weak_close:
                base = "WEAK" if volume.change_pct <= -5 else "REPAIR"
                evidence.append("收盘承接偏弱，基础预期改为次日修复，不把反弹直接当作反转。")
            elif volume.price_vs_vwap >= 1 and volume.change_pct >= 3 and volume.high_drawdown < 3:
                base = "STRONG"
                evidence.append("收盘位于分时均价上方且回撤受控，次日按强势延续验证。")
        low, high = EXPECTATION_DEFAULTS[base]
        if base == "REPAIR" and volume:
            low, high = (-3.0, 0.5) if volume.high_drawdown >= 5 else (-2.0, 1.5)
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
        row.suggestion = "次日先用集合竞价验证开盘区间：高于区间上沿为超预期/弱转强候选，落在区间内为符合预期，低于下沿则转弱；再用开盘5分钟、VWAP和量价承接持续修正。"
        row.created_at = datetime.now()
        db.add(row)
        count += 1
    db.commit()
    return count
