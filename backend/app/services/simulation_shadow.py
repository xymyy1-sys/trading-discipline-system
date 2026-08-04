from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import hashlib
import json
import logging
import math
import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.helpers.decision import quote_for_code
from app.api.helpers.quotes import _normalize_code
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    ExpectationSnapshot,
    NextDayPlan,
    PositionExecutionState,
    SimulationAccount,
    SimulationDailyEquity,
    SimulationOrder,
    SimulationPosition,
    SimulationShadowDecision,
    VolumePriceSnapshot,
)
from app.schemas.simulation import SimulationOrderCreate
from app.services.simulation import (
    QuoteLoader,
    _is_trading_session,
    _quote_data_quality,
    _quote_time,
    _safe_float,
    mark_to_market,
    submit_order,
)
from app.services.autonomous_selection import latest_autonomous_selection


logger = logging.getLogger(__name__)
RULE_VERSION = "shadow-v2-conviction"
AI_TRADER_AUTOMATION_KEY = "codex-ai-paper-trader-v1"
AI_TRADER_ACCOUNT_NAME = "Codex AI模拟交易员（2万元）"
AI_TRADER_INITIAL_CASH = 20_000
MAX_SIGNAL_AGE_SECONDS = 15 * 60
MAX_EXPECTATION_AGE_SECONDS = 6 * 60 * 60
MAX_PLAN_AGE_SECONDS = 36 * 60 * 60
MAX_QUOTE_AGE_SECONDS = 2 * 60
EXPLORATION_START = time(10, 0)
EXPLORATION_END = time(14, 30)
EXPLORATION_POSITION_RATIO = 0.50
MAX_ENTRY_POSITION_RATIO = 0.85


@dataclass(frozen=True)
class SourceDependency:
    label: str
    observed_at: datetime | None
    max_age_seconds: int | None
    require_current_date: bool = True


@dataclass(frozen=True)
class ShadowCandidate:
    strategy_source: str
    source_kind: str
    source_id: int | None
    source_version: str
    source_at: datetime | None
    code: str
    name: str
    intent: str
    side: str
    ratio: float
    ready: bool
    reason: str
    evidence: tuple[str, ...] = ()
    dependencies: tuple[SourceDependency, ...] = ()
    invalidation_price: float = 0.0
    risk_budget_ratio: float = 0.03


@dataclass
class ShadowRunResult:
    account_id: int
    evaluated_at: datetime
    order_ids: list[int] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    duplicate_signal_keys: list[str] = field(default_factory=list)


@dataclass
class ShadowEquityResult:
    evaluated_at: datetime
    equity_ids: list[int] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)


def get_or_create_ai_trader_account(db: Session) -> SimulationAccount:
    """Return the dedicated 20k forward-only paper trading account.

    The account is intentionally separated by ``automation_key`` so the user's
    manual simulation experiments do not contaminate the AI trader's daily
    record.  Existing accounts are never reset: once the forward test starts,
    preserving its PnL path is more important than matching a nominal balance.
    """

    row = db.query(SimulationAccount).filter(
        SimulationAccount.automation_key == AI_TRADER_AUTOMATION_KEY,
    ).first()
    if row is not None:
        changed = False
        if row.name != AI_TRADER_ACCOUNT_NAME:
            row.name = AI_TRADER_ACCOUNT_NAME
            changed = True
        if row.account_type != "shadow":
            row.account_type = "shadow"
            changed = True
        if row.status != "active":
            row.status = "active"
            changed = True
        if changed:
            db.add(row)
            db.commit()
            db.refresh(row)
        return row
    row = SimulationAccount(
        name=AI_TRADER_ACCOUNT_NAME,
        initial_cash=AI_TRADER_INITIAL_CASH,
        cash=AI_TRADER_INITIAL_CASH,
        account_type="shadow",
        automation_key=AI_TRADER_AUTOMATION_KEY,
        status="active",
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        # Multiple API workers may race on the first minute after deployment.
        # The unique automation key makes the operation idempotent.
        db.rollback()
        existing = db.query(SimulationAccount).filter(
            SimulationAccount.automation_key == AI_TRADER_AUTOMATION_KEY,
        ).first()
        if existing is None:
            raise
        return existing


def _local(value: datetime | None) -> datetime | None:
    return shanghai_now_naive(value) if value is not None else None


def _version_time(value: datetime | None) -> str:
    converted = _local(value)
    return converted.isoformat(timespec="seconds") if converted else "missing"


def _stable_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def _semantic_text(value: str) -> str:
    """Remove changing prices/times while retaining the evidence category."""
    collapsed = re.sub(r"[+-]?\d+(?:\.\d+)?%?", "#", str(value or ""))
    return " ".join(collapsed.split())


def _semantic_evidence(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_semantic_text(value) for value in values if _semantic_text(value)}))


def _latest_by_code(rows: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for row in rows:
        code = _normalize_code(str(row.code or ""))
        if code and code not in result:
            result[code] = row
    return result


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _json_dict(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _dependencies_are_fresh(candidate: ShadowCandidate, evaluated_at: datetime) -> tuple[bool, str]:
    if not candidate.dependencies:
        return False, "信号缺少可审计的依赖版本"
    for dependency in candidate.dependencies:
        observed_at = _local(dependency.observed_at)
        if observed_at is None:
            return False, f"{dependency.label}缺少可审计的生成时间"
        if observed_at > evaluated_at:
            return False, f"{dependency.label}晚于评估时点，拒绝未来数据"
        if dependency.require_current_date and observed_at.date() != evaluated_at.date():
            return False, f"{dependency.label}不是当前交易日数据，禁止回填历史模拟成交"
        age = (evaluated_at - observed_at).total_seconds()
        if dependency.max_age_seconds is not None and age > dependency.max_age_seconds:
            return False, (
                f"{dependency.label}已陈旧（{age:.0f}秒，阈值"
                f"{dependency.max_age_seconds}秒），等待新证据版本"
            )
    return True, ""


def _quality_ok(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"realtime", "real", "live", "真实", "实时"}


def _positive_expectation(row: ExpectationSnapshot) -> bool:
    result = str(row.expectation_result or "").upper()
    transition = str(row.state_transition or "").upper()
    positive_tokens = ("STRONG", "OUTPERFORM", "BETTER", "POSITIVE", "BEAT", "转强", "超预期")
    return int(row.expectation_gap_score or 0) >= 8 and any(
        token in f"{result} {transition}" for token in positive_tokens
    )


def _negative_expectation(row: ExpectationSnapshot) -> bool:
    result = str(row.expectation_result or "").upper()
    transition = str(row.state_transition or "").upper()
    negative_tokens = ("WEAK", "UNDERPERFORM", "NEGATIVE", "FAIL", "INVALID", "证伪", "弱于")
    return int(row.expectation_gap_score or 0) <= -8 and any(
        token in f"{result} {transition}" for token in negative_tokens
    )


def _positive_volume(row: VolumePriceSnapshot | None) -> bool:
    if row is None or not _quality_ok(row.data_quality):
        return False
    pattern = str(row.pattern or "").upper()
    negative = ("BROKEN", "WEAK", "DOWN", "FAIL", "跌破", "转弱", "下跌", "诱多", "待确认")
    if any(token in pattern for token in negative):
        return False
    positive = (
        "BREAKOUT", "RECLAIM", "SUPPORT", "V_REVERSE", "VOLUME_REBOUND",
        "STRENGTH_CONFIRMED", "突破", "站回", "支撑", "V形", "放量上涨",
    )
    pattern_confirmed = any(token in pattern for token in positive)
    numeric_confirmed = bool(
        row.vwap_reliable
        and float(row.price_vs_vwap or 0) >= 0
        and (float(row.volume_acceleration or 0) > 0 or float(row.attack_efficiency or 0) > 0)
    )
    return pattern_confirmed or numeric_confirmed


def _negative_volume(row: VolumePriceSnapshot | None) -> bool:
    if row is None or not _quality_ok(row.data_quality):
        return False
    pattern = str(row.pattern or "").upper()
    tokens = (
        "VWAP_BROKEN", "BREAKDOWN", "VOLUME_DOWN", "WEAKNESS", "DISTRIBUTION",
        "跌破", "放量下跌", "量价转弱", "资金流出加速", "冲高回落",
    )
    pattern_confirmed = any(token in pattern for token in tokens)
    numeric_confirmed = bool(
        row.vwap_reliable
        and float(row.price_vs_vwap or 0) < 0
        and float(row.active_sell_amount or 0) > float(row.active_buy_amount or 0) > 0
    )
    return pattern_confirmed or numeric_confirmed


def _exploration_volume(row: VolumePriceSnapshot | None) -> bool:
    """A softer but still real-data confirmation used for one daily sample."""
    if row is None or not _quality_ok(row.data_quality) or not row.vwap_reliable:
        return False
    pattern = str(row.pattern or "").upper()
    forbidden = (
        "BROKEN", "BREAKDOWN", "DISTRIBUTION", "FAIL", "WEAKNESS",
        "跌破", "放量下跌", "量价转弱", "资金流出加速", "冲高回落", "诱多",
    )
    if any(token in pattern for token in forbidden):
        return False
    if float(row.price_vs_vwap or 0) < -0.3:
        return False
    buy = float(row.active_buy_amount or 0)
    sell = float(row.active_sell_amount or 0)
    flow_not_hostile = buy <= 0 or sell <= 0 or buy >= sell * 0.85
    momentum_not_negative = float(row.volume_acceleration or 0) >= 0 or float(row.attack_efficiency or 0) >= 0
    return flow_not_hostile and momentum_not_negative


def _autonomous_position_ratio(score: float, maximum_entries: int) -> tuple[float, str]:
    """Translate conviction into a meaningful, portfolio-safe allocation.

    A strong signal should not be reduced to a token 100-share trade.  At the
    same time, a regime that allows several concurrent names must reserve room
    for the other approved entries instead of overcommitting the same cash.
    """
    if score >= 90:
        raw_ratio, tier = 0.70, "主攻仓"
    elif score >= 82:
        raw_ratio, tier = 0.50, "确认仓"
    else:
        raw_ratio, tier = 0.35, "进攻仓"
    portfolio_slot = MAX_ENTRY_POSITION_RATIO / max(maximum_entries, 1)
    return min(raw_ratio, portfolio_slot), tier


def _execution_candidate(row: PositionExecutionState) -> ShadowCandidate | None:
    action = str(row.recommended_action or "").strip()
    state = str(row.state or "").upper()
    evidence = tuple(_json_list(row.evidence_json))
    counter_evidence = tuple(_json_list(row.counter_evidence_json))
    invalid = tuple(_json_list(row.invalid_conditions_json))
    recovery = tuple(_json_list(row.recovery_conditions_json))
    all_text = f"{state} {action} {' '.join(evidence)}"
    exit_all = any(token in all_text for token in ("EXIT_REQUIRED", "HARD_STOP", "全部退出", "清仓", "必须退出"))
    reduce_signal = any(token in all_text for token in ("REDUCE", "减仓", "降低仓位", "只留观察仓", "分批卖出"))
    entry_signal = any(token in all_text for token in ("ADD_ALLOWED", "BUY_CONFIRMED", "允许加仓", "确认买入", "加仓25%"))
    prohibited_entry = any(token in f"{action} {' '.join(invalid)}" for token in ("禁止", "不加仓", "观察但", "只留"))
    # A non-real-time signal is recorded as SKIPPED, but must not permanently
    # consume the same signal once its data becomes real-time.  A two-state
    # readiness bucket gives that recovery a new version while repeated
    # real-time refreshes remain idempotent.
    readiness = "realtime" if _quality_ok(row.data_quality) else "not_realtime"
    semantic_version = _stable_fingerprint(
        {
            "state": state,
            "expectation_state": row.expectation_state,
            "volume_price_state": row.volume_price_state,
            "sector_state": row.sector_state,
            "action": _semantic_text(action),
            "reduce_ratio": round(float(row.recommended_reduce_ratio or 0), 4),
            "recommended_position_ratio": round(float(row.recommended_position_ratio or 0), 4),
            "evidence": _semantic_evidence(evidence),
            "counter_evidence": _semantic_evidence(counter_evidence),
            "invalid": _semantic_evidence(invalid),
            "recovery": _semantic_evidence(recovery),
            "readiness": readiness,
        }
    )
    state_dependency = (
        SourceDependency("持仓执行状态", row.updated_at, MAX_SIGNAL_AGE_SECONDS),
    )
    if exit_all or reduce_signal:
        ratio = 1.0 if exit_all else float(row.recommended_reduce_ratio or 0)
        if ratio <= 0:
            ratio = 0.75 if "只留观察仓" in action else 0.25
        ratio = min(max(ratio, 0.01), 1.0)
        return ShadowCandidate(
            strategy_source="holding_execution",
            source_kind="position_execution_state",
            source_id=row.id,
            source_version=f"execution:{semantic_version}",
            source_at=row.updated_at,
            code=_normalize_code(row.code),
            name=row.name,
            intent="EXIT",
            side="SELL",
            ratio=ratio,
            ready=readiness == "realtime",
            reason="持仓执行状态给出明确退出/减仓信号" if _quality_ok(row.data_quality) else "持仓执行信号不是实时可信数据",
            evidence=evidence + (f"执行状态={state}", f"动作={action}"),
            dependencies=state_dependency,
            invalidation_price=max(float(row.hard_stop_price or 0), float(row.structure_stop_price or 0)),
        )
    if entry_signal and not prohibited_entry:
        add_ratio = float(row.recommended_position_ratio or 0)
        add_ratio = min(max(add_ratio if add_ratio > 0 else 0.35, 0.20), 0.50)
        return ShadowCandidate(
            strategy_source="holding_execution",
            source_kind="position_execution_state",
            source_id=row.id,
            source_version=f"execution:{semantic_version}",
            source_at=row.updated_at,
            code=_normalize_code(row.code),
            name=row.name,
            intent="ENTER",
            side="BUY",
            ratio=add_ratio,
            ready=readiness == "realtime",
            reason="持仓执行状态给出明确加仓确认" if _quality_ok(row.data_quality) else "持仓执行信号不是实时可信数据",
            evidence=evidence + (f"执行状态={state}", f"动作={action}"),
            dependencies=state_dependency,
            invalidation_price=max(float(row.hard_stop_price or 0), float(row.structure_stop_price or 0)),
        )
    return None


def _expectation_candidate(
    expectation: ExpectationSnapshot,
    volume: VolumePriceSnapshot | None,
) -> ShadowCandidate | None:
    positive = _positive_expectation(expectation)
    negative = _negative_expectation(expectation)
    if not positive and not negative:
        return None
    volume_at = volume.captured_at if volume else None
    source_at = max(
        [value for value in (_local(expectation.created_at), _local(volume_at)) if value is not None],
        default=None,
    )
    version = f"e{expectation.id}:v{getattr(volume, 'id', 0)}"
    dependencies = (
        SourceDependency("预期快照", expectation.created_at, MAX_EXPECTATION_AGE_SECONDS),
        SourceDependency(
            "量价快照",
            getattr(volume, "captured_at", None),
            MAX_SIGNAL_AGE_SECONDS,
        ),
    )
    evidence = tuple(_json_list(expectation.evidence_json))
    if volume:
        evidence += tuple(_json_list(volume.evidence_json))
        evidence += (f"量价形态={volume.pattern}",)
    if positive:
        confirmed = _positive_volume(volume)
        gap_score = int(expectation.expectation_gap_score or 0)
        if gap_score >= 18:
            entry_ratio, position_tier = 0.65, "主攻仓"
        elif gap_score >= 12:
            entry_ratio, position_tier = 0.50, "确认仓"
        else:
            entry_ratio, position_tier = 0.35, "进攻仓"
        return ShadowCandidate(
            strategy_source="expectation_volume_price",
            source_kind="expectation_volume_pair",
            source_id=expectation.id,
            source_version=version,
            source_at=source_at,
            code=_normalize_code(expectation.code),
            name=expectation.name or getattr(volume, "name", ""),
            intent="ENTER",
            side="BUY",
            ratio=entry_ratio,
            ready=confirmed,
            reason=f"正预期差与真实量价确认共振，按{position_tier}{entry_ratio:.0%}执行" if confirmed else "正预期差尚未获得真实量价确认",
            evidence=evidence + (f"预期差={expectation.expectation_gap_score}", f"仓位等级={position_tier}"),
            dependencies=dependencies,
            invalidation_price=float(getattr(volume, "vwap", 0) or 0) * 0.99,
        )
    confirmed = _negative_volume(volume)
    return ShadowCandidate(
        strategy_source="expectation_volume_price",
        source_kind="expectation_volume_pair",
        source_id=expectation.id,
        source_version=version,
        source_at=source_at,
        code=_normalize_code(expectation.code),
        name=expectation.name or getattr(volume, "name", ""),
        intent="EXIT",
        side="SELL",
        ratio=1.0 if int(expectation.expectation_gap_score or 0) <= -18 else 0.5,
        ready=confirmed,
        reason="负预期差与量价转弱共同确认失效/降风险" if confirmed else "负预期差尚未获得量价转弱确认",
        evidence=evidence + (f"预期差={expectation.expectation_gap_score}",),
        dependencies=dependencies,
    )


def _limit_up_candidate(
    plan: NextDayPlan,
    volume: VolumePriceSnapshot | None,
    quote: dict[str, Any],
) -> ShadowCandidate:
    quote_at = _quote_time(quote)
    source_values = (
        _local(plan.updated_at),
        _local(getattr(volume, "captured_at", None)),
        _local(quote_at),
    )
    source_at = max([value for value in source_values if value], default=None)
    price = _safe_float(quote.get("price"))
    limit_price = _safe_float(quote.get("limit_up_price")) or float(plan.limit_up_price or 0)
    at_board = bool(limit_price > 0 and price >= limit_price * 0.995)
    volume_confirmed = _positive_volume(volume)
    ready = at_board and volume_confirmed
    evidence = tuple(_json_list(getattr(volume, "evidence_json", "[]"))) if volume else ()
    evidence += (
        f"当前价={price:.2f}",
        f"涨停参考价={limit_price:.2f}",
        f"量价形态={getattr(volume, 'pattern', '缺失')}",
    )
    auction_plan = _json_dict(plan.auction_plan)
    configured_ratio = _safe_float(auction_plan.get("max_position_ratio"))
    limit_up_ratio = min(configured_ratio, 0.50) if configured_ratio > 0 else 0.35
    return ShadowCandidate(
        strategy_source="limit_up",
        source_kind="limit_up_plan_confirmation",
        source_id=plan.id,
        source_version=f"p{plan.id}:v{getattr(volume, 'id', 0)}:q{_version_time(quote_at)}",
        source_at=source_at,
        code=_normalize_code(plan.code),
        name=plan.name,
        intent="ENTER",
        side="BUY",
        ratio=limit_up_ratio,
        ready=ready,
        reason="触及涨停区且量价确认，生成影子打板委托" if ready else "打板预案尚未同时满足触板与真实量价确认",
        evidence=evidence,
        dependencies=(
            SourceDependency("当日打板预案", plan.updated_at, MAX_PLAN_AGE_SECONDS, require_current_date=False),
            SourceDependency("量价快照", getattr(volume, "captured_at", None), MAX_SIGNAL_AGE_SECONDS),
            SourceDependency("实时行情", quote_at, MAX_QUOTE_AGE_SECONDS),
        ),
        invalidation_price=float(getattr(volume, "vwap", 0) or 0) * 0.99,
    )


def _autonomous_candidates(
    db: Session,
    evaluated_at: datetime,
    volumes: dict[str, VolumePriceSnapshot],
) -> list[ShadowCandidate]:
    """Promote a bounded number of all-market candidates after volume proof.

    The independent scan is broader than every product pool, but it remains a
    discovery layer.  A fresh real minute snapshot and an open market-risk gate
    are mandatory before a paper order can be created.
    """

    payload = latest_autonomous_selection(
        db,
        trade_date=evaluated_at.date().isoformat(),
        max_age=timedelta(seconds=MAX_SIGNAL_AGE_SECONDS),
    )
    if not payload:
        return []
    gate = payload.get("gate") or {}
    try:
        captured_at = datetime.fromisoformat(str(payload.get("captured_at") or ""))
    except (TypeError, ValueError):
        return []
    account = db.query(SimulationAccount).filter(
        SimulationAccount.automation_key == AI_TRADER_AUTOMATION_KEY,
    ).first()
    available_cash = float(account.cash or 0) if account else 0.0
    maximum = min(max(int(gate.get("max_entries") or 0), 0), 3) if bool(gate.get("allow_entry")) else 0
    result: list[ShadowCandidate] = []
    for item in list(payload.get("items") or []):
        if len(result) >= maximum:
            break
        code = _normalize_code(str(item.get("code") or ""))
        score = float(item.get("score") or 0)
        position_ratio, position_tier = _autonomous_position_ratio(score, maximum)
        # A-share orders require at least one 100-share lot.  Do not let
        # unaffordable high-priced names consume the three candidate slots.
        if float(item.get("price") or 0) * 100 > available_cash * position_ratio:
            continue
        volume = volumes.get(code)
        confirmed = _positive_volume(volume)
        reasons = tuple(str(value) for value in item.get("reasons") or [] if str(value).strip())
        risks = tuple(str(value) for value in item.get("risks") or [] if str(value).strip())
        source_tags = tuple(str(value) for value in item.get("source_tags") or [] if str(value).strip())
        result.append(ShadowCandidate(
            strategy_source="expectation_volume_price",
            source_kind="autonomous_universe_selection",
            source_id=int(payload.get("snapshot_id") or 0) or None,
            source_version=f"a{payload.get('snapshot_id', 0)}:v{getattr(volume, 'id', 0)}",
            source_at=max(
                value for value in (captured_at, _local(getattr(volume, "captured_at", None))) if value is not None
            ),
            code=code,
            name=str(item.get("name") or ""),
            intent="ENTER",
            side="BUY",
            ratio=position_ratio,
            ready=confirmed,
            reason=(
                f"全A独立评分{score:.1f}分，{item.get('style') or '量价候选'}，真实分钟量价确认，按{position_tier}{position_ratio:.0%}执行"
                if confirmed
                else f"全A独立评分{score:.1f}分，但尚未获得真实分钟量价确认"
            ),
            evidence=reasons + risks + tuple(f"来源标签={value}" for value in source_tags) + (
                f"市场闸门={gate.get('reason') or '缺失'}",
                f"失效条件={item.get('invalidation') or '跌破分时均价并放量转弱'}",
            ),
            dependencies=(
                SourceDependency("全A独立选股快照", captured_at, MAX_SIGNAL_AGE_SECONDS),
                SourceDependency("分钟量价快照", getattr(volume, "captured_at", None), MAX_SIGNAL_AGE_SECONDS),
            ),
            invalidation_price=float(getattr(volume, "vwap", 0) or 0) * 0.99,
        ))

    ready_normal = any(candidate.ready for candidate in result)
    current_time = evaluated_at.time()
    if ready_normal or not (EXPLORATION_START <= current_time <= EXPLORATION_END) or account is None:
        return result
    has_position = db.query(SimulationPosition.id).filter(
        SimulationPosition.account_id == account.id,
        SimulationPosition.quantity > 0,
    ).first() is not None
    has_open_buy = db.query(SimulationOrder.id).filter(
        SimulationOrder.account_id == account.id,
        SimulationOrder.side == "BUY",
        SimulationOrder.status.in_(("PENDING", "OPEN", "PROCESSING")),
    ).first() is not None
    sampled_today = db.query(SimulationShadowDecision.id).filter(
        SimulationShadowDecision.account_id == account.id,
        SimulationShadowDecision.trade_date == evaluated_at.date().isoformat(),
        SimulationShadowDecision.source_kind == "autonomous_exploration_sample",
    ).first() is not None
    if has_position or has_open_buy or sampled_today:
        return result

    for item in list(payload.get("exploration_items") or []):
        code = _normalize_code(str(item.get("code") or ""))
        price = float(item.get("price") or 0)
        if price <= 0 or price * 100 > available_cash * EXPLORATION_POSITION_RATIO:
            continue
        volume = volumes.get(code)
        if not _exploration_volume(volume):
            continue
        reasons = tuple(str(value) for value in item.get("reasons") or [] if str(value).strip())
        risks = tuple(str(value) for value in item.get("risks") or [] if str(value).strip())
        source_tags = tuple(str(value) for value in item.get("source_tags") or [] if str(value).strip())
        result.append(ShadowCandidate(
            strategy_source="expectation_volume_price",
            source_kind="autonomous_exploration_sample",
            source_id=int(payload.get("snapshot_id") or 0) or None,
            source_version=f"explore:{evaluated_at.date().isoformat()}:{code}:v{getattr(volume, 'id', 0)}",
            source_at=max(value for value in (captured_at, _local(getattr(volume, "captured_at", None))) if value is not None),
            code=code,
            name=str(item.get("name") or ""),
            intent="ENTER",
            side="BUY",
            ratio=EXPLORATION_POSITION_RATIO,
            ready=True,
            reason=f"每日探索样本：全A评分{float(item.get('score') or 0):.1f}分，正常策略尚未成交，使用确认仓50%验证",
            evidence=reasons + risks + tuple(f"来源标签={value}" for value in source_tags) + (
                "样本类型=探索；仓位等级=确认仓50%；与正式策略分开审计",
                f"市场闸门={gate.get('reason') or '缺失'}",
                f"量价形态={getattr(volume, 'pattern', '中性确认')}",
                f"失效条件={item.get('invalidation') or '跌破分时均价且主动卖出增强'}",
            ),
            dependencies=(
                SourceDependency("全A探索候选快照", captured_at, MAX_SIGNAL_AGE_SECONDS),
                SourceDependency("探索分钟量价快照", getattr(volume, "captured_at", None), MAX_SIGNAL_AGE_SECONDS),
            ),
            invalidation_price=float(getattr(volume, "vwap", 0) or 0) * 0.99,
            risk_budget_ratio=0.02,
        ))
        break
    return result


def _signal_key(account_id: int, candidate: ShadowCandidate, trade_date: str) -> str:
    # The database unique tuple includes account_id as well; retaining it in
    # the key makes exported audit records self-contained.
    return "|".join(
        (
            str(account_id), candidate.strategy_source, candidate.code, trade_date,
            RULE_VERSION, candidate.source_version, candidate.side,
        )
    )[:160]


def _claim_decision(
    db: Session,
    account: SimulationAccount,
    candidate: ShadowCandidate,
    evaluated_at: datetime,
) -> tuple[SimulationShadowDecision | None, bool]:
    trade_date = evaluated_at.date().isoformat()
    key = _signal_key(account.id, candidate, trade_date)
    existing = db.query(SimulationShadowDecision).filter(
        SimulationShadowDecision.account_id == account.id,
        SimulationShadowDecision.signal_key == key,
    ).first()
    if existing is not None:
        return existing, True
    row = SimulationShadowDecision(
        account_id=account.id,
        signal_key=key,
        strategy_source=candidate.strategy_source,
        source_kind=candidate.source_kind,
        source_id=candidate.source_id,
        rule_version=RULE_VERSION,
        source_version=candidate.source_version,
        trade_date=trade_date,
        source_at=_local(candidate.source_at),
        evaluated_at=evaluated_at,
        code=candidate.code,
        name=candidate.name,
        intent=candidate.intent,
        side=candidate.side,
        quantity=0,
        status="CLAIMED",
        reason=candidate.reason,
        evidence_json=json.dumps(list(candidate.evidence), ensure_ascii=False),
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        duplicate = db.query(SimulationShadowDecision).filter(
            SimulationShadowDecision.account_id == account.id,
            SimulationShadowDecision.signal_key == key,
        ).first()
        return duplicate, True
    return row, False


def _position(db: Session, account_id: int, code: str) -> SimulationPosition | None:
    return db.query(SimulationPosition).filter(
        SimulationPosition.account_id == account_id,
        SimulationPosition.code == code,
        SimulationPosition.quantity > 0,
    ).first()


def _has_open_order(db: Session, account_id: int, code: str, side: str) -> bool:
    return db.query(SimulationOrder.id).filter(
        SimulationOrder.account_id == account_id,
        SimulationOrder.code == code,
        SimulationOrder.side == side,
        SimulationOrder.status.in_(("PENDING", "OPEN", "PROCESSING")),
    ).first() is not None


def _entry_quantity(account: SimulationAccount, price: float, ratio: float) -> int:
    budget = max(float(account.cash or 0), 0) * min(max(ratio, 0.01), MAX_ENTRY_POSITION_RATIO)
    return max(int(math.floor(budget / max(price, 0.01) / 100) * 100), 0)


def _risk_adjusted_entry_ratio(
    price: float,
    conviction_ratio: float,
    invalidation_price: float,
    risk_budget_ratio: float,
) -> tuple[float, float, bool]:
    """Cap conviction sizing by the loss at the frozen invalidation price.

    When a strategy cannot provide a numeric invalidation level we assume an
    8% adverse move rather than pretending the trade has no downside.  This
    keeps high-conviction sizing meaningful while bounding one-trade damage.
    """
    inferred = not (price > 0 and 0 < invalidation_price < price)
    stop_distance_ratio = (
        (price - invalidation_price) / price
        if not inferred
        else 0.08
    )
    stop_distance_ratio = min(max(stop_distance_ratio, 0.01), 0.30)
    risk_cap = min(max(risk_budget_ratio, 0.005), 0.05) / stop_distance_ratio
    effective = min(
        max(conviction_ratio, 0.01),
        max(risk_cap, 0.01),
        MAX_ENTRY_POSITION_RATIO,
    )
    return effective, stop_distance_ratio, inferred


def _exit_quantity(position: SimulationPosition, ratio: float, trade_date: str) -> int:
    available = (
        int(position.quantity or 0)
        if position.last_rollover_date != trade_date
        else int(position.available_quantity or 0)
    )
    if available <= 0:
        return 0
    if ratio >= 0.999:
        return available
    target = int(available * min(max(ratio, 0.01), 1.0))
    rounded = target // 100 * 100
    return rounded if rounded > 0 else (100 if available >= 100 else available)


def _discover_candidates(
    db: Session,
    evaluated_at: datetime,
    quote_loader: QuoteLoader,
) -> tuple[list[ShadowCandidate], dict[str, dict[str, Any]]]:
    trade_date = evaluated_at.date().isoformat()
    volumes = _latest_by_code(
        db.query(VolumePriceSnapshot)
        .filter(VolumePriceSnapshot.trade_date == trade_date, VolumePriceSnapshot.captured_at <= evaluated_at)
        .order_by(VolumePriceSnapshot.captured_at.desc(), VolumePriceSnapshot.id.desc())
        .all()
    )
    candidates: list[ShadowCandidate] = []
    execution_rows = (
        db.query(PositionExecutionState)
        .filter(PositionExecutionState.trade_date == trade_date, PositionExecutionState.updated_at <= evaluated_at)
        .order_by(PositionExecutionState.updated_at.desc(), PositionExecutionState.id.desc())
        .all()
    )
    for row in _latest_by_code(execution_rows).values():
        candidate = _execution_candidate(row)
        if candidate:
            candidates.append(candidate)

    expectation_rows = (
        db.query(ExpectationSnapshot)
        .filter(ExpectationSnapshot.trade_date == trade_date, ExpectationSnapshot.created_at <= evaluated_at)
        .order_by(ExpectationSnapshot.created_at.desc(), ExpectationSnapshot.id.desc())
        .all()
    )
    for code, expectation in _latest_by_code(expectation_rows).items():
        candidate = _expectation_candidate(expectation, volumes.get(code))
        if candidate:
            candidates.append(candidate)

    quote_cache: dict[str, dict[str, Any]] = {}
    plans = (
        db.query(NextDayPlan)
        .filter(NextDayPlan.plan_date == trade_date, NextDayPlan.plan_type == "limit_up_auction")
        .order_by(NextDayPlan.updated_at.desc(), NextDayPlan.id.desc())
        .all()
    )
    for code, plan in _latest_by_code(plans).items():
        quote_cache[code] = quote_loader(code) or {}
        candidates.append(_limit_up_candidate(plan, volumes.get(code), quote_cache[code]))
    candidates.extend(_autonomous_candidates(db, evaluated_at, volumes))
    # Exits must reserve the simulated holding before any same-run entry signal.
    candidates.sort(key=lambda item: (0 if item.side == "SELL" else 1, item.code, item.strategy_source))
    return candidates, quote_cache


def run_shadow_experiments(
    db: Session,
    account: SimulationAccount,
    *,
    now: datetime | None = None,
    quote_loader: QuoteLoader = quote_for_code,
) -> ShadowRunResult:
    """Turn current, confirmed strategy signals into auditable paper orders.

    This function deliberately *only* calls the simulation ledger.  It never
    invokes a broker/live-trading route, never matches on the decision bar and
    never consumes a previous trading day's source row.
    """
    evaluated_at = shanghai_now_naive(now)
    result = ShadowRunResult(account_id=account.id, evaluated_at=evaluated_at)
    if account.status != "active":
        result.skipped.append({"code": "*", "reason": "模拟账户未启用"})
        return result
    if not _is_trading_session(evaluated_at):
        result.skipped.append({"code": "*", "reason": "仅在连续竞价时段生成影子委托"})
        return result

    candidates, quote_cache = _discover_candidates(db, evaluated_at, quote_loader)
    if not candidates:
        result.skipped.append({"code": "*", "reason": "当前没有可验证的明确策略信号"})
        return result

    trade_date = evaluated_at.date().isoformat()
    for candidate in candidates:
        decision, duplicate = _claim_decision(db, account, candidate, evaluated_at)
        if duplicate:
            if decision is not None:
                result.duplicate_signal_keys.append(decision.signal_key)
            continue
        assert decision is not None
        fresh, stale_reason = _dependencies_are_fresh(candidate, evaluated_at)
        if not fresh or not candidate.ready:
            decision.status = "SKIPPED"
            decision.reason = stale_reason or candidate.reason
            db.commit()
            result.skipped.append({"code": candidate.code, "reason": decision.reason})
            continue

        quote = quote_cache.get(candidate.code)
        if quote is None:
            quote = quote_loader(candidate.code) or {}
            quote_cache[candidate.code] = quote
        quality = _quote_data_quality(quote, evaluated_at)
        if quality != "realtime":
            decision.status = "SKIPPED"
            decision.reason = f"实时行情质量不合格（{quality}），不生成模拟成交机会"
            db.commit()
            result.skipped.append({"code": candidate.code, "reason": decision.reason})
            continue

        position = _position(db, account.id, candidate.code)
        if candidate.side == "BUY":
            if position is not None or _has_open_order(db, account.id, candidate.code, "BUY"):
                decision.status = "SKIPPED"
                decision.reason = "模拟账户已有该标的仓位或待成交买单，禁止叠加影子样本"
                db.commit()
                result.skipped.append({"code": candidate.code, "reason": decision.reason})
                continue
            entry_price = _safe_float(quote.get("price"))
            effective_ratio, stop_distance_ratio, inferred_stop = _risk_adjusted_entry_ratio(
                entry_price,
                candidate.ratio,
                candidate.invalidation_price,
                candidate.risk_budget_ratio,
            )
            quantity = _entry_quantity(account, entry_price, effective_ratio)
            sizing_reason = (
                f"仓位按置信度上限{candidate.ratio:.0%}与单笔风险预算{candidate.risk_budget_ratio:.0%}共同计算，"
                f"失效距离{stop_distance_ratio:.2%}，实际资金上限{effective_ratio:.0%}"
                + ("（缺少有效数值止损，按8%保守距离估算）" if inferred_stop else f"（失效价{candidate.invalidation_price:.2f}）")
            )
            decision.reason = f"{candidate.reason}；{sizing_reason}"
            decision.evidence_json = json.dumps(list(candidate.evidence) + [sizing_reason], ensure_ascii=False)
            if quantity <= 0:
                decision.status = "SKIPPED"
                decision.reason = "按仓位上限和当前价格计算不足一手，跳过信号"
                db.commit()
                result.skipped.append({"code": candidate.code, "reason": decision.reason})
                continue
        else:
            if position is None:
                decision.status = "SKIPPED"
                decision.reason = "模拟账户没有该标的仓位，退出信号仅留审计记录"
                db.commit()
                result.skipped.append({"code": candidate.code, "reason": decision.reason})
                continue
            if _has_open_order(db, account.id, candidate.code, "SELL"):
                decision.status = "SKIPPED"
                decision.reason = "已有待成交卖单，禁止重复生成退出委托"
                db.commit()
                result.skipped.append({"code": candidate.code, "reason": decision.reason})
                continue
            quantity = _exit_quantity(position, candidate.ratio, trade_date)
            if quantity <= 0:
                decision.status = "SKIPPED"
                decision.reason = "T+1下当前没有可卖数量，保留信号但不伪造成交"
                db.commit()
                result.skipped.append({"code": candidate.code, "reason": decision.reason})
                continue

        decision.quantity = quantity
        order_reason = decision.reason or candidate.reason
        note = f"shadow:{decision.signal_key};rule={RULE_VERSION};reason={order_reason}"
        order = submit_order(
            db,
            account,
            SimulationOrderCreate(
                strategy_source=candidate.strategy_source,  # type: ignore[arg-type]
                code=candidate.code,
                name=candidate.name,
                side=candidate.side,  # type: ignore[arg-type]
                order_type="MARKET",
                quantity=quantity,
                client_note=note[:1000],
            ),
            now=evaluated_at,
            quote_loader=lambda _code, cached=quote: cached,
        )
        decision = db.get(SimulationShadowDecision, decision.id)
        if decision is not None:
            decision.order_id = order.id
            decision.status = "ORDER_CREATED" if order.status in {"OPEN", "PENDING"} else "ORDER_REJECTED"
            decision.reason = order_reason if order.status in {"OPEN", "PENDING"} else order.reject_reason
            db.add(decision)
            db.commit()
        result.order_ids.append(order.id)
    return result


def mark_shadow_equity_after_close(
    db: Session,
    *,
    now: datetime | None = None,
    quote_loader: QuoteLoader = quote_for_code,
) -> ShadowEquityResult:
    """Idempotently mark active system shadow accounts after the A-share close.

    Quotes must carry an event time from the same business date.  Missing data
    skips the account rather than silently valuing it with a historical quote.
    ``mark_to_market`` already upserts one row per account/trade date.  Manual
    simulation accounts are deliberately excluded: their explicit API/user
    workflow owns valuation and must never block the collector's daily shadow
    close from completing.
    """
    evaluated_at = shanghai_now_naive(now)
    result = ShadowEquityResult(evaluated_at=evaluated_at)
    if evaluated_at.weekday() >= 5 or evaluated_at.time() < time(15, 0):
        result.skipped.append({"account_id": "*", "reason": "尚未到A股收盘净值标记时点"})
        return result
    trade_date = evaluated_at.date()
    accounts = (
        db.query(SimulationAccount)
        .filter(
            SimulationAccount.status == "active",
            SimulationAccount.account_type == "shadow",
            SimulationAccount.automation_key.is_not(None),
            func.length(func.trim(SimulationAccount.automation_key)) > 0,
        )
        .all()
    )
    for account in accounts:
        positions = db.query(SimulationPosition).filter(
            SimulationPosition.account_id == account.id,
            SimulationPosition.quantity > 0,
        ).all()
        cache: dict[str, dict[str, Any]] = {}
        invalid: list[str] = []
        for position in positions:
            quote = quote_loader(position.code) or {}
            cache[position.code] = quote
            quote_at = _quote_time(quote)
            local_quote_at = _local(quote_at)
            close_window_ok = bool(
                local_quote_at is not None
                and time(14, 55) <= local_quote_at.time() <= time(15, 5)
            )
            if (
                local_quote_at is None
                or local_quote_at > evaluated_at
                or local_quote_at.date() != trade_date
                or not close_window_ok
                or _safe_float(quote.get("price")) <= 0
            ):
                invalid.append(position.code)
        if invalid:
            reason = f"以下持仓缺少当日收盘行情：{','.join(invalid)}；本账户不回填历史净值"
            logger.warning("shadow equity skipped account=%s reason=%s", account.id, reason)
            result.skipped.append({"account_id": str(account.id), "reason": reason})
            continue
        row: SimulationDailyEquity = mark_to_market(
            db,
            account,
            now=evaluated_at,
            quote_loader=lambda code, values=cache: values.get(code, {}),
        )
        result.equity_ids.append(row.id)
    return result
