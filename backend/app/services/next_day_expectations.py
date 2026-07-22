from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy.orm import Session

from app.api.helpers.decision import (
    EXPECTATION_DEFAULTS,
    _persist_expectation_revision,
    expectation_evidence_coverage,
)
from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.models.trading import ExpectationSnapshot, Holding, NextDayPlan, VolumePriceSnapshot
from app.services.trading_calendar import next_a_share_trading_day


def next_trading_date(value: date | None = None, *, now: datetime | None = None) -> str:
    return next_a_share_trading_day(value or shanghai_today(now)).isoformat()


def rotate_watchlist_and_generate_next_day_expectations(
    db: Session,
    *,
    completed_trade_date: str,
) -> bool:
    """Rotate the just-closed automatic pool before building next-day baselines.

    A provider may publish its final dated limit-up pool a few minutes after
    15:00.  Returning ``False`` deliberately leaves the scheduler completion
    marker unset so the next loop retries instead of freezing yesterday's
    names into today's pool and tomorrow's expectation baselines.
    """

    from app.api.routes.stocks import (
        _watchlist_generation_completed,
        _watchlist_recommendations,
    )

    if not _watchlist_generation_completed(db, completed_trade_date):
        _watchlist_recommendations(db, persist_rotation=True)
    if not _watchlist_generation_completed(db, completed_trade_date):
        # A delayed limit-up provider must not block the user's current
        # holdings from receiving their next-session scripts and baselines.
        # Keep returning False so the watchlist half is retried later.
        generate_next_day_expectations(
            db,
            completed_trade_date=completed_trade_date,
            include_watchlist=False,
        )
        return False
    generate_next_day_expectations(db, completed_trade_date=completed_trade_date)
    return True


def generate_next_day_expectations(
    db: Session,
    *,
    completed_trade_date: str | None = None,
    include_watchlist: bool = True,
) -> int:
    """Upsert baselines for holdings, plans and every active watchlist name.

    The automatic portion is already capped at ten by the nightly rotation;
    user-maintained names are deliberately retained in addition to that cap.
    """
    from app.api.routes.stocks import watchlist_recommendations

    targets: dict[str, dict] = {}
    for holding in db.query(Holding).all():
        targets[holding.code] = {"name": holding.name, "hint": holding.position_type or "持仓股", "evidence": ["来源：当前持仓"]}
    if include_watchlist:
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
        for item in [row for row in recommendations if row.code not in holding_codes]:
            is_manual = item.category == "手动自选"
            origin = "手动观察池" if is_manual else "自动观察池前10"
            targets[item.code] = {
                "name": item.name,
                "hint": "手动观察" if is_manual else ("强预期" if item.score >= 75 else "主线前排"),
                "evidence": [f"来源：{origin}；评分{item.score}，{item.theme}，{item.limit_quality}"] + item.reasons[:2],
            }

    reference_date = completed_trade_date or shanghai_today().isoformat()
    latest_volume: dict[str, VolumePriceSnapshot] = {}
    if targets:
        for row in db.query(VolumePriceSnapshot).filter(
            VolumePriceSnapshot.code.in_(targets),
            VolumePriceSnapshot.trade_date == reference_date,
        ).order_by(VolumePriceSnapshot.captured_at.desc()).all():
            latest_volume.setdefault(row.code, row)
    trade_date = next_trading_date(date.fromisoformat(reference_date))
    if targets and include_watchlist:
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
        coverage, coverage_evidence, coverage_counter = expectation_evidence_coverage(
            quote={},
            volume=volume,
            reference_trade_date=reference_date,
        )
        evidence.extend(coverage_evidence)
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
        # A close baseline is a hypothesis for the next session, not a verified
        # result.  Marking it as MATCHED here polluted hit-rate statistics and
        # made the UI claim success before auction/open evidence existed.
        row.expectation_result = "UNKNOWN"
        row.state_transition = "WAITING_VALIDATION"
        row.confidence = coverage
        row.evidence_json = json.dumps(evidence, ensure_ascii=False)
        row.counter_evidence_json = json.dumps(coverage_counter, ensure_ascii=False)
        row.suggestion = "次日先用集合竞价验证开盘区间：高于区间上沿为超预期/弱转强候选，落在区间内为符合预期，低于下沿则转弱；再用开盘5分钟、VWAP和量价承接持续修正。"
        row.created_at = shanghai_now_naive()
        db.add(row)
        db.flush()
        _persist_expectation_revision(db, row, trigger="close_baseline")
        count += 1
    # Build the ordinary holding plan in the same transaction as its close
    # baseline.  A scheduler retry can therefore never expose an expectation
    # without the matching three-branch execution script (or vice versa).
    from app.api.helpers.plan_calc import upsert_holding_next_day_plans

    upsert_holding_next_day_plans(
        db,
        completed_trade_date=reference_date,
        commit=False,
    )
    db.commit()
    return count
