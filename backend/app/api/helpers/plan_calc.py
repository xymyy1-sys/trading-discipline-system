import json
from datetime import date, datetime, time
from typing import Any
from sqlalchemy.orm import Session
from app.models.trading import ExpectationSnapshot, Holding, NextDayPlan, VolumePriceSnapshot
from app.schemas.trading import ClassificationBasis, NextDayPlanOut, NextDayPlanCreate, LimitUpPlanCreate, AuctionPlan
from app.services.market_data import _get_current_theme_radar_cache, _last_trading_day
from app.services.cache import _get_response_cache
from app.api.helpers.quotes import (
    _safe_float,
    _safe_turnover,
    _quote_code_candidates,
    _normalize_code,
    _latest_quote_for_holding,
    _daily_history_metrics,
    _next_limit_up_price,
    _is_realtime_note,
    _json_obj,
    _json_list
)
from app.api.helpers.holdings_calc import _account_total_asset, _refresh_holding_prices
from app.core.trading_clock import shanghai_now_naive, shanghai_today
from app.services.trading_calendar import is_a_share_trading_day, next_a_share_trading_day
from app.api.helpers.seesaw import (
    _cached_holding_theme_flow_profile,
    _holding_theme_profile,
    _holding_sector_keywords,
    _intraday_sell_triggers
)

_CATEGORY_RISK_PRIORITY = {
    "弱于预期": 1,
    "分歧转弱": 2,
    "弱转强": 3,
    "符合预期": 4,
    "强预期": 5,
    "超预期": 6,
    "低价情绪股": 1,
    "高位巨量分歧股": 2,
    "弱于预期股": 3,
    "震荡趋势股": 4,
    "主线前排股": 5,
    "非主线观察股": 2,
}

_FORBIDDEN_BY_CATEGORY = {
    "弱于预期": ["不补仓", "反抽优先减仓", "不默认接回"],
    "分歧转弱": ["不追高", "不扩大做T风险", "先确认承接"],
    "弱转强": ["不抢第一笔翻红", "不无条件买回"],
    "符合预期": ["不追高", "不脱离计划做T"],
    "强预期": ["不因小波动丢核心仓", "不机械做T"],
    "超预期": ["不追最高点", "不临盘扩大仓位"],
    "低价情绪股": ["不追高", "不补仓", "不接回", "不新增风险"],
    "高位巨量分歧股": ["不追高", "不补仓", "不扩大做T风险"],
    "弱于预期股": ["不补仓", "不默认接回", "反抽优先减仓"],
    "震荡趋势股": ["不追高", "不无条件买回"],
    "主线前排股": ["不机械做T", "不因小波动丢核心仓"],
    "非主线观察股": ["不因单只涨停开仓", "不追后排", "主线与阶段未确认前仓位为0"],
}

def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword and keyword in text for keyword in keywords)

def _estimated_vwap(quote: dict[str, Any]) -> float:
    for key in ("vwap", "avg_price", "average_price"):
        value = _safe_float(quote.get(key))
        if value:
            return value
    price = _safe_float(quote.get("price"))
    open_price = _safe_float(quote.get("open"))
    high = _safe_float(quote.get("high"))
    low = _safe_float(quote.get("low"))
    samples = [item for item in (open_price, high, low, price) if item]
    return round(sum(samples) / len(samples), 2) if samples else 0.0

def _next_trade_date(value: date | None = None) -> str:
    """Return the next real A-share session, including exchange holidays."""

    return next_a_share_trading_day(value or shanghai_today()).isoformat()


def resolve_default_plan_date(db: Session, now: datetime | None = None) -> str:
    """Resolve the plan currently being executed rather than always tomorrow."""

    current = shanghai_now_naive(now)
    today = current.date().isoformat()
    before_close_roll = (current.hour, current.minute) < (15, 5)
    if is_a_share_trading_day(current.date()) and before_close_roll:
        exists = db.query(NextDayPlan.id).filter(NextDayPlan.plan_date == today).first()
        if exists is not None:
            return today
    return _next_trade_date(current.date())

def _default_next_day_plan(
    holding: Holding,
    plan_date: str,
    account_total_asset: float | None = None,
    quote: dict[str, Any] | None = None,
) -> NextDayPlan:
    quote = quote or {}
    reference_price = _safe_float(quote.get("price")) or holding.current_price
    total_asset = account_total_asset if account_total_asset is not None else holding.total_asset
    position_ratio = (
        holding.quantity * reference_price / total_asset
        if total_asset
        else 0.0
    )
    evidence = _holding_market_evidence(holding, quote)
    category = _infer_expectation_category(holding, evidence, quote)
    basis = ClassificationBasis(
        sector=evidence.get("sector") or "待盘后确认",
        mainline_position=evidence.get("mainline_position") or "待识别是否为主线前排",
        fund_flow=evidence.get("fund_flow") or "待结合订单流方向证据页确认",
        amount=evidence.get("amount") or "待补成交额",
        turnover=evidence.get("turnover") or "待补换手率",
        trend="；".join(
            item
            for item in [
                evidence.get("intraday"),
                evidence.get("trend") or "按确认位、支撑位、压力位复核",
            ]
            if item
        ),
        support=str(round(reference_price * 0.97, 2)),
        pressure=str(round(reference_price * 1.04, 2)),
        weaker_than_sector=bool(evidence.get("weaker_than_sector")),
    )
    dynamic_plan = _dynamic_holding_auction_plan(holding, category, evidence, quote)
    trim_quantity = max(0, holding.quantity // 3)
    plan = NextDayPlan(
        plan_date=plan_date,
        plan_type="holding",
        holding_id=holding.id,
        code=holding.code,
        name=holding.name,
        quantity=holding.quantity,
        cost_price=holding.cost_price,
        current_price=reference_price,
        position_ratio=round(position_ratio, 4),
        holding_category=category,
        classification_basis=basis.model_dump_json(),
        outperform_condition=_outperform_condition(category),
        outperform_action=_outperform_action(category),
        expected_condition=_expected_condition(category),
        expected_action=_expected_action(category),
        underperform_condition=_underperform_condition(category),
        underperform_action=_underperform_action(category),
        confirm_price=round(max(reference_price, holding.cost_price), 2),
        trim_price=round(max(reference_price * 1.03, holding.cost_price * 1.04), 2),
        trim_condition=(
            "到达计划兑现位/早盘有效高点后，只有放量滞涨或跌破VWAP、板块订单流方向走弱、"
            "高点回撤、利润保护中的至少两类证据共振，才分批兑现25%-50%；重新站稳VWAP并突破高点则取消。"
        ),
        trim_quantity=trim_quantity,
        allow_buyback=category in {"符合预期", "弱转强"},
        buyback_price=round(reference_price * 0.97, 2),
        buyback_condition=(
            "禁止把不恐慌卖出等同于抄底。仅当全市场扩仓闸门开放、板块订单流方向转强、"
            "个股V形/低点抬高并站回真实VWAP、到兑现位风险收益比不低于1.5时，才允许评估小仓试错。"
        ),
        max_buyback_quantity=trim_quantity if category in {"符合预期", "弱转强"} else 0,
        reduce_price=round(max(reference_price * 0.97, holding.cost_price * 0.98), 2),
        final_risk_price=round(max(reference_price * 0.94, holding.cost_price * 0.96), 2),
        stop_loss_4pct=round(holding.cost_price * 0.96, 2),
        auction_plan=json.dumps(dynamic_plan, ensure_ascii=False),
        forbidden_actions=json.dumps(list(dict.fromkeys([
            *_FORBIDDEN_BY_CATEGORY.get(category, []),
            "冲高后未出现多证据走弱时不猜顶清仓",
            "日内极低位且未触发固定硬止损时不恐慌追卖",
            "不恐慌卖出不等于允许逆势补仓",
        ])), ensure_ascii=False),
    )
    _refresh_plan_risk(plan)
    return plan

def _sync_holding_plan(existing: NextDayPlan, fresh: NextDayPlan) -> None:
    existing.holding_id = fresh.holding_id
    existing.quantity = fresh.quantity
    existing.cost_price = fresh.cost_price
    existing.current_price = fresh.current_price
    existing.position_ratio = fresh.position_ratio
    existing.holding_category = fresh.holding_category
    existing.classification_basis = fresh.classification_basis
    existing.confirm_price = fresh.confirm_price
    existing.trim_price = fresh.trim_price
    existing.trim_quantity = fresh.trim_quantity
    existing.buyback_price = fresh.buyback_price
    existing.max_buyback_quantity = fresh.max_buyback_quantity if existing.allow_buyback else 0
    existing.reduce_price = fresh.reduce_price
    existing.final_risk_price = fresh.final_risk_price
    existing.stop_loss_4pct = fresh.stop_loss_4pct
    existing.auction_plan = fresh.auction_plan
    existing.forbidden_actions = fresh.forbidden_actions
    for field in (
        "outperform_condition",
        "outperform_action",
        "expected_condition",
        "expected_action",
        "underperform_condition",
        "underperform_action",
        "trim_condition",
        "buyback_condition",
    ):
        if not str(getattr(existing, field) or "").strip():
            setattr(existing, field, getattr(fresh, field))
    # Upgrade only known legacy defaults.  User-authored text remains intact.
    if str(existing.trim_condition or "").strip() == "冲高到压力位或放量不封板时分批高抛":
        existing.trim_condition = fresh.trim_condition
    if str(existing.buyback_condition or "").strip() == "回落到支撑位缩量企稳，重新站回分时均价/VWAP":
        existing.buyback_condition = fresh.buyback_condition
    _refresh_plan_risk(existing)


def upsert_holding_next_day_plans(
    db: Session,
    *,
    completed_trade_date: str,
    commit: bool = True,
) -> int:
    """Create the next-session holding scripts from the completed close.

    This is the server-side counterpart of the old manual ``generate`` button.
    It deliberately uses the already persisted close snapshot instead of doing
    provider I/O after the close, so the plan and its expectation baseline are
    derived from the same auditable market-data cut.
    """

    reference_date = date.fromisoformat(completed_trade_date)
    plan_date = _next_trade_date(reference_date)
    holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
    holding_codes = {str(row.code) for row in holdings}
    if holding_codes:
        db.query(NextDayPlan).filter(
            NextDayPlan.plan_date == plan_date,
            NextDayPlan.plan_type == "holding",
            ~NextDayPlan.code.in_(holding_codes),
        ).delete(synchronize_session=False)
    else:
        db.query(NextDayPlan).filter(
            NextDayPlan.plan_date == plan_date,
            NextDayPlan.plan_type == "holding",
        ).delete(synchronize_session=False)

    latest_volume: dict[str, VolumePriceSnapshot] = {}
    if holding_codes:
        rows = (
            db.query(VolumePriceSnapshot)
            .filter(
                VolumePriceSnapshot.trade_date == completed_trade_date,
                VolumePriceSnapshot.code.in_(holding_codes),
            )
            .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
            .all()
        )
        for row in rows:
            latest_volume.setdefault(str(row.code), row)

    account_total_asset = _account_total_asset(db)
    count = 0
    for holding in holdings:
        volume = latest_volume.get(str(holding.code))
        quote: dict[str, Any] = {}
        if volume is not None:
            quote = {
                "price": volume.price,
                "change_pct": volume.change_pct,
                "open": volume.open_price,
                "prev_close": volume.prev_close,
                "high": volume.high_price,
                "low": volume.low_price,
                "volume": volume.volume,
                "amount": volume.amount,
                "turnover": volume.turnover,
                "volume_ratio": volume.volume_ratio,
                "vwap": volume.vwap if volume.vwap_reliable else 0,
                "note": f"{completed_trade_date} 收盘量价快照",
            }
        fresh = _default_next_day_plan(
            holding,
            plan_date,
            account_total_asset,
            quote,
        )
        auction = _json_obj(fresh.auction_plan)
        auction.update(
            {
                "plan_source": "after_close_auto",
                "baseline_trade_date": completed_trade_date,
                "selected_branch": "data_gap",
                "selected_branch_label": "等待竞价数据",
                "branch_status": "pending",
                "branch_reason": "盘后剧本已生成，等待下一交易日集合竞价选择互斥分支。",
                "branch_selected_at": "",
                "current_advice": "等待集合竞价，不把盘后预期当作买卖指令。",
                "advice_level": "observe",
                "advice_state": "active",
                "advice_revision": 1,
                "previous_advice": "",
                "advice_change": "initialized",
                "advice_change_reason": "盘后自动生成三套条件剧本。",
                "auto_refreshed_at": shanghai_now_naive().strftime("%Y-%m-%d %H:%M:%S"),
                "advice_history": [
                    {
                        "revision": 1,
                        "advice": "等待集合竞价，不把盘后预期当作买卖指令。",
                        "level": "observe",
                        "state": "active",
                        "stage": "盘后预期",
                        "branch": "data_gap",
                        "reason": "盘后自动生成三套条件剧本。",
                        "created_at": shanghai_now_naive().strftime("%Y-%m-%d %H:%M:%S"),
                    }
                ],
            }
        )
        fresh.auction_plan = json.dumps(auction, ensure_ascii=False)
        existing_rows = (
            db.query(NextDayPlan)
            .filter(
                NextDayPlan.plan_date == plan_date,
                NextDayPlan.code == holding.code,
                NextDayPlan.plan_type == "holding",
            )
            .order_by(NextDayPlan.updated_at.desc(), NextDayPlan.id.desc())
            .all()
        )
        if existing_rows:
            existing = existing_rows[0]
            existing_auction_raw = existing.auction_plan
            existing_auction = _json_obj(existing_auction_raw)
            same_baseline = (
                str(existing_auction.get("baseline_trade_date") or "")
                == completed_trade_date
            )
            for duplicate in existing_rows[1:]:
                db.delete(duplicate)
            _sync_holding_plan(existing, fresh)
            if same_baseline:
                # A morning restart replays the latest completed close.  Keep
                # the already-selected branch, advice audit trail, stage
                # checks, and any user-authored JSON instead of rewinding the
                # live plan to its post-close initial state.
                existing.auction_plan = existing_auction_raw
        else:
            db.add(fresh)
        count += 1
    db.flush()
    if commit:
        db.commit()
    return count


def _current_stage_label(now: datetime | None = None) -> str:
    current = shanghai_now_naive(now).time()
    if current.hour < 9 or (current.hour == 9 and current.minute < 25):
        return "盘后/盘前预期"
    if current.hour == 9 and current.minute < 30:
        return "竞价确认"
    if current.hour == 9 and current.minute < 35:
        return "开盘确认"
    if current.hour < 10:
        return "五分钟确认"
    if current.hour < 11 or (current.hour == 11 and current.minute <= 30):
        return "第一阶段确认"
    if current.hour < 14 or (current.hour == 14 and current.minute < 30):
        return "午盘/午后确认"
    if current.hour < 15:
        return "尾盘确认"
    return "盘后校准"


def _stage_status_from_expectation(result: str) -> tuple[str, str]:
    if result in {"STRONGER", "SLIGHTLY_STRONGER"}:
        return "通过", "预期强化，允许继续观察确认，不追最高点。"
    if result == "MATCHED":
        return "观察", "基本符合预期，必须继续等量价和板块确认。"
    if result in {"WEAKER", "SLIGHTLY_WEAKER"}:
        return "失败", "弱于预期，优先降风险，禁止补仓。"
    return "待确认", "预期数据不足，不能给确定动作。"


def _volume_confirmation_reliable(volume_price: Any | None) -> bool:
    if volume_price is None:
        return False
    quality = str(getattr(volume_price, "data_quality", "") or "").lower()
    return bool(
        getattr(volume_price, "vwap_reliable", False)
        and int(getattr(volume_price, "minute_bar_count", 0) or 0) >= 5
        and quality in {"realtime", "reliable", "complete", "ok"}
    )


def _stage_status_from_volume(pattern: str, volume_price: Any | None = None) -> tuple[str, str]:
    if not _volume_confirmation_reliable(volume_price):
        return "观察", "真实分钟VWAP或分钟样本不足，本阶段只保留观察，不升级买卖建议。"
    if any(token in pattern for token in (
        "放量上涨但承载效率下降",
        "放量滞涨",
        "高位承载衰减",
        "缩量上涨脆弱",
        "缩量诱多",
    )):
        return "失败", "量价承载效率下降，禁止追高；冲高后按计划保护利润。"
    if "VWAP上方强势" in pattern or "放量上涨确认" in pattern:
        return "通过", "量价承接有效，可按计划持有确认。"
    if "放量上涨待承接确认" in pattern or "缩量上涨待确认" in pattern:
        return "观察", "上涨尚未得到承接确认，等待回踩、VWAP和订单流方向验证。"
    if "冲高回落跌破VWAP" in pattern or "跌破VWAP" in pattern or "量价转弱" in pattern:
        return "失败", "量价承接失效，反抽优先降风险。"
    if "冲高回落" in pattern:
        return "观察", "冲高回落但未完全证伪，等重新站回VWAP。"
    return "观察", "量价暂未给出强确认。"


_ADVICE_LEVEL_RANK = {"positive": 0, "observe": 1, "warning": 2, "critical": 3}


def _opening_branch(
    plan: NextDayPlan,
    expectation: Any,
    quote: dict[str, Any],
    volume_price: Any,
) -> tuple[str, str, str, str]:
    """Select exactly one open branch from verified opening data."""

    open_price = _safe_float(quote.get("open")) or _safe_float(getattr(volume_price, "open_price", 0))
    prev_close = _safe_float(quote.get("prev_close")) or _safe_float(getattr(volume_price, "prev_close", 0))
    if open_price <= 0 or prev_close <= 0:
        return (
            "data_gap",
            "等待竞价数据",
            "pending",
            "竞价/开盘价或昨收价缺失，保留盘后剧本，不生成新的买卖动作。",
        )
    open_pct = (open_price - prev_close) / prev_close * 100
    low = _safe_float(getattr(expectation, "expected_open_low", 0))
    high = _safe_float(getattr(expectation, "expected_open_high", 0))
    if low > high:
        low, high = high, low
    if open_pct < low:
        return (
            "low_open_selloff",
            "低开下杀",
            "active",
            f"竞价/开盘 {open_pct:+.2f}% 低于合理区间下沿 {low:+.2f}%。",
        )
    if open_pct > high:
        return (
            "high_open_rally",
            "高开冲高",
            "active",
            f"竞价/开盘 {open_pct:+.2f}% 高于合理区间上沿 {high:+.2f}%。",
        )
    return (
        "range_open_balance",
        "区间内平开震荡",
        "active",
        f"竞价/开盘 {open_pct:+.2f}% 位于合理区间 {low:+.2f}%～{high:+.2f}% 内。",
    )


def _derive_plan_advice(
    plan: NextDayPlan,
    *,
    branch: str,
    expectation: Any,
    volume_price: Any,
) -> tuple[str, str, str]:
    result = str(getattr(expectation, "expectation_result", "") or "UNKNOWN")
    transition = str(getattr(expectation, "state_transition", "") or "")
    pattern = str(getattr(volume_price, "pattern", "") or "")
    volume_status, _ = _stage_status_from_volume(pattern, volume_price)
    reversal = (
        "REVERSAL" in transition
        or any(token in pattern for token in ("V形", "站回VWAP", "回踩不破", "支撑确认"))
    )
    weak_expectation = result in {"WEAKER", "SLIGHTLY_WEAKER", "INVALID"}
    strong_expectation = result in {"STRONGER", "SLIGHTLY_STRONGER"}

    if branch == "data_gap":
        return "等待竞价/开盘数据，不使用旧行情产生新动作。", "observe", "开盘证据缺口。"
    if branch == "low_open_selloff":
        if reversal:
            return (
                "低开分支出现修复证据，撤销低点立即卖出；等待站稳VWAP和抬高后的次低点确认，失败再恢复降风险。",
                "observe",
                f"低开后新增反转证据：{pattern or transition}。",
            )
        if weak_expectation and volume_status == "失败":
            return (
                plan.underperform_action or "预期证伪且量价承接失败，反抽分批降风险，禁止补仓。",
                "critical",
                f"低开、预期{result}与量价{pattern or '转弱'}三项共振。",
            )
        return (
            "低开先观察首个5分钟窗口，不在瞬时恐慌低点追卖；若反抽不过VWAP且再创新低，再按弱于预期剧本降风险。",
            "warning",
            "低开分支已激活，但尚未获得量价承接失败的双重确认。",
        )
    if branch == "high_open_rally":
        if volume_status == "失败":
            return (
                plan.trim_condition or "高开冲高未获量价承接，达到计划兑现区后分批保护利润。",
                "warning",
                f"高开后量价转弱：{pattern or '未站稳VWAP'}。",
            )
        if strong_expectation or volume_status == "通过":
            return (
                plan.outperform_action or "高开分支获得量价确认，保留核心仓，不追最高点。",
                "positive",
                "高开与预期/量价至少一项强确认共振。",
            )
        return (
            "高开只按冲高剧本观察；未确认站稳VWAP和首个回踩前，不扩大仓位。",
            "observe",
            "高开分支已激活，量价尚未确认。",
        )
    if weak_expectation and volume_status == "失败":
        return (
            plan.underperform_action or "区间开盘后量价转弱，反抽分批降风险。",
            "warning",
            f"区间开盘后预期{result}且量价{pattern or '转弱'}。",
        )
    if reversal or strong_expectation or volume_status == "通过":
        return (
            plan.expected_action or "区间开盘后承接改善，按计划持有观察，不追高。",
            "positive",
            f"区间开盘后新增正向证据：{pattern or transition or result}。",
        )
    return (
        plan.expected_action or "区间内震荡，按计划持有观察，不新增风险。",
        "observe",
        "区间分支内尚无足够证据升级或证伪。",
    )


def _persist_advice_revision(
    auction: dict[str, Any],
    *,
    advice: str,
    level: str,
    reason: str,
    stage: str,
    branch: str,
    now: datetime,
) -> None:
    history = [dict(item) for item in auction.get("advice_history", []) if isinstance(item, dict)]
    previous = str(auction.get("current_advice") or auction.get("operation_advice") or "")
    previous_level = str(auction.get("advice_level") or "observe")
    if previous == advice and previous_level == level:
        auction.update(
            {
                "advice_change": "unchanged",
                "advice_change_reason": reason,
                "auto_refreshed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        return

    if previous:
        for item in reversed(history):
            if item.get("state") == "active":
                item["state"] = "withdrawn"
                item["withdrawn_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
                item["withdraw_reason"] = reason
                break
    previous_rank = _ADVICE_LEVEL_RANK.get(previous_level, 1)
    next_rank = _ADVICE_LEVEL_RANK.get(level, 1)
    if previous and previous_rank >= 2 and next_rank <= 1:
        change = "withdrawn"
    elif next_rank > previous_rank:
        change = "upgraded"
    elif next_rank < previous_rank:
        change = "downgraded"
    elif previous:
        change = "replaced"
    else:
        change = "initialized"
    revision = int(auction.get("advice_revision") or 0) + 1
    history.append(
        {
            "revision": revision,
            "advice": advice,
            "level": level,
            "state": "active",
            "stage": stage,
            "branch": branch,
            "reason": reason,
            "created_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    auction.update(
        {
            "previous_advice": previous,
            "current_advice": advice,
            "operation_advice": advice,
            "advice_level": level,
            "advice_state": "active",
            "advice_revision": revision,
            "advice_change": change,
            "advice_change_reason": reason,
            "auto_refreshed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "advice_history": history[-60:],
        }
    )


def _build_stage_checks(
    plan: NextDayPlan,
    expectation: Any,
    volume_price: Any,
    quote: dict[str, Any],
) -> list[dict[str, Any]]:
    auction = _json_obj(plan.auction_plan)
    expected_status, expected_action = _stage_status_from_expectation(str(getattr(expectation, "expectation_result", "") or ""))
    volume_status, volume_action = _stage_status_from_volume(
        str(getattr(volume_price, "pattern", "") or ""),
        volume_price,
    )
    price = _safe_float(quote.get("price")) or plan.current_price
    high = _safe_float(quote.get("high"))
    prev_close = _safe_float(quote.get("prev_close"))
    open_price = _safe_float(quote.get("open"))
    limit_price = _safe_float(auction.get("limit_up_price")) or _safe_float(plan.limit_up_price)
    change_pct = _safe_float(quote.get("change_pct"))
    open_pct = ((open_price - prev_close) / prev_close * 100) if open_price and prev_close else 0
    touched_limit = bool(limit_price and high >= limit_price * 0.995)
    near_limit = bool(limit_price and price >= limit_price * 0.985)
    break_limit = bool(touched_limit and price < limit_price * 0.985)
    return [
        {
            "stage": "盘后预期",
            "status": "通过" if plan.holding_category in {"超预期", "强预期", "主线前排股"} else "观察",
            "trigger": plan.holding_category,
            "decision": auction.get("expectation_level") or plan.holding_category,
            "required_action": "只记录预案，不把预期当买入指令。",
            "evidence": [
                plan.outperform_condition or "超预期剧本待补。",
                plan.underperform_condition or "弱于预期剧本待补。",
            ],
        },
        {
            "stage": "竞价确认",
            "status": expected_status if open_price else "待确认",
            "trigger": f"开盘/竞价 {open_pct:+.2f}%",
            "decision": str(getattr(expectation, "expectation_result", "") or "待确认"),
            "required_action": expected_action,
            "evidence": list(getattr(expectation, "evidence", []) or [])[:3],
        },
        {
            "stage": "开盘确认",
            "status": "通过" if price >= max(open_price, plan.confirm_price or 0) and price > 0 else "观察" if price > 0 else "待确认",
            "trigger": f"现价 {price:.2f} / 确认位 {plan.confirm_price:.2f}",
            "decision": "站上确认位" if price >= (plan.confirm_price or 0) and price > 0 else "未站稳确认位",
            "required_action": "站稳确认位才继续持有；未站稳则只观察不加仓。",
            "evidence": [str(getattr(volume_price, "data_source", "") or "行情源待确认")],
        },
        {
            "stage": "五分钟量价确认",
            "status": volume_status,
            "trigger": str(getattr(volume_price, "pattern", "") or "量价待确认"),
            "decision": str(getattr(volume_price, "pattern", "") or "量价待确认"),
            "required_action": volume_action,
            "evidence": list(getattr(volume_price, "evidence", []) or [])[:4],
        },
        {
            "stage": "冲板确认",
            "status": "通过" if near_limit and volume_status != "失败" else "观察",
            "trigger": f"现价 {price:.2f} / 涨停 {limit_price:.2f}" if limit_price else "涨停价待确认",
            "decision": "接近涨停且量价未证伪" if near_limit else "尚未进入冲板确认",
            "required_action": "只在板块和量价同步确认时执行，不临盘扩大仓位。",
            "evidence": [auction.get("board_strength") or "板块订单流方向证据待补。"],
        },
        {
            "stage": "炸板/回落处理",
            "status": "失败" if break_limit else "观察",
            "trigger": f"最高 {high:.2f} / 现价 {price:.2f} / 涨跌 {change_pct:+.2f}%",
            "decision": "疑似炸板回落" if break_limit else "未触发炸板处理",
            "required_action": auction.get("break_limit_action") or "炸板无承接时不补仓，反抽优先降风险。",
            "evidence": list(auction.get("risk_notes") or [])[:4],
        },
    ]


def refresh_plan_stage_from_evidence(
    plan: NextDayPlan,
    db: Session,
    *,
    expectation: Any,
    volume_price: Any,
    quote: dict[str, Any],
    now: datetime | None = None,
) -> NextDayPlanOut:
    """Persist automatic branch selection and every advice transition."""

    evaluated_at = shanghai_now_naive(now)
    stage = _current_stage_label(evaluated_at)
    auction = _json_obj(plan.auction_plan)
    baseline = (
        db.query(ExpectationSnapshot)
        .filter(
            ExpectationSnapshot.trade_date == plan.plan_date,
            ExpectationSnapshot.code.in_([plan.code, plan.code.zfill(6), plan.code.lstrip("0")]),
            ExpectationSnapshot.stage == "次日盘前预期",
        )
        .order_by(
            ExpectationSnapshot.created_at.desc(),
            ExpectationSnapshot.id.desc(),
        )
        .first()
    )
    branch_source = baseline or expectation
    auction_not_finished = evaluated_at.time() < time(9, 25)
    if auction_not_finished:
        # Some quote vendors keep the previous session's ``open`` field before
        # the call auction has finished.  Never let that stale value select a
        # live branch before 09:25.
        branch, branch_label, branch_status, branch_reason = (
            "data_gap",
            "等待竞价数据",
            "pending",
            "集合竞价尚未结束，继续保留盘后剧本，不提前选择开盘分支。",
        )
    else:
        branch, branch_label, branch_status, branch_reason = _opening_branch(
            plan,
            branch_source,
            quote,
            volume_price,
        )
    previous_branch = str(auction.get("selected_branch") or "")
    if branch == "data_gap" and previous_branch and previous_branch != "data_gap":
        branch = previous_branch
        branch_label = str(auction.get("selected_branch_label") or "已选分支")
        branch_status = str(auction.get("branch_status") or "active")
        branch_reason = "本轮开盘字段暂时缺失，保留此前已由真实竞价选中的分支，不生成新动作。"
    branch_selected_at = str(auction.get("branch_selected_at") or "")
    if branch != "data_gap" and branch != previous_branch:
        branch_selected_at = evaluated_at.strftime("%Y-%m-%d %H:%M:%S")

    checks = _build_stage_checks(plan, expectation, volume_price, quote)
    failed = [item for item in checks if item["status"] == "失败"]
    passed = [item for item in checks if item["status"] == "通过"]
    if failed:
        decision = f"{failed[-1]['stage']}失败：{failed[-1]['required_action']}"
    elif len(passed) >= 3:
        decision = "关键阶段通过，继续按计划确认，不追最高点。"
    else:
        decision = "仍处观察阶段，等待竞价/开盘/量价进一步确认。"
    if auction_not_finished:
        advice = str(
            auction.get("current_advice")
            or "等待集合竞价结束，不使用盘前波动产生新的买卖动作。"
        )
        advice_level = str(auction.get("advice_level") or "observe")
        advice_reason = branch_reason
    else:
        advice, advice_level, advice_reason = _derive_plan_advice(
            plan,
            branch=branch,
            expectation=expectation,
            volume_price=volume_price,
        )
    if branch_reason:
        advice_reason = f"{branch_reason} {advice_reason}".strip()
    auction.update(
        {
            "current_stage": stage,
            "stage_decision": decision,
            "stage_checks": checks,
            "selected_branch": branch,
            "selected_branch_label": branch_label,
            "branch_status": branch_status,
            "branch_reason": branch_reason,
            "branch_selected_at": branch_selected_at,
            "action_ladder": [
                "高开冲高：确认站稳VWAP与首个回踩后再持有，放量滞涨则分批保护利润。",
                "区间内平开震荡：按计划持有观察，不新增风险。",
                "低开下杀：不在瞬时恐慌低点追卖，反抽不过VWAP且再创新低才降风险。",
                "新增V形修复/站回VWAP证据：撤销低点时的旧卖出结论；修复失败可再次升级。",
            ],
            "expectation_match": str(getattr(expectation, "expectation_result", "") or auction.get("expectation_match") or ""),
            "volume_price_status": str(getattr(volume_price, "pattern", "") or auction.get("volume_price_status") or ""),
            "refreshed_at": evaluated_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    _persist_advice_revision(
        auction,
        advice=advice,
        level=advice_level,
        reason=advice_reason,
        stage=stage,
        branch=branch,
        now=evaluated_at,
    )
    plan.auction_plan = json.dumps(auction, ensure_ascii=False)
    if advice_level == "critical":
        plan.holding_category = "弱于预期"
    elif advice_level == "warning" and plan.holding_category not in {"弱于预期", "分歧转弱"}:
        plan.holding_category = "分歧转弱"
    elif auction.get("advice_change") == "withdrawn" and plan.holding_category in {"弱于预期", "分歧转弱"}:
        plan.holding_category = "弱转强" if "反转" in advice_reason or "V形" in advice_reason else "符合预期"
    _refresh_plan_risk(plan)
    db.commit()
    db.refresh(plan)
    return _next_day_plan_out(plan, price_note=str(quote.get("note") or ""))


def refresh_limit_expectation_stage(plan: NextDayPlan, db: Session) -> NextDayPlanOut:
    from app.api.helpers.decision import build_expectation_snapshot, quote_for_code
    from app.api.helpers.volume_price import build_volume_price_snapshot

    quote = quote_for_code(plan.code)
    expectation = build_expectation_snapshot(
        db,
        plan.code,
        name=plan.name,
        stage=_current_stage_label(),
        quote=quote,
        base_hint=f"{plan.holding_category} {plan.outperform_condition} {plan.underperform_condition}",
    )
    volume_price = build_volume_price_snapshot(
        db,
        plan.code,
        name=plan.name,
        stage=_current_stage_label(),
        quote=quote,
    )
    return refresh_plan_stage_from_evidence(
        plan,
        db,
        expectation=expectation,
        volume_price=volume_price,
        quote=quote,
    )

def _limit_up_next_day_plan(
    payload: LimitUpPlanCreate,
    plan_date: str,
    existing: NextDayPlan | None = None,
) -> NextDayPlan:
    next_limit_price = _next_limit_up_price(payload.price)
    turnover = _safe_turnover(payload.turnover)
    concepts = [item for item in payload.concepts if item]
    concept_text = "、".join(concepts[:4]) or "待补概念"
    board_level = f"{max(payload.level, 1)}板"
    evidence = _limit_up_auction_evidence(payload, concepts)
    risk_notes = evidence["risk_notes"]
    requested_cap = max(0.0, min(0.10, float(payload.max_position_ratio or 0)))
    evidence_cap = max(0.0, min(0.10, float(evidence.get("max_position_ratio") or 0)))
    approved_cap = min(requested_cap, evidence_cap)
    weak_reduce_price = round(payload.price, 2)
    weak_exit_price = round(payload.price * 0.97, 2)
    keep_condition = (
        "9:20后封单不明显撤退，同题材核心股同步强化；"
        "开盘后5-15分钟站稳分时均价/VWAP，不能只看9:15-9:20虚假竞价。"
    )
    cancel_condition = (
        "9:20后封单快速衰减、同题材前排走弱、竞价高开低走或炸板后无承接，立即撤单。"
    )
    auction_plan = {
        "board_level": board_level,
        "industry": payload.industry,
        "concepts": concepts,
        "overnight_order": approved_cap > 0,
        "order_price": next_limit_price,
        "limit_up_price": next_limit_price,
        "keep_order_condition": keep_condition,
        "cancel_condition": cancel_condition,
        "opening_confirmation": "集合竞价只是筛选，连续竞价开盘后的承接才是确认。",
        "max_position_ratio": approved_cap,
        "break_limit_action": "炸板后不临时加仓；只有强回封、板块仍扩散、成交承接健康时才重新评估。",
        "notes": payload.expectation,
        "board_strength": evidence["board_strength"],
        "leader_support": evidence["leader_support"],
        "limit_quality": evidence["limit_quality"],
        "expectation_level": evidence["expectation_level"],
        "strong_boundary_price": weak_reduce_price,
        "weak_reduce_price": weak_reduce_price,
        "weak_exit_price": weak_exit_price,
        "risk_notes": risk_notes,
        "mainline_name": evidence["mainline_name"],
        "mainline_rank": evidence["mainline_rank"],
        "mainline_score": evidence["mainline_score"],
        "mainline_level": evidence["mainline_level"],
        "is_mainline": evidence["is_mainline"],
        "theme_stage": evidence["theme_stage"],
        "theme_stage_reason": evidence["theme_stage_reason"],
        "identity_roles": evidence["identity_roles"],
        "identity_action": evidence["identity_action"],
        "position_rule": evidence["position_rule"],
        "theme_evidence": evidence["theme_evidence"],
    }
    plan = existing or NextDayPlan()
    plan.plan_date = plan_date
    plan.plan_type = "limit_up_auction"
    plan.holding_id = None
    plan.code = payload.code
    plan.name = payload.name
    plan.quantity = 0
    plan.cost_price = payload.price
    plan.current_price = payload.price
    plan.position_ratio = 0.0
    plan.holding_category = "主线前排股" if evidence["is_mainline"] is True and approved_cap > 0 else "非主线观察股"
    plan.classification_basis = json.dumps(
        {
            "sector": payload.industry or concept_text,
            "mainline_position": evidence["mainline_position"] or f"{board_level}涨停股，需明日竞价确认是否仍是前排",
            "fund_flow": evidence["board_strength"] or f"涨停封单约{payload.sealed_amount:.2f}亿，成交约{payload.amount:.2f}亿",
            "amount": f"{payload.amount:.2f}亿",
            "turnover": f"{turnover:.2f}%" if turnover is not None else f"数据异常：原始换手率 {payload.turnover}",
            "trend": evidence["limit_quality"],
            "support": str(payload.price),
            "pressure": str(next_limit_price),
            "weaker_than_sector": False,
        },
        ensure_ascii=False,
    )
    plan.outperform_condition = (
        f"超预期：{payload.name}直接一字，或高开5%以上快速加速上板；"
        f"{payload.industry or concept_text}继续强于市场，前排助攻不掉队。"
        f"若属于高位天量后的再一致，只按加速末段处理，明日涨停参考 {next_limit_price:.2f}。"
    )
    plan.outperform_action = (
        f"超预期才看晋级：若封单稳定且板块助攻成立，持有为主；"
        f"委托价不高于 {next_limit_price:.2f}，仓位上限 {approved_cap * 100:.0f}%。"
        "高位天量或偏离5日线过远时以保护利润为主，不继续扩大仓位。"
    )
    if approved_cap <= 0:
        plan.outperform_action = (
            f"即使个股超预期，也先验证{evidence['mainline_name'] or payload.industry or concept_text}"
            f"的主线地位、{evidence['theme_stage']}阶段和前排身份；当前只观察，不下单。"
        )
    plan.expected_condition = (
        f"符合预期：高开2%-5%，短暂换手后10点前回封；"
        f"回踩不破 {weak_reduce_price:.2f}，分时均价承接强，板块订单流方向仍在前排。"
    )
    plan.expected_action = (
        f"持有观察，不加仓；若迟迟不板但仍站稳 {weak_reduce_price:.2f}，可以保留底仓，"
        "冲高封单转弱时先锁一部分利润。"
    )
    plan.underperform_condition = (
        f"弱于预期：平开/低开，或高开后不能快速上板；跌破强弱分界 {weak_reduce_price:.2f} 后不能迅速收回；"
        f"若继续跌破清仓线 {weak_exit_price:.2f}，说明接力失败；"
        "个股强而板块无助攻时，涨停预期下调一级。"
    )
    plan.underperform_action = (
        f"跌破 {weak_reduce_price:.2f} 且5-15分钟不能收回，先减仓至少1/2；"
        f"跌破分时均价后反抽不过继续减仓；跌破 {weak_exit_price:.2f} 或冲板失败放量回落，清掉剩余仓位。"
    )
    plan.confirm_price = payload.price
    plan.trim_price = 0.0
    plan.trim_condition = "买入后次日再生成卖出计划；打板当日不做T。"
    plan.trim_quantity = 0
    plan.allow_buyback = False
    plan.buyback_price = 0.0
    plan.buyback_condition = ""
    plan.max_buyback_quantity = 0
    plan.reduce_price = weak_reduce_price
    plan.final_risk_price = weak_exit_price
    plan.stop_loss_4pct = round(payload.price * 0.96, 2)
    plan.limit_up_price = next_limit_price
    plan.auction_plan = json.dumps(auction_plan, ensure_ascii=False)
    plan.forbidden_actions = json.dumps(
        [
            "不无脑隔夜成交",
            "不看9:15虚假封单",
            "不盘中临时追高",
            "高位天量后不继续扩大仓位",
            "炸板无承接必须放弃",
            "非主线、高潮或退潮阶段仓位上限为0",
        ],
        ensure_ascii=False,
    )
    _refresh_plan_risk(plan)
    return plan

def _limit_up_auction_evidence(payload: LimitUpPlanCreate, concepts: list[str]) -> dict[str, Any]:
    concept_text = " ".join([payload.industry, payload.name, *concepts])
    board_strength = "板块订单流方向数据缺口：请先刷新题材雷达/订单流估算，再生成打板预案。"
    mainline_position = ""
    leader_support: list[str] = []
    board_supported = False
    weak_board = False
    support_count = 0
    mainline_name = ""
    mainline_rank: int | None = None
    mainline_score: int | None = None
    mainline_level = "待验证"
    is_mainline: bool | None = None
    theme_stage = "数据不足"
    theme_stage_reason = "缺少题材订单流方向与阶段证据"
    identity_roles: list[str] = []
    identity_action = "只观察"
    position_rule = "主线、阶段和个股身份未完成联合确认，仓位上限为0%"
    evidence_position_cap = 0.0
    theme_evidence: list[str] = []

    radar = _get_current_theme_radar_cache()
    if radar is not None:
        for idx, theme in enumerate(radar.themes[:20], start=1):
            theme_mainline = str(getattr(theme, "mainline", "") or getattr(theme, "theme_type", "") or "")
            theme_subline = str(getattr(theme, "subline", "") or "")
            theme_category = str(getattr(theme, "category", "") or "")
            related = " ".join([
                theme.name,
                theme_mainline,
                theme_subline,
                *theme.related_boards,
                *theme.leader_names,
                *(role.name for role in theme.core_stocks),
                *(role.code for role in theme.core_stocks),
            ])
            if _contains_any(related, tuple([payload.industry, *concepts, payload.name, payload.code])) or _contains_any(concept_text, tuple([theme.name, theme_mainline, theme_subline])):
                board_strength = (
                    f"{theme.name}：题材强度{theme.score}分，排名第{idx}；"
                    f"板块订单流方向净额{theme.net_inflow:.2f}亿，大单方向估算{theme.main_inflow:.2f}亿，"
                    f"涨停{theme.limit_up_count}只，阶段={theme.stage}（供应商算法，非账户真实流水）。"
                )
                board_supported = theme.net_inflow > 0 and theme.main_inflow > 0 and theme.limit_up_count >= 3
                weak_board = theme.net_inflow <= 0 or theme.main_inflow <= 0 or theme.limit_up_count <= 1 or theme.score < 60
                support_count = theme.limit_up_count
                mainline_position = (
                    f"{theme.name} / {theme_mainline or theme_category or '待分类'}，"
                    f"{'主线前排' if theme.score >= 75 else '轮动/分歧题材'}。"
                )
                leader_support = [
                    f"{role.name}({role.code}) {role.role}，涨跌{role.change_pct:+.2f}%，成交{role.amount:.2f}亿：{role.reason}"
                    for role in theme.core_stocks[:6]
                ] or [f"核心股：{name}" for name in theme.leader_names[:6]]
                break

    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    ladder_support: list[str] = []
    break_count = payload.break_count
    if ladder is not None:
        matched_clusters = [
            cluster
            for cluster in ladder.clusters
            if _contains_any(cluster.name, tuple([payload.industry, *concepts]))
            or _contains_any(" ".join(cluster.stocks), tuple([payload.name, payload.code, *concepts]))
        ]
        for cluster in matched_clusters[:3]:
            support_count = max(support_count, cluster.count)
            ladder_support.append(
                f"{cluster.name}：{cluster.count}只涨停，最高{cluster.highest_level}板，前排 {'、'.join(cluster.stocks[:6])}。"
            )
        for group in ladder.groups:
            for stock in group.stocks:
                if stock.code == payload.code or stock.name == payload.name:
                    break_count = stock.break_count
                    quality = _limit_quality_text(stock.amount, stock.turnover, stock.break_count, stock.sealed_amount, group.label)
                    if quality:
                        payload.amount = stock.amount or payload.amount
                        payload.turnover = stock.turnover or payload.turnover
                        payload.sealed_amount = stock.sealed_amount or payload.sealed_amount
                    break

    if ladder_support:
        leader_support = list(dict.fromkeys([*leader_support, *ladder_support]))[:8]
    if not leader_support:
        leader_support = ["前排助攻数据缺口：请先刷新涨停天梯和题材雷达。"]

    atmosphere = _get_response_cache("limit-up-atmosphere-latest")
    if atmosphere is not None:
        matched_theme = None
        matched_role = None
        for theme in getattr(atmosphere, "theme_ladders", []) or []:
            role = next(
                (
                    item for item in theme.identity_roles
                    if item.code == payload.code or item.name == payload.name
                ),
                None,
            )
            if role is not None:
                matched_theme = theme
                matched_role = role
                break
            payload_theme_text = " ".join([payload.industry, *concepts])
            if matched_theme is None and (
                _contains_any(theme.name, tuple([payload.industry, *concepts]))
                or _contains_any(payload_theme_text, (theme.name, theme.mainline_name))
            ):
                matched_theme = theme
        if matched_theme is not None:
            mainline_name = matched_theme.mainline_name or matched_theme.name
            mainline_rank = matched_theme.mainline_rank
            mainline_score = matched_theme.mainline_score
            mainline_level = matched_theme.mainline_level
            is_mainline = matched_theme.is_mainline
            theme_stage = matched_theme.stage
            theme_stage_reason = matched_theme.stage_reason
            position_rule = matched_theme.stage_position_rule
            theme_evidence = list(matched_theme.evidence)
            board_strength = "；".join(theme_evidence[:3]) or board_strength
            mainline_position = (
                f"{mainline_name} / {mainline_level} / 排名"
                f"{mainline_rank if mainline_rank is not None else '待确认'} / 阶段={theme_stage}。"
            )
            if matched_role is not None:
                identity_roles = list(matched_role.roles)
                identity_action = matched_role.recommended_action
                evidence_position_cap = float(matched_role.max_position_ratio or 0)
                position_rule = matched_role.recommended_action
                leader_support = list(dict.fromkeys([
                    f"{matched_role.name}({matched_role.code})：{' / '.join(identity_roles)}；{matched_role.reason}",
                    *leader_support,
                ]))[:8]
            else:
                identity_action = "该股未进入题材前排身份列表，只生成观察预案，禁止下单"
                evidence_position_cap = 0.0
                position_rule = identity_action

    limit_quality = _limit_quality_text(
        payload.amount,
        payload.turnover,
        break_count,
        payload.sealed_amount,
        f"{max(payload.level, 1)}板",
    )
    risk_notes = _auction_risk_notes(payload, break_count, board_supported, weak_board, support_count)
    if is_mainline is False:
        risk_notes.append(f"{mainline_level}：单只涨停不能证明持续性，当前禁止新开打板仓。")
    if theme_stage in {"高潮", "退潮"}:
        risk_notes.append(f"题材处于{theme_stage}阶段：防一致性兑现或退潮补跌，仓位上限为0。")
    if evidence_position_cap <= 0:
        risk_notes.append(f"仓位门控：{position_rule}")
    expectation_level = _auction_expectation_level(payload, break_count, board_strength, weak_board, support_count)
    if evidence_position_cap <= 0:
        expectation_level = f"观察级：{mainline_level}·{theme_stage}，不开放打板仓位"
    return {
        "board_strength": board_strength,
        "mainline_position": mainline_position,
        "leader_support": leader_support,
        "limit_quality": limit_quality,
        "expectation_level": expectation_level,
        "risk_notes": risk_notes,
        "mainline_name": mainline_name,
        "mainline_rank": mainline_rank,
        "mainline_score": mainline_score,
        "mainline_level": mainline_level,
        "is_mainline": is_mainline,
        "theme_stage": theme_stage,
        "theme_stage_reason": theme_stage_reason,
        "identity_roles": identity_roles,
        "identity_action": identity_action,
        "position_rule": position_rule,
        "max_position_ratio": evidence_position_cap,
        "theme_evidence": theme_evidence,
    }

def _limit_quality_text(amount: float, turnover: float, break_count: int, sealed_amount: float, board_level: str) -> str:
    quality = "一致强封"
    if break_count > 0:
        quality = "高换手分歧回封"
    if amount >= 80:
        quality = "容量核心放量换手板" if break_count == 0 else "容量核心爆量分歧回封"
    elif turnover >= 18 or break_count >= 1:
        quality = "高换手分歧回封"
    if sealed_amount <= 0.2 and break_count > 0:
        quality = "弱封单分歧回封"
    return (
        f"{board_level}，{quality}；成交{amount:.2f}亿，换手{turnover:.2f}%，"
        f"炸板{break_count}次，封单{sealed_amount:.2f}亿。"
    )

def _auction_expectation_level(
    payload: LimitUpPlanCreate,
    break_count: int,
    board_strength: str,
    weak_board: bool = False,
    support_count: int = 0,
) -> str:
    if weak_board or (support_count and support_count <= 1):
        if break_count > 0 or payload.turnover >= 18 or payload.amount >= 20:
            return "个股强而板块弱，预期下调：次日必须强更强"
    if payload.amount >= 80:
        return "容量核心放量换手，风险升高但可观察"
    if break_count == 0 and payload.turnover < 12 and payload.sealed_amount >= 1:
        return "强预期"
    if break_count > 0 or payload.turnover >= 18:
        return "分歧偏弱，次日必须强更强"
    return "符合预期"

def _auction_risk_notes(
    payload: LimitUpPlanCreate,
    break_count: int,
    board_supported: bool,
    weak_board: bool,
    support_count: int,
) -> list[str]:
    turnover = _safe_turnover(payload.turnover) or 0.0
    notes: list[str] = []
    high_level = payload.level >= 2
    high_volume = payload.amount >= 20 or turnover >= 18
    if high_level and high_volume:
        if payload.amount >= 80:
            notes.append("容量核心放量换手：风险权重升高，但若主线订单流方向与前排助攻持续，仍有观察价值。")
        elif break_count > 0:
            notes.append("高位天量分歧回封：继续转一致难度上升，次日必须强更强。")
        else:
            notes.append("高位放量：筹码交换剧烈，风险收益比下降，不能按低位启动看待。")
    if high_level and payload.price > 0:
        notes.append("高位天量后若次日再一致，只按加速末段处理，保护利润优先，不宜继续扩大仓位。")
    if high_volume:
        notes.append("偏离5日线风险：未取得均线数据时按高位放量替代提示，禁止追高加仓，等待竞价/开盘强势确认。")
    if weak_board:
        notes.append("板块订单流方向不支持：个股独立行情持续性下降，若次日无板块助攻，涨停预期下调一级。")
    elif not board_supported:
        notes.append("板块共振证据不足：需补充题材雷达、订单流方向估算与涨停天梯后再提高预期。")
    if support_count <= 1:
        notes.append("前排/后排助攻不足：同题材梯队或首板扩散不足，次日必须个股强更强。")
    notes.append(f"弱于预期价格触发：跌破{payload.price:.2f}先减仓，跌破分时均价后反抽不过继续减仓，跌破{payload.price * 0.97:.2f}附近清仓。")
    return list(dict.fromkeys(notes))

def _holding_market_evidence(holding: Holding, quote: dict[str, Any] | None = None) -> dict[str, Any]:
    theme_profile = _holding_theme_profile(holding)
    evidence: dict[str, Any] = {
        "sector": theme_profile["primary"],
        "theme_tags": list(theme_profile["tags"]),
        "stock_industry": theme_profile.get("industry") or "",
        "stock_concepts": list(theme_profile.get("concepts") or []),
        "theme_source": theme_profile.get("source") or "",
        "mainline_position": (
            f"所属主线：{theme_profile['primary']}；标签：{' / '.join(theme_profile['tags']) or '待确认'}；"
            f"原始行业={theme_profile.get('industry') or '未抓到'}；"
            f"原始概念={ '、'.join(list(theme_profile.get('concepts') or [])[:6]) or '未抓到'}。"
        ),
        "fund_flow": "",
        "amount": "",
        "turnover": "",
        "trend": "",
        "intraday": "",
        "weaker_than_sector": False,
        "is_mainline_front": False,
        "is_high_divergence": False,
        "is_underperforming": False,
    }
    quote = quote or {}
    if quote:
        current_price = _safe_float(quote.get("price")) or holding.current_price
        open_price = _safe_float(quote.get("open"))
        prev_close = _safe_float(quote.get("prev_close"))
        high_price = _safe_float(quote.get("high"))
        low_price = _safe_float(quote.get("low"))
        change_pct = _safe_float(quote.get("change_pct"))
        amount = _safe_float(quote.get("amount"))
        turnover = quote.get("turnover")
        if amount > 0:
            evidence["amount"] = f"{amount:.2f}亿"
        if turnover:
            evidence["turnover"] = f"{turnover:.2f}%"
        open_gap = ((open_price - prev_close) / prev_close * 100) if prev_close else 0.0
        intraday_repair = bool(
            prev_close
            and open_price
            and low_price
            and open_price < prev_close
            and current_price >= prev_close
        )
        high_reject = bool(high_price and current_price <= high_price * 0.97 and change_pct <= 2)
        if prev_close and open_price:
            evidence["intraday"] = (
                f"今开{open_price:.2f}（开盘{open_gap:+.2f}%），"
                f"现价{current_price:.2f}（涨跌{change_pct:+.2f}%），"
                f"日内高低{high_price:.2f}/{low_price:.2f}。"
            )
        else:
            evidence["intraday"] = "分时字段不足，仅使用最新价与涨跌幅做降级判断。"
        evidence["gap_pct"] = open_gap
        evidence["change_pct"] = change_pct
        evidence["intraday_repair"] = intraday_repair
        evidence["high_reject"] = high_reject
        evidence["strong_open"] = open_gap >= 2 and current_price >= open_price
        evidence["super_expectation"] = open_gap >= 3 and change_pct >= 5 and not high_reject
        evidence["weak_open"] = open_gap <= -1.5
        volume_context = _volume_price_context(holding.code, quote)
        evidence.update(volume_context)
    theme_flow = _cached_holding_theme_flow_profile(holding, allow_network=True)
    if theme_flow["sectors"]:
        evidence["flow_basis"] = str(theme_flow.get("basis") or "行业资金流").replace("资金流", "订单流算法")
        evidence["primary_industry_sector"] = "、".join(theme_flow["sectors"][:3])
        evidence["matched_flow_sector"] = evidence["primary_industry_sector"]
        evidence["theme_flow_sectors"] = list(theme_flow["sectors"])
        evidence["concept_flow_sectors"] = list(theme_flow.get("concept_sectors") or [])
        evidence["concept_flow_summary"] = theme_flow.get("concept_summary") or ""
        evidence["theme_flow_summary"] = theme_flow["summary"]
        evidence["theme_flow_current"] = theme_flow["current"]
        evidence["theme_flow_peak"] = theme_flow["peak"]
        evidence["theme_flow_pullback"] = theme_flow["pullback"]
        evidence["theme_flow_pullback_pct"] = theme_flow["pullback_pct"]
        evidence["fund_flow"] = theme_flow["summary"]
        evidence["is_underperforming"] = bool(
            evidence.get("is_underperforming")
            or theme_flow["pullback"] >= 20
            or theme_flow["pullback_pct"] >= 20
            or theme_flow["current"] < 0
        )
    radar = _get_current_theme_radar_cache()
    if radar is not None:
        for theme in radar.themes[:20]:
            stock_names = [role.name for role in theme.core_stocks]
            stock_codes = [role.code for role in theme.core_stocks]
            related_text = "".join(theme.related_boards + theme.leader_names + stock_names + stock_codes)
            if holding.code in related_text or holding.name in related_text or _contains_any(
                f"{holding.position_type} {holding.next_discipline}",
                tuple(theme.related_boards + [theme.name]),
            ):
                evidence["radar_auxiliary_sector"] = theme.name
                evidence["mainline_position"] = (
                    f"所属主线：{theme_profile['primary']}；题材雷达辅助：{theme.name}，"
                    f"{theme.stage}，题材评分{theme.score}；核心股："
                    f"{'、'.join(theme.leader_names[:4]) or '待确认'}"
                )
                if not evidence.get("fund_flow"):
                    evidence["fund_flow"] = (
                        f"题材雷达辅助：{theme.name}订单流方向净额{theme.net_inflow:.2f}亿，"
                        f"大单方向估算{theme.main_inflow:.2f}亿，涨停{theme.limit_up_count}只（供应商算法，非账户真实流水）。"
                    )
                evidence["is_mainline_front"] = (
                    theme.score >= 75
                    and (holding.name in theme.leader_names or holding.code in stock_codes or holding.name in stock_names)
                )
                evidence["is_underperforming"] = theme.score < 55 or theme.net_inflow < 0
                break

    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for group in ladder.groups:
            for stock in group.stocks:
                if stock.code == holding.code or stock.name == holding.name:
                    turnover = _safe_turnover(stock.turnover)
                    if stock.industry:
                        evidence["ladder_auxiliary_sector"] = stock.industry
                    evidence["mainline_position"] = (
                        f"所属主线：{theme_profile['primary']}；涨停天梯{group.label}；"
                        f"概念：{'、'.join(stock.concepts[:4]) or '待确认'}。"
                    )
                    evidence["amount"] = f"{stock.amount:.2f}亿"
                    evidence["turnover"] = (
                        f"{turnover:.2f}%"
                        if turnover is not None
                        else f"数据异常：原始换手率 {stock.turnover}"
                    )
                    if not evidence.get("fund_flow"):
                        evidence["fund_flow"] = (
                            f"涨停天梯辅助：封单{stock.sealed_amount:.2f}亿，"
                            f"炸板{stock.break_count}次；{stock.expectation}"
                        )
                    evidence["is_mainline_front"] = True
                    evidence["is_high_divergence"] = (
                        stock.break_count >= 2
                        or stock.amount >= 30
                        or (turnover is not None and turnover >= 25)
                    )
                    evidence["trend"] = "涨停强势结构，次日必须看封单、开盘承接与板块扩散。"
                    return evidence

    current = holding.current_price
    cost = holding.cost_price
    if cost and current < cost * 0.97:
        evidence["is_underperforming"] = True
        evidence["trend"] = "现价低于成本3%以上，优先按弱修复/退出纪律处理。"
    elif cost and current > cost * 1.12:
        evidence["trend"] = "已有较明显利润垫，关注冲高兑现与回撤保护。"
    return evidence

def _volume_price_context(code: str, quote: dict[str, Any]) -> dict[str, Any]:
    amount_today = _safe_float(quote.get("amount"))
    volume_today = _safe_float(quote.get("volume")) / 100
    change_pct = _safe_float(quote.get("change_pct"))
    high_price = _safe_float(quote.get("high"))
    low_price = _safe_float(quote.get("low"))
    open_price = _safe_float(quote.get("open"))
    price = _safe_float(quote.get("price"))
    hist = _daily_history_metrics(code)
    five_day_avg_volume = _safe_float(hist.get("five_day_avg_volume"))
    ma5 = _safe_float(hist.get("ma5"))
    volume_ratio = volume_today / five_day_avg_volume if volume_today and five_day_avg_volume else 0.0
    amplitude = (high_price - low_price) / _safe_float(quote.get("prev_close")) * 100 if high_price and low_price and quote.get("prev_close") else 0.0
    status = "量价数据不足"
    if volume_ratio >= 2.5 and abs(change_pct) >= 3:
        status = "放巨量分歧" if change_pct < 5 else "放巨量拉升"
    elif volume_ratio >= 1.3 and change_pct >= 3:
        status = "放量拉升"
    elif 0 < volume_ratio < 1.0 and change_pct >= 3:
        status = "缩量拉升"
    elif volume_ratio >= 1.2 and change_pct <= -3:
        status = "放量大跌"
    elif 0 < volume_ratio < 0.8 and -1 <= change_pct <= 1:
        status = "缩量震荡"
    elif 0 < volume_ratio < 0.8 and change_pct > 0:
        status = "缩量止跌/修复"
    elif volume_ratio >= 1.1 and abs(change_pct) < 1.5:
        status = "放量滞涨/震荡"
    elif volume_ratio >= 1.1:
        status = "轻微放量"
    elif volume_ratio > 0:
        status = "缩量整理"

    ma5_deviation = (price - ma5) / ma5 * 100 if price and ma5 else 0.0
    detail = (
        f"今日成交额{amount_today:.2f}亿，今日成交量{volume_today:.0f}手，近5日均量{five_day_avg_volume:.0f}手，"
        f"量比{volume_ratio:.2f}；涨跌{change_pct:+.2f}%，振幅{amplitude:.2f}%"
    )
    if ma5:
        detail += f"，5日均价{ma5:.2f}，偏离{ma5_deviation:+.2f}%"
    return {
        "volume_price_status": status,
        "volume_price_detail": detail,
        "five_day_avg_amount": five_day_avg_volume,
        "today_amount": amount_today,
        "today_volume": volume_today,
        "volume_ratio": volume_ratio,
        "ma5": ma5,
        "ma5_deviation": ma5_deviation,
    }

def _dynamic_holding_auction_plan(
    holding: Holding,
    category: str,
    evidence: dict[str, Any],
    quote: dict[str, Any] | None,
) -> dict[str, Any]:
    quote = quote or {}
    current = _safe_float(quote.get("price")) or holding.current_price
    prev_close = _safe_float(quote.get("prev_close"))
    open_price = _safe_float(quote.get("open"))
    change_pct = _safe_float(quote.get("change_pct"))
    volume_status = str(evidence.get("volume_price_status") or "量价数据不足")
    expected_state = _expected_condition(category)
    expectation_match = _expectation_match_label(evidence, category)
    operation_advice = _dynamic_operation_advice(expectation_match, category, holding, current)
    board_strength = evidence.get("fund_flow") or "板块订单流方向证据缺口：请刷新题材雷达/订单流估算。"
    leader_support = _leader_support_for_holding(holding, evidence)
    limit_quality = (
        f"{holding.name}盘中状态：{evidence.get('intraday') or '实时分时字段不足'}；"
        f"{evidence.get('volume_price_detail') or volume_status}。"
    )
    strong_boundary = round(max(prev_close or current, open_price or current, holding.cost_price), 2)
    weak_reduce = round(max(holding.cost_price * 0.98, current * 0.97), 2)
    weak_exit = round(max(holding.cost_price * 0.96, current * 0.94), 2)
    next_day_script = [
        f"超预期：{_outperform_condition(category)} 动作：{_outperform_action(category)}",
        f"符合预期：{_expected_condition(category)} 动作：{_expected_action(category)}",
        f"弱于预期：{_underperform_condition(category)} 动作：{_underperform_action(category)}",
    ]
    risk_notes = _dynamic_risk_notes(evidence, holding, current)
    sell_trigger_cards = _dynamic_sell_trigger_cards(holding, evidence, quote, current)
    return {
        "board_level": "持仓动态预期",
        "industry": evidence.get("sector") or "",
        "concepts": [str(item) for item in (evidence.get("theme_tags") or [evidence.get("sector")]) if item],
        "overnight_order": False,
        "order_price": 0.0,
        "limit_up_price": _next_limit_up_price(current) if current else 0.0,
        "keep_order_condition": "持仓计划不使用隔夜买入，盘中只按强弱触发处理。",
        "cancel_condition": "不符合预期或板块证据转弱时取消加仓/买回动作。",
        "opening_confirmation": evidence.get("intraday") or "",
        "max_position_ratio": 0.0,
        "break_limit_action": "冲板失败、炸板无承接或放量回落，按弱于预期降风险。",
        "notes": evidence.get("volume_price_detail") or "",
        "board_strength": board_strength,
        "board_strength_detail": [board_strength, evidence.get("mainline_position") or "主线地位待确认"],
        "leader_support": leader_support,
        "limit_quality": limit_quality,
        "expectation_level": expectation_match,
        "strong_boundary_price": strong_boundary,
        "weak_reduce_price": weak_reduce,
        "weak_exit_price": weak_exit,
        "risk_notes": risk_notes,
        "intraday_status": evidence.get("intraday") or f"现价{current:.2f}，涨跌{change_pct:+.2f}%。",
        "expected_state": expected_state,
        "expectation_match": expectation_match,
        "operation_advice": operation_advice,
        "volume_price_status": volume_status,
        "next_day_script": next_day_script,
        "sell_trigger_cards": sell_trigger_cards,
        "refreshed_at": shanghai_now_naive().strftime("%Y-%m-%d %H:%M:%S"),
    }

def _dynamic_sell_trigger_cards(holding: Holding, evidence: dict[str, Any], quote: dict[str, Any], current: float) -> list[str]:
    prev_close = _safe_float(quote.get("prev_close"))
    high = _safe_float(quote.get("high"))
    change_pct = _safe_float(quote.get("change_pct"))
    if prev_close and high:
        high_change_pct = (high - prev_close) / prev_close * 100
        pullback = max(0.0, high_change_pct - change_pct)
    else:
        high_change_pct = 0.0
        pullback = 0.0
    estimated_vwap = _estimated_vwap(quote)
    below_vwap = bool(estimated_vwap and current < estimated_vwap)
    sector = str(evidence.get("sector") or "")
    triggers = _intraday_sell_triggers(
        holding=holding,
        current=current,
        high=high,
        high_change_pct=high_change_pct,
        change_pct=change_pct,
        pullback=pullback,
        below_vwap=below_vwap,
        sector=sector,
        sector_rank=0,
        sector_net=float(evidence.get("theme_flow_current") or 0),
        sector_main=0.0,
        sector_acc=0.0,
        sector_flow_peak=float(evidence.get("theme_flow_peak") or 0),
        sector_flow_current=float(evidence.get("theme_flow_current") or 0),
        sector_flow_pullback=float(evidence.get("theme_flow_pullback") or 0),
        sector_flow_pullback_pct=float(evidence.get("theme_flow_pullback_pct") or 0),
        strongest_name="其他强势方向",
        strongest_is_other=bool(evidence.get("is_underperforming")),
    )
    cards = [
        f"利润保护：{triggers['profit_protection_state']}",
        "板块退潮："
        + ("；".join(triggers["sector_ebb_trigger"]) if triggers["sector_ebb_trigger"] else "未触发，继续看板块订单流方向排名和主线前排。"),
        "个股弱化："
        + ("；".join(triggers["stock_weakening_trigger"]) if triggers["stock_weakening_trigger"] else "未触发，继续看是否守住分时均价/VWAP。"),
        "利润回撤："
        + ("；".join(triggers["profit_drawdown_trigger"]) if triggers["profit_drawdown_trigger"] else "未触发，未到规则减仓阈值。"),
        "接回条件：" + "；".join(triggers["buyback_trigger"]),
    ]
    if triggers["trigger_action"]:
        cards.insert(0, f"动作建议：{triggers['trigger_action']}")
    return cards

def _expectation_match_label(evidence: dict[str, Any], category: str) -> str:
    volume_status = str(evidence.get("volume_price_status") or "")
    if evidence.get("super_expectation") or (evidence.get("strong_open") and "拉升" in volume_status):
        return "强更强"
    if evidence.get("intraday_repair"):
        return "弱转强"
    if evidence.get("weak_open") and evidence.get("high_reject"):
        return "弱转强失败"
    if evidence.get("high_reject") or evidence.get("is_underperforming") or "大跌" in volume_status:
        return "弱于预期"
    if category in {"超预期", "强预期"} and "放量" in volume_status:
        return "符合预期偏强"
    return "符合预期"

def _dynamic_operation_advice(label: str, category: str, holding: Holding, current: float) -> str:
    if label in {"强更强", "符合预期偏强"}:
        return "持有核心仓为主，不主动卖飞；高位天量或偏离5日线过远时不扩大仓位。"
    if label == "弱转强":
        return "先观察翻红后能否站稳分时均价/VWAP，确认后只允许按计划买回，不追第一笔。"
    if label == "弱转强失败":
        return f"反抽不过分时均价/VWAP先减仓，跌回{max(holding.cost_price * 0.98, current * 0.97):.2f}附近继续降风险。"
    if label == "弱于预期":
        return f"不幻想，跌破{max(holding.cost_price * 0.98, current * 0.97):.2f}减仓，跌破{max(holding.cost_price * 0.96, current * 0.94):.2f}清仓。"
    return "持有观察，不加仓；迟迟不能走强或板块无助攻时减一部分风险。"

def _leader_support_for_holding(holding: Holding, evidence: dict[str, Any]) -> list[str]:
    sector = str(evidence.get("sector") or "")
    supports: list[str] = []
    radar = _get_current_theme_radar_cache()
    if radar is not None:
        for theme in radar.themes[:20]:
            if sector and sector in theme.name:
                supports.extend(
                    f"{role.name}({role.code}) {role.role} 涨跌{role.change_pct:+.2f}% 成交{role.amount:.2f}亿：{role.reason}"
                    for role in theme.core_stocks[:6]
                )
                break
    ladder = _get_response_cache(f"limit-up-ladder|{_last_trading_day()}")
    if ladder is not None:
        for cluster in ladder.clusters[:10]:
            if sector and (sector in cluster.name or holding.name in "、".join(cluster.stocks)):
                supports.append(f"{cluster.name}：{cluster.count}只涨停，最高{cluster.highest_level}板，前排{'、'.join(cluster.stocks[:6])}。")
    return list(dict.fromkeys(supports))[:8] or ["前后排助攻数据缺口：请刷新题材雷达/涨停天梯。"]

def _dynamic_risk_notes(evidence: dict[str, Any], holding: Holding, current: float) -> list[str]:
    notes: list[str] = []
    volume_ratio = _safe_float(evidence.get("volume_ratio"))
    ma5_deviation = _safe_float(evidence.get("ma5_deviation"))
    if volume_ratio >= 2:
        notes.append("高位/盘中放巨量：巨大分歧信号，继续转一致难度上升，只作为风险权重升高处理。")
    if ma5_deviation >= 8:
        notes.append(f"偏离5日线{ma5_deviation:.2f}%：禁止追高加仓，若开盘/竞价不能强势确认，按回踩均线风险处理。")
    if evidence.get("is_underperforming"):
        notes.append("现状弱于板块或订单流方向：个股独立行情持续性下降，涨停预期下调一级。")
    if evidence.get("leader_support_missing"):
        notes.append("前排/后排助攻不足：无梯队扩散时，次日必须个股强更强。")
    notes.append(f"弱于预期价格触发：跌破{max(holding.cost_price * 0.98, current * 0.97):.2f}减仓，跌破{max(holding.cost_price * 0.96, current * 0.94):.2f}清仓。")
    return list(dict.fromkeys(notes))

def _infer_expectation_category(holding: Holding, evidence: dict[str, Any] | None = None, quote: dict[str, Any] | None = None) -> str:
    evidence = evidence or {}
    base_category = _infer_holding_category(holding, evidence)
    if evidence.get("super_expectation"):
        return "超预期"
    if evidence.get("strong_open"):
        return "强预期"
    if evidence.get("intraday_repair"):
        return "弱转强"
    if evidence.get("high_reject") or base_category == "高位巨量分歧股":
        return "分歧转弱"
    if evidence.get("weak_open") or evidence.get("is_underperforming") or base_category in {"弱于预期股", "低价情绪股"}:
        return "弱于预期"
    return "符合预期"

def _infer_holding_category(holding: Holding, evidence: dict[str, Any] | None = None) -> str:
    evidence = evidence or {}
    text = f"{holding.position_type} {holding.next_discipline} {holding.name}"
    if "低价" in text or holding.current_price <= 5:
        return "低价情绪股"
    if evidence.get("is_high_divergence") or "高位" in text or "分歧" in text or "巨量" in text:
        return "高位巨量分歧股"
    if evidence.get("is_underperforming") or "退出" in text or "风险" in text or "亏损" in text or "弱于预期" in text:
        return "弱于预期股"
    if evidence.get("is_mainline_front") or "主线" in text or "龙头" in text or "前排" in text:
        return "主线前排股"
    return "震荡趋势股"

def _outperform_condition(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "高开后继续放量走强，回踩分时均价/VWAP不破，且强于板块。"
    if category == "弱转强":
        return "低开后快速翻红，重新站上分时均价/VWAP，板块同步修复。"
    if category == "主线前排股":
        return "板块继续强化，个股高开或快速站上确认位，放量突破且守住分时均价/VWAP"
    return "板块不退潮，个股站上确认位并放量突破，回踩分时均价/VWAP不破"

def _outperform_action(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "保留核心仓，冲高只按计划止盈，不因小波动做无意义高抛。"
    if category == "弱转强":
        return "先确认翻红后的承接，允许按计划买回已高抛部分，不能超过已卖股数。"
    if category == "主线前排股":
        return "继续持有核心仓，冲高只做分批止盈，不机械做T"
    if category in {"弱于预期", "分歧转弱", "弱于预期股", "高位巨量分歧股", "低价情绪股"}:
        return "冲高优先降风险，只处理卖出计划，不盲目加仓"
    return "按压力位分批高抛，买回必须等支撑承接确认"

def _expected_condition(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "高开或红盘震荡，有承接但未继续主动突破，仍站在关键确认位上方。"
    if category == "弱转强":
        return "翻红后围绕分时均价震荡，回落不破昨收或确认位。"
    return "板块未退潮，个股围绕确认位震荡，有承接但未主动突破"

def _expected_action(category: str) -> str:
    if category in {"符合预期", "弱转强", "震荡趋势股"}:
        return "允许按计划高抛低吸，买回必须等支撑缩量企稳"
    if category in {"强预期", "超预期", "主线前排股"}:
        return "保持核心仓，非关键压力位不做无意义高抛"
    return "以观察和减风险为主，不主动扩大仓位"

def _underperform_condition(category: str) -> str:
    if category in {"超预期", "强预期"}:
        return "高开后快速回落跌破分时均价/VWAP，无法重新收回确认位，明显弱于板块。"
    if category == "弱转强":
        return "翻红失败后再次跌回昨收/确认位下方，低点继续下移。"
    return "低开不修复、跌破确认位/减仓线、弱于板块、放量下跌或订单流方向明显转弱"

def _underperform_action(category: str) -> str:
    if category in {"强预期", "超预期", "主线前排股"}:
        return "跌破确认位先降仓，若板块同步退潮则退出非核心仓"
    return "先减仓或退出；反抽是卖出窗口，不默认接回"

def _plan_from_payload(payload: NextDayPlanCreate) -> NextDayPlan:
    plan = NextDayPlan(
        **payload.model_dump(
            exclude={
                "classification_basis",
                "forbidden_actions",
                "auction_plan",
                "market_value",
                "profit_amount",
                "profit_ratio",
                "price_source",
                "price_note",
            }
        ),
        classification_basis=payload.classification_basis.model_dump_json(),
        auction_plan=payload.auction_plan.model_dump_json(),
        forbidden_actions=json.dumps(payload.forbidden_actions, ensure_ascii=False),
    )
    if not plan.plan_date:
        plan.plan_date = _next_trade_date()
    _refresh_plan_risk(plan)
    return plan

def _refresh_plan_risk(plan: NextDayPlan) -> None:
    plan.risk_priority = _CATEGORY_RISK_PRIORITY.get(plan.holding_category, 9)
    if not plan.stop_loss_4pct and plan.cost_price:
        plan.stop_loss_4pct = round(plan.cost_price * 0.96, 2)
    warnings = _plan_warnings(plan)
    plan.risk_warnings = json.dumps(warnings, ensure_ascii=False)

def _plan_warnings(plan: NextDayPlan) -> list[str]:
    warnings: list[str] = []
    try:
        auction_plan = json.loads(plan.auction_plan or "{}")
    except json.JSONDecodeError:
        auction_plan = {}
    if plan.plan_type == "limit_up_auction":
        warnings.append("打板预案不是买入指令：9:20后封单和开盘承接不符合条件就撤单。")
        warnings.append("T+1风险：一旦炸板或高开低走，当日无法通过卖出纠错。")
    elif plan.plan_type == "holding":
        warnings.extend([
            "冲高兑现：到达计划区后仍须至少两类走弱证据共振，按25%-50%分批，不按单一浮盈猜顶。",
            "恐慌保护：极低位且未触发固定硬止损时禁止追卖；开板承接、V形、低点抬高或重回VWAP可撤销旧卖出结论。",
            "逆势补仓：不恐慌卖出≠允许抄底；市场闸门、板块转强、个股反转、风险收益比四项必须同时通过。",
        ])
    for item in auction_plan.get("risk_notes") or []:
        if item:
            warnings.append(str(item))
    if plan.trim_quantity > 0 and not plan.buyback_condition and plan.allow_buyback:
        warnings.append("没有买回条件却标记做T：本次高抛默认为减仓。")
    if plan.allow_buyback and plan.max_buyback_quantity > plan.trim_quantity:
        warnings.append("做T买回不能超过已卖出股数。")
    if plan.holding_category in {"弱于预期", "弱于预期股"} and plan.allow_buyback:
        warnings.append("弱于预期反抽优先减仓。")
    if plan.holding_category in {"分歧转弱", "高位巨量分歧股"} and plan.allow_buyback:
        warnings.append("分歧转弱需先缩量企稳，不做T扩大风险。")
    if plan.holding_category == "低价情绪股" and plan.allow_buyback:
        warnings.append("低价情绪股以退出为主。")
    if plan.trim_price and plan.buyback_price and plan.buyback_price > 0:
        spread = (plan.trim_price - plan.buyback_price) / plan.buyback_price
        if spread < 0.02:
            warnings.append("差价不足2%-3%：不值得做T。")
    return warnings

def _refresh_existing_holding_plans(
    plans: list[NextDayPlan],
    db: Session,
) -> dict[str, str]:
    holding_plans = [plan for plan in plans if plan.plan_type == "holding"]
    if not holding_plans:
        return {}
    codes = {str(plan.code).zfill(6) for plan in holding_plans}
    lookup_codes = set(codes | {code.lstrip("0") for code in codes if code.lstrip("0")})
    for code in codes:
        lookup_codes.update(_quote_code_candidates(code))
    holdings = db.query(Holding).filter(Holding.code.in_(lookup_codes)).all()
    holdings_by_code: dict[str, Holding] = {}
    for holding in holdings:
        holdings_by_code[str(holding.code).zfill(6)] = holding
        for candidate in _quote_code_candidates(holding.code):
            holdings_by_code[candidate] = holding
    account_total_asset = _account_total_asset(db)
    price_notes = _refresh_holding_prices(holdings, db)
    changed = False
    for plan in holding_plans:
        holding = holdings_by_code.get(str(plan.code).zfill(6))
        if holding is None:
            for candidate in _quote_code_candidates(plan.code):
                holding = holdings_by_code.get(candidate)
                if holding is not None:
                    break
        if holding is None:
            continue
        fresh = _default_next_day_plan(
            holding,
            plan.plan_date,
            account_total_asset,
            _latest_quote_for_holding(holding),
        )
        _sync_holding_plan(plan, fresh)
        changed = True
    if changed:
        db.commit()
        for plan in plans:
            db.refresh(plan)
    return price_notes

def _next_day_plan_out(plan: NextDayPlan, price_note: str = "") -> NextDayPlanOut:
    market_value = plan.quantity * plan.current_price
    profit_amount = (plan.current_price - plan.cost_price) * plan.quantity
    profit_ratio = (
        (plan.current_price - plan.cost_price) / plan.cost_price
        if plan.cost_price
        else 0.0
    )
    is_realtime = _is_realtime_note(price_note)
    return NextDayPlanOut(
        id=plan.id,
        plan_date=plan.plan_date,
        plan_type=plan.plan_type,
        holding_id=plan.holding_id,
        code=plan.code,
        name=plan.name,
        quantity=plan.quantity,
        cost_price=plan.cost_price,
        current_price=plan.current_price,
        market_value=round(market_value, 2),
        profit_amount=round(profit_amount, 2),
        profit_ratio=round(profit_ratio, 4),
        price_source="realtime" if is_realtime else "manual",
        price_note=price_note,
        position_ratio=plan.position_ratio,
        holding_category=plan.holding_category,
        risk_priority=plan.risk_priority,
        classification_basis=ClassificationBasis(**_json_obj(plan.classification_basis)),
        outperform_condition=plan.outperform_condition,
        outperform_action=plan.outperform_action,
        expected_condition=plan.expected_condition,
        expected_action=plan.expected_action,
        underperform_condition=plan.underperform_condition,
        underperform_action=plan.underperform_action,
        confirm_price=plan.confirm_price,
        trim_price=plan.trim_price,
        trim_condition=plan.trim_condition,
        trim_quantity=plan.trim_quantity,
        allow_buyback=plan.allow_buyback,
        buyback_price=plan.buyback_price,
        buyback_condition=plan.buyback_condition,
        max_buyback_quantity=plan.max_buyback_quantity,
        reduce_price=plan.reduce_price,
        final_risk_price=plan.final_risk_price,
        stop_loss_4pct=plan.stop_loss_4pct,
        limit_up_price=plan.limit_up_price,
        auction_plan=AuctionPlan(**_json_obj(plan.auction_plan)),
        forbidden_actions=_json_list(plan.forbidden_actions),
        risk_warnings=_json_list(plan.risk_warnings),
        review_expectation=plan.review_expectation,
        review_execution=plan.review_execution,
        review_deviation=plan.review_deviation,
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )
