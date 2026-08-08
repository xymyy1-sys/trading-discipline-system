from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.api.helpers.decision import (
    EXPECTATION_DEFAULTS,
    _persist_expectation_revision,
    expectation_evidence_coverage,
)
from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.models.trading import (
    ExpectationSnapshot,
    Holding,
    NextDayPlan,
    SimulationAccount,
    SimulationPosition,
    VolumePriceSnapshot,
)
from app.services.trading_calendar import next_a_share_trading_day


def _rank_limit_up_plan_candidates(
    stocks: dict[str, Any],
    atmosphere: Any,
    promotion_by_code: dict[str, dict[str, Any]],
    *,
    maximum_plans: int,
) -> list[dict[str, Any]]:
    """Rank next-session board candidates using the active promotion model.

    The old implementation chose eligible names mainly by board height and
    role score, while the learned k -> k+1 probability was only displayed
    after selection.  That made the visible shortlist inconsistent with the
    probability ledger.  The promotion probability and same-level rank are
    now the primary ordering evidence after the hard position gate.
    """
    context_by_code: dict[str, dict[str, Any]] = {}
    for theme in atmosphere.theme_ladders:
        for role in theme.identity_roles:
            role_cap = float(role.max_position_ratio or 0)
            candidate = {
                "code": role.code,
                "theme": theme,
                "role": role,
                "eligible": role_cap > 0,
                "role_score": float(role.role_score or 0),
                "board_level": int(role.level or 0),
            }
            previous = context_by_code.get(role.code)
            if previous is None or (
                int(candidate["eligible"]), candidate["role_score"], candidate["board_level"]
            ) > (
                int(previous["eligible"]), previous["role_score"], previous["board_level"]
            ):
                context_by_code[role.code] = candidate

    rows: list[dict[str, Any]] = []
    for code, stock in stocks.items():
        # Keep the board plan universe consistent with the user's executable
        # market boundary: Shanghai/Shenzhen main-board A shares only.
        if not re.fullmatch(r"(?:600|601|603|605|000|001|002|003)\d{3}", str(code)):
            continue
        context = context_by_code.get(code, {})
        promotion = promotion_by_code.get(code) or {}
        probability = float(promotion.get("probability") or 0)
        same_level_rank = int(promotion.get("same_level_rank") or 9999)
        row = {
            "code": code,
            "theme": context.get("theme"),
            "role": context.get("role"),
            "eligible": bool(context.get("eligible", False)),
            "probability": probability,
            "same_level_rank": same_level_rank,
            "role_score": float(context.get("role_score") or 0),
            "board_level": max(1, int(getattr(stock, "consecutive_limit_days", 1) or 1)),
        }
        rows.append(row)

    rows.sort(key=lambda row: (
        int(row["eligible"]),
        row["probability"],
        -row["same_level_rank"],
        row["role_score"],
        row["board_level"],
    ), reverse=True)
    selected = rows[:max(1, maximum_plans)]
    for rank, row in enumerate(selected, start=1):
        row["selection_rank"] = rank
        gate = "具备题材身份与试错仓资格" if row["eligible"] else "仅观察，尚未通过题材身份仓位闸门"
        row["selection_reason"] = (
            f"自动候选第{rank}名：{row['board_level']}进{row['board_level'] + 1}概率"
            f"{row['probability']:.1f}%，同身位第{row['same_level_rank']}；{gate}。"
        )
    return selected


def generate_automatic_limit_up_plans(
    db: Session,
    *,
    completed_trade_date: str,
    maximum_plans: int = 12,
) -> int:
    """Turn the audited closing limit-up ladder into next-session plans.

    Manual clicks remain available, but the paper trader must not depend on a
    human remembering to create tomorrow's plan.  Every selected row keeps the
    ladder/theme/identity provenance in ``auction_plan``; ineligible high-board
    names are retained as observation plans with a zero position ceiling.
    """
    from app.api.helpers.plan_calc import _limit_up_next_day_plan
    from app.schemas.trading import LimitUpPlanCreate
    from app.services.market_data import MarketDataProvider, _is_valid_limit_up_ladder

    provider = MarketDataProvider()
    ladder = provider.limit_up_ladder(completed_trade_date, force_refresh=False)
    if not _is_valid_limit_up_ladder(ladder) or ladder.trade_date != completed_trade_date:
        return 0
    atmosphere = provider.limit_up_atmosphere(completed_trade_date, force_refresh=False)
    from app.services.limit_up_promotion import record_closing_promotion_cohort

    promotion_by_code = record_closing_promotion_cohort(
        db,
        completed_trade_date=completed_trade_date,
        ladder=ladder,
        atmosphere=atmosphere,
    )
    stocks = {
        stock.code: stock
        for group in ladder.groups
        for stock in group.stocks
    }
    selected = _rank_limit_up_plan_candidates(
        stocks,
        atmosphere,
        promotion_by_code,
        maximum_plans=maximum_plans,
    )
    plan_date = next_trading_date(date.fromisoformat(completed_trade_date))
    selected_codes: set[str] = set()
    created = 0
    for selection in selected:
        code = str(selection["code"])
        theme = selection.get("theme")
        role = selection.get("role")
        stock = stocks.get(code)
        if stock is None:
            continue
        selected_codes.add(code)
        existing = db.query(NextDayPlan).filter(
            NextDayPlan.plan_date == plan_date,
            NextDayPlan.plan_type == "limit_up_auction",
            NextDayPlan.code == code,
        ).first()
        if existing is not None:
            existing_auction = json.loads(existing.auction_plan or "{}")
            if existing_auction.get("auto_generated") is not True:
                # A manually edited plan is authoritative for the same code
                # and date.  Keep it in the selected set so rotation cannot
                # delete it, but never overwrite the user's scenario.
                continue
        theme_name = str(getattr(theme, "name", "") or "")
        concepts = list(stock.concepts or [])
        if theme_name and theme_name not in concepts:
            concepts.insert(0, theme_name)
        role_cap = float(getattr(role, "max_position_ratio", 0) or 0)
        board_level = max(1, int(stock.consecutive_limit_days or 1))
        stage_trial_cap = 0.15 if board_level == 1 else 0.12 if board_level == 2 else 0.08
        effective_cap = min(role_cap, stage_trial_cap) if role_cap > 0 else 0.0
        roles = list(getattr(role, "roles", []) or [])
        expectation = (
            f"系统盘后自动预案：{stock.consecutive_limit_days}板；"
            f"题材={theme_name or stock.industry or '待验证'}；"
            f"身份={'/'.join(roles) or '高标观察'}。次日竞价与开盘承接不通过则自动失效。"
        )
        payload = LimitUpPlanCreate(
            code=stock.code,
            name=stock.name,
            price=float(stock.price or 0),
            level=board_level,
            industry=stock.industry,
            concepts=concepts,
            sealed_amount=float(stock.sealed_amount or 0),
            amount=float(stock.amount or 0),
            turnover=float(stock.turnover or 0),
            break_count=int(stock.break_count or 0),
            first_limit_time=stock.first_limit_time,
            last_limit_time=stock.last_limit_time,
            expectation=expectation,
            max_position_ratio=effective_cap,
        )
        plan = _limit_up_next_day_plan(payload, plan_date, existing)
        auction = json.loads(plan.auction_plan or "{}")
        auction.update({
            "auto_generated": True,
            "generation_source": "盘后真实涨停天梯+题材身份竞争",
            "source_trade_date": completed_trade_date,
            "source_ladder": ladder.source,
            "source_atmosphere": atmosphere.source,
            "promotion_model_version": str((promotion_by_code.get(code) or {}).get("champion_model") or "promotion-v1"),
            "auto_selection_rank": selection["selection_rank"],
            "auto_selection_pool_size": len(stocks),
            "auto_selection_reason": selection["selection_reason"],
            "auto_selection_basis": "仓位资格→本级晋级概率→同身位排名→题材身份分→连板高度",
        })
        promotion = promotion_by_code.get(code) or {}
        if promotion:
            auction.update({
                "promotion_transition": promotion.get("transition", ""),
                "promotion_probability": promotion.get("probability"),
                "promotion_confidence_low": promotion.get("confidence_low"),
                "promotion_confidence_high": promotion.get("confidence_high"),
                "promotion_sample_count": promotion.get("historical_sample_count", 0),
                "promotion_promoted_count": promotion.get("historical_promoted_count", 0),
                "same_level_rank": promotion.get("same_level_rank"),
                "same_level_count": promotion.get("same_level_count"),
                "promotion_evidence": promotion.get("probability_evidence", []),
                "live_promotion_probability": promotion.get("probability"),
                "promotion_trial_position_ratio": effective_cap,
                "promotion_role_position_ratio": role_cap,
                "promotion_position_rule": "逐级独立、小仓试错；晋级失败不补仓，赢家由后续真实晋级自然保留。",
            })
        plan.auction_plan = json.dumps(auction, ensure_ascii=False)
        if existing is None:
            db.add(plan)
        created += 1

    # Rotate only plans previously owned by this automatic job.  User-created
    # plans are never removed by the scheduler.
    for stale in db.query(NextDayPlan).filter(
        NextDayPlan.plan_date == plan_date,
        NextDayPlan.plan_type == "limit_up_auction",
    ).all():
        payload = json.loads(stale.auction_plan or "{}")
        if payload.get("auto_generated") is True and stale.code not in selected_codes:
            db.delete(stale)
    db.commit()
    return created


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
        calibrate_watchlist_recommendations,
        _watchlist_generation_completed,
        _watchlist_recommendations,
    )

    try:
        calibrate_watchlist_recommendations(
            db,
            outcome_trade_date=completed_trade_date,
            force_refresh=False,
            persist=True,
        )
    except Exception:
        # Calibration is a feedback report.  It must never block the core
        # nightly rotation and next-session expectation baselines.
        db.rollback()

    # The closing limit-up ladder is an independent source.  Generate its
    # next-session plans even when the automatic watchlist provider is late;
    # otherwise a delayed watchlist would also leave the early board window
    # without a plan on the following morning.
    try:
        generate_automatic_limit_up_plans(db, completed_trade_date=completed_trade_date)
    except Exception:
        # A temporarily unavailable ladder must not suppress holding scripts.
        # The close scheduler will retry the automatic board plan later.
        db.rollback()

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
    # Keep the pool at its existing size: replace at most three low-ranked
    # automatic names with independently scanned candidates whose ranking is
    # also adjusted by completed forward-paper results. Manual names remain
    # untouched and intraday user deletions are never refilled here.
    try:
        from app.services.autonomous_selection import merge_autonomous_candidates_into_watchlist

        merge_autonomous_candidates_into_watchlist(db, completed_trade_date)
    except Exception:
        db.rollback()
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
    shadow_account_ids = [
        row[0] for row in db.query(SimulationAccount.id).filter(
            SimulationAccount.status == "active",
            SimulationAccount.account_type == "shadow",
            SimulationAccount.automation_key.is_not(None),
        ).all()
    ]
    if shadow_account_ids:
        for position in db.query(SimulationPosition).filter(
            SimulationPosition.account_id.in_(shadow_account_ids),
            SimulationPosition.quantity > 0,
        ).all():
            targets.setdefault(position.code, {
                "name": position.name,
                "hint": "AI模拟持仓",
                "evidence": ["来源：AI前向模拟持仓；次日按竞价、开盘5分钟和VWAP逐级验证"],
            })
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
