from __future__ import annotations

import asyncio
import json
import sys
import threading
import time as clock
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from app.api.helpers.decision import current_expectation_stage
from app.api.helpers.quotes import _normalize_code
from app.api.helpers.reflexivity import build_consensus_high_open_fade
from app.api.helpers.seesaw import _market_seesaw_monitor
from app.core.database import SessionLocal
from app.core.trading_clock import shanghai_now_naive
from app.models.trading import (
    Holding,
    IntradayCollectionRun,
    IntradayEvidenceEvent,
    MarketRegimeSnapshot,
    NextDayPlan,
    SimulationAccount,
    SimulationOrder,
    WatchlistEntry,
)
from app.schemas.trading import MarketRegimeOut
from app.services.intraday_evidence_engine import collect_holding_evidence, collect_tracked_stock_evidence
from app.services.market_data import MarketDataProvider
from app.services.market_regime import get_market_regime
from app.services.opportunity_radar import OpportunityRadarService
from app.services.sector_expansion import SectorExpansionRadarService
from app.services.simulation import process_open_orders
from app.services.simulation_shadow import mark_shadow_equity_after_close, run_shadow_experiments
from app.services.unified_market_events import persist_unified_market_events

COLLECTOR_INTERVAL_SECONDS = 60
MARKET_REGIME_INTERVAL_SECONDS = 300
MARKET_REGIME_MIN_PERSIST_SECONDS = 240
OPPORTUNITY_MARKET_SNAPSHOT_MAX_AGE = timedelta(minutes=15)
COLLECTOR_ENABLED = True
_collector_task: asyncio.Task | None = None
_market_regime_task: asyncio.Task | None = None
_collector_running = False
_collector_last_success_at: datetime | None = None
_collector_last_error = ""
_market_regime_running = False
_market_regime_guard = threading.Lock()
_opportunity_radar_guard = threading.Lock()
_market_regime_last_success_at: datetime | None = None
_market_regime_last_error = ""
_opportunity_radar_running = False
_opportunity_radar_last_success_at: datetime | None = None
_opportunity_radar_last_error = ""
_simulation_match_running = False
_simulation_match_last_success_at: datetime | None = None
_simulation_match_last_error = ""
_simulation_shadow_running = False
_simulation_shadow_last_success_at: datetime | None = None
_simulation_shadow_last_error = ""
_simulation_shadow_equity_last_success_at: datetime | None = None
_simulation_shadow_equity_last_error = ""
_close_expectation_date: str | None = None
_close_shadow_equity_date: str | None = None
_notified_recommendations: set[int] = set()
_failure_counts: dict[str, int] = {}
_circuit_until: dict[str, float] = {}
_queue_depth = 0

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
opportunity_market_provider = MarketDataProvider()
opportunity_radar_service = OpportunityRadarService()
opportunity_sector_expansion_service = SectorExpansionRadarService()

SHADOW_ACCOUNT_AUTOMATION_KEY = "system-shadow-forward-v1"
SHADOW_ACCOUNT_NAME = "系统影子验证账户"
SHADOW_ACCOUNT_INITIAL_CASH = 1_000_000


def _shanghai_now_naive() -> datetime:
    """Return China Standard Time independently from the host timezone."""
    return shanghai_now_naive()


def _run_with_resilience(key: str, callback, notes: list[str], *, on_error=None):
    """Run one collection job with bounded exponential backoff and a circuit breaker."""
    now = clock.time()
    if _circuit_until.get(key, 0) > now:
        remaining = int(_circuit_until[key] - now)
        notes.append(f"{key} 熔断中，{remaining}秒后重试；不将单一数据源故障误报为全系统故障。")
        return None
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            result = callback()
            _failure_counts.pop(key, None)
            _circuit_until.pop(key, None)
            return result
        except Exception as exc:
            last_error = exc
            # A failed SQLAlchemy flush/commit invalidates the transaction.
            # Roll back before every retry, not only after all retries fail.
            if on_error is not None:
                try:
                    on_error()
                except Exception as rollback_exc:
                    notes.append(f"{key} 事务回滚失败：{rollback_exc.__class__.__name__}。")
            if attempt < 2:
                clock.sleep(0.25 * (2 ** attempt))
    failures = _failure_counts.get(key, 0) + 1
    _failure_counts[key] = failures
    if failures >= 3:
        _circuit_until[key] = clock.time() + 300
        notes.append(f"{key} 连续失败{failures}轮，已熔断5分钟。")
    if last_error:
        notes.append(f"{key} 采集失败：{last_error.__class__.__name__}。")
    return None


def _json_dumps(value: list[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except Exception:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _is_market_watch_time(now: datetime | None = None) -> bool:
    now = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    current = now.time()
    return now.weekday() < 5 and (
        time(9, 15) <= current <= time(11, 30)
        or time(13, 0) <= current <= time(15, 0)
    )


def _is_market_regime_watch_time(now: datetime | None = None) -> bool:
    """Collect from the end of auction through a final post-close snapshot."""
    now = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    current = now.time()
    return now.weekday() < 5 and time(9, 25) <= current <= time(15, 5)


def _is_simulation_match_time(now: datetime | None = None) -> bool:
    """Simulation fills are only evaluated during the continuous auction."""
    now = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    if now.weekday() >= 5:
        return False
    current = now.time()
    return time(9, 30) <= current <= time(11, 30) or time(13, 0) <= current <= time(15, 0)


def run_simulation_matching_once(*, now: datetime | None = None) -> dict[str, object]:
    """Match every active simulation account without touching live collection.

    Each account owns a fresh transaction/session.  One corrupt order or one
    provider failure is rolled back and recorded without preventing other
    accounts, holdings evidence, or market-regime collection from running.
    """
    global _simulation_match_running, _simulation_match_last_success_at, _simulation_match_last_error
    evaluated_at = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    result: dict[str, object] = {"account_count": 0, "processed_count": 0, "errors": []}
    if not _is_simulation_match_time(evaluated_at):
        result["skipped"] = "outside_continuous_auction"
        return result
    _simulation_match_running = True
    discovery_db = SessionLocal()
    try:
        account_ids = [
            row[0]
            for row in (
                discovery_db.query(SimulationAccount.id)
                .join(SimulationOrder, SimulationOrder.account_id == SimulationAccount.id)
                .filter(
                    SimulationAccount.status == "active",
                    SimulationOrder.status.in_(("OPEN", "PENDING")),
                )
                .distinct()
                .all()
            )
        ]
    except Exception as exc:
        discovery_db.rollback()
        _simulation_match_last_error = f"{exc.__class__.__name__}: {exc}"
        result["errors"] = [_simulation_match_last_error]
        _simulation_match_running = False
        return result
    finally:
        discovery_db.close()

    result["account_count"] = len(account_ids)
    for account_id in account_ids:
        db = SessionLocal()
        try:
            account = db.get(SimulationAccount, account_id)
            if account is None or account.status != "active":
                continue
            rows = process_open_orders(db, account, now=evaluated_at)
            result["processed_count"] = int(result["processed_count"]) + len(rows)
        except Exception as exc:
            db.rollback()
            errors = list(result["errors"])
            errors.append(f"account:{account_id}:{exc.__class__.__name__}")
            result["errors"] = errors
        finally:
            db.close()
    errors = list(result["errors"])
    _simulation_match_last_error = "; ".join(errors)
    if not errors:
        _simulation_match_last_success_at = _shanghai_now_naive()
    _simulation_match_running = False
    return result


def _get_or_create_shadow_account(db) -> SimulationAccount:
    row = db.query(SimulationAccount).filter(
        SimulationAccount.automation_key == SHADOW_ACCOUNT_AUTOMATION_KEY,
    ).first()
    if row is not None:
        return row
    row = SimulationAccount(
        name=SHADOW_ACCOUNT_NAME,
        initial_cash=SHADOW_ACCOUNT_INITIAL_CASH,
        cash=SHADOW_ACCOUNT_INITIAL_CASH,
        account_type="shadow",
        automation_key=SHADOW_ACCOUNT_AUTOMATION_KEY,
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
            SimulationAccount.automation_key == SHADOW_ACCOUNT_AUTOMATION_KEY,
        ).first()
        if existing is None:
            raise
        return existing


def run_simulation_shadow_once(*, now: datetime | None = None) -> dict[str, object]:
    """Create forward-only paper orders from this minute's confirmed signals."""

    global _simulation_shadow_running, _simulation_shadow_last_success_at, _simulation_shadow_last_error
    evaluated_at = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    result: dict[str, object] = {
        "account_id": None,
        "created_order_ids": [],
        "skipped_count": 0,
        "duplicate_count": 0,
        "errors": [],
    }
    if not _is_simulation_match_time(evaluated_at):
        result["skipped"] = "outside_continuous_auction"
        return result
    _simulation_shadow_running = True
    db = SessionLocal()
    try:
        account = _get_or_create_shadow_account(db)
        result["account_id"] = account.id
        shadow = run_shadow_experiments(db, account, now=evaluated_at)
        result["created_order_ids"] = list(shadow.order_ids)
        result["skipped_count"] = len(shadow.skipped)
        result["duplicate_count"] = len(shadow.duplicate_signal_keys)
        _simulation_shadow_last_error = ""
        _simulation_shadow_last_success_at = _shanghai_now_naive()
    except Exception as exc:
        db.rollback()
        _simulation_shadow_last_error = f"{exc.__class__.__name__}: {exc}"
        result["errors"] = [_simulation_shadow_last_error]
    finally:
        db.close()
        _simulation_shadow_running = False
    return result


def run_simulation_shadow_equity_once(*, now: datetime | None = None) -> dict[str, object]:
    """Persist same-day simulated equity after close; never backfill stale quotes."""

    global _simulation_shadow_equity_last_success_at, _simulation_shadow_equity_last_error
    evaluated_at = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    result: dict[str, object] = {"equity_ids": [], "skipped_count": 0, "errors": []}
    db = SessionLocal()
    try:
        shadow = mark_shadow_equity_after_close(db, now=evaluated_at)
        result["equity_ids"] = list(shadow.equity_ids)
        result["skipped_count"] = len(shadow.skipped)
        _simulation_shadow_equity_last_error = ""
        # A partial snapshot is retried on a later minute.  Do not expose it
        # as the latest successful close-equity run.
        if not shadow.skipped:
            _simulation_shadow_equity_last_success_at = _shanghai_now_naive()
    except Exception as exc:
        db.rollback()
        _simulation_shadow_equity_last_error = f"{exc.__class__.__name__}: {exc}"
        result["errors"] = [_simulation_shadow_equity_last_error]
    finally:
        db.close()
    return result


def _record_close_shadow_equity_completion(result: dict[str, object], trade_date: str) -> bool:
    """Mark a close-equity day complete only after every account was valued."""

    global _close_shadow_equity_date
    errors = list(result.get("errors") or [])
    skipped_count = int(result.get("skipped_count") or 0)
    if errors or skipped_count:
        return False
    _close_shadow_equity_date = trade_date
    return True


def _recent_market_regime_exists(db, now: datetime) -> bool:
    latest = (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date == now.date().isoformat())
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )
    if latest is None or latest.captured_at is None:
        return False
    age_seconds = (now - latest.captured_at).total_seconds()
    return -60 <= age_seconds < MARKET_REGIME_MIN_PERSIST_SECONDS


def run_market_regime_collection_once(
    trigger: str = "scheduler",
    *,
    now: datetime | None = None,
    force: bool = False,
) -> MarketRegimeOut | None:
    """Persist one full-market snapshot without blocking or failing holding collection.

    A process-local guard prevents overlapping requests, while the database freshness
    check prevents duplicate snapshots after restarts or when another worker/API call
    has just completed a collection.
    """
    del trigger  # Reserved for future collection-run audit metadata.
    global _market_regime_running, _market_regime_last_error, _market_regime_last_success_at

    if not _market_regime_guard.acquire(blocking=False):
        return None
    _market_regime_running = True
    db = None
    collected_at = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    key = "市场环境"
    try:
        db = SessionLocal()
        if _circuit_until.get(key, 0) > clock.time():
            return None
        if not force and _recent_market_regime_exists(db, collected_at):
            return None
        result = get_market_regime(db, force_refresh=force)
        _failure_counts.pop(key, None)
        _circuit_until.pop(key, None)
        _market_regime_last_error = ""
        _market_regime_last_success_at = _shanghai_now_naive()
        return result
    except Exception as exc:
        if db is not None:
            db.rollback()
        failures = _failure_counts.get(key, 0) + 1
        _failure_counts[key] = failures
        _market_regime_last_error = f"{exc.__class__.__name__}: {exc}"
        if failures >= 3:
            _circuit_until[key] = clock.time() + MARKET_REGIME_INTERVAL_SECONDS
        return None
    finally:
        if db is not None:
            db.close()
        _market_regime_running = False
        _market_regime_guard.release()


def _fresh_opportunity_market_snapshot(
    db,
    *,
    trade_date: str,
    now: datetime,
) -> tuple[MarketRegimeSnapshot | None, str | None]:
    """Return only current-session market context for the opportunity radar."""

    current = now
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    else:
        current = current.astimezone(SHANGHAI_TZ)
    latest = (
        db.query(MarketRegimeSnapshot)
        .filter(MarketRegimeSnapshot.trade_date == trade_date)
        .order_by(MarketRegimeSnapshot.captured_at.desc(), MarketRegimeSnapshot.id.desc())
        .first()
    )
    if latest is None or latest.captured_at is None:
        return None, f"{trade_date}无同交易日市场环境快照，机会雷达不使用历史市场涨跌替代。"
    captured = latest.captured_at
    if captured.tzinfo is None:
        captured = captured.replace(tzinfo=SHANGHAI_TZ)
    else:
        captured = captured.astimezone(SHANGHAI_TZ)
    age = current - captured
    if age < timedelta(minutes=-1) or age > OPPORTUNITY_MARKET_SNAPSHOT_MAX_AGE:
        age_minutes = max(0, int(age.total_seconds() // 60))
        return None, f"{trade_date}市场环境快照已过期（{age_minutes}分钟），未用于板块相对强度计算。"
    return latest, None


def _build_current_opportunity_radar(
    db,
    *,
    holdings: dict[str, str],
    evaluated_at: datetime,
    force_refresh: bool = False,
) -> dict[str, object]:
    """Build the same real-evidence radar used by the read endpoint.

    The market-data response cache is process-wide, so this minute job reuses
    the same five-minute cache as API reads.  Missing providers stay explicit;
    no historical or synthetic rows are substituted.
    """

    trade_date = evaluated_at.date().isoformat()
    degraded_notes: list[str] = []
    try:
        information = opportunity_market_provider.information_differential(
            date=trade_date,
            force_refresh=force_refresh,
            related_stocks=holdings,
        )
    except Exception as exc:
        information = {
            "items": [],
            "data_quality": "missing",
            "notes": [f"当日资讯源暂不可用：{exc.__class__.__name__}"],
        }
        degraded_notes.append(f"当日资讯源暂不可用：{exc.__class__.__name__}；未生成替代消息。")

    sector_flows: list[object] = []
    for flow_type in ("行业资金流", "概念资金流"):
        try:
            sector_flows.append(
                opportunity_market_provider.sector_flow(
                    flow_type=flow_type,
                    period="今日",
                    force_refresh=force_refresh,
                )
            )
        except Exception as exc:
            sector_flows.append({
                "inflow": [],
                "outflow": [],
                "data_quality": "missing",
                "source": [],
                "notes": [f"{flow_type}暂不可用：{exc.__class__.__name__}"],
            })
            degraded_notes.append(f"{flow_type}暂不可用：{exc.__class__.__name__}；未生成模拟资金证据。")

    latest_regime, regime_note = _fresh_opportunity_market_snapshot(
        db,
        trade_date=trade_date,
        now=evaluated_at,
    )
    if regime_note:
        degraded_notes.append(regime_note)
    try:
        sector_opening = opportunity_market_provider.sector_opening_breadth(
            trade_date=trade_date,
            force_refresh=force_refresh,
        )
    except Exception as exc:
        sector_opening = {
            "trade_date": trade_date,
            "data_quality": "missing",
            "source": "",
            "sample_count": 0,
            "notes": [f"行业板块真实开盘广度暂不可用：{exc.__class__.__name__}"],
        }
        degraded_notes.append(f"行业板块真实开盘广度暂不可用：{exc.__class__.__name__}。")

    radar = opportunity_radar_service.assess(
        information,
        sector_flows,
        market_change_pct=(latest_regime.index_composite_change_pct if latest_regime else None),
    )
    radar["consensus_high_open_fade"] = build_consensus_high_open_fade(
        db,
        latest_regime,
        sector_opening,
    )
    try:
        ladder = opportunity_market_provider.limit_up_ladder(
            trade_date,
            force_refresh=force_refresh,
        )
        radar["intraday_expansion"] = opportunity_sector_expansion_service.assess(
            ladder,
            sector_flows,
            as_of=evaluated_at.replace(tzinfo=SHANGHAI_TZ),
        )
    except Exception as exc:
        timestamp = evaluated_at.replace(tzinfo=SHANGHAI_TZ).isoformat()
        radar["intraday_expansion"] = {
            "updated_at": timestamp,
            "as_of": timestamp,
            "window_minutes": 0,
            "data_quality": "missing",
            "source": [],
            "items": [],
            "counts": {"增量已确认": 0, "增量待确认": 0},
            "notes": [f"涨停梯队或板块资金暂不可用：{exc.__class__.__name__}；本轮不生成模拟增量方向。"],
        }
        degraded_notes.append(f"盘中板块增量证据暂不可用：{exc.__class__.__name__}。")

    if degraded_notes:
        radar["notes"] = list(dict.fromkeys([*(radar.get("notes") or []), *degraded_notes]))
        if radar.get("data_quality") == "ok":
            radar["data_quality"] = "degraded"
    return radar


def run_opportunity_radar_collection_once(
    trigger: str = "scheduler",
    *,
    now: datetime | None = None,
    force_refresh: bool = False,
    target_trade_date: str | None = None,
) -> dict[str, object]:
    """Collect and persist today's opportunity/news events without a browser.

    This job has its own session and error boundary.  A news, fund-flow, ladder
    or persistence failure is reported in collector status and never rolls
    back or interrupts the holding evidence transaction.
    """

    del trigger  # Reserved for later per-run audit metadata.
    global _opportunity_radar_running, _opportunity_radar_last_success_at, _opportunity_radar_last_error
    evaluated_at = shanghai_now_naive(now) if now is not None else _shanghai_now_naive()
    trade_date = target_trade_date or evaluated_at.date().isoformat()
    result: dict[str, object] = {
        "trade_date": trade_date,
        "persisted_count": 0,
        "data_quality": "missing",
        "errors": [],
    }
    if trade_date != evaluated_at.date().isoformat():
        result["skipped"] = "historical_read_only"
        return result
    if not _is_market_watch_time(evaluated_at):
        result["skipped"] = "outside_market_watch_time"
        return result
    if not _opportunity_radar_guard.acquire(blocking=False):
        result["skipped"] = "already_running"
        return result

    _opportunity_radar_running = True
    db = SessionLocal()
    try:
        holdings = {row.code: row.name for row in db.query(Holding).all()}
        radar = _build_current_opportunity_radar(
            db,
            holdings=holdings,
            evaluated_at=evaluated_at,
            force_refresh=force_refresh,
        )
        emitted = persist_unified_market_events(
            db,
            radar,
            holdings,
            now=evaluated_at,
        )
        result["persisted_count"] = len(emitted)
        result["data_quality"] = str(radar.get("data_quality") or "missing")
        result["notes"] = list(radar.get("notes") or [])
        _opportunity_radar_last_error = ""
        _opportunity_radar_last_success_at = _shanghai_now_naive()
    except Exception as exc:
        db.rollback()
        _opportunity_radar_last_error = f"{exc.__class__.__name__}: {exc}"
        result["errors"] = [_opportunity_radar_last_error]
    finally:
        db.close()
        _opportunity_radar_running = False
        _opportunity_radar_guard.release()
    return result


def run_intraday_collection_once(trigger: str = "manual") -> IntradayCollectionRun:
    global _queue_depth
    db = SessionLocal()
    started = _shanghai_now_naive()
    run = IntradayCollectionRun(started_at=started, trigger=trigger, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    notes: list[str] = []
    snapshot_count = 0
    event_before = 0
    try:
        event_before = db.query(IntradayEvidenceEvent).count()
        holdings = db.query(Holding).order_by(Holding.updated_at.desc()).all()
        holding_codes = {row.code for row in holdings}
        tracked: dict[str, tuple[str, str]] = {}
        latest_auto = max(
            (
                row.snapshot_date for row in db.query(WatchlistEntry).filter(
                    WatchlistEntry.status == "active",
                    WatchlistEntry.source == "auto",
                ).all() if row.snapshot_date
            ),
            default="",
        )
        tracked_entries = db.query(WatchlistEntry).filter(
            WatchlistEntry.status == "active",
        ).order_by(WatchlistEntry.snapshot_rank.asc(), WatchlistEntry.updated_at.desc()).all()
        for row in tracked_entries:
            if row.source != "manual" and row.snapshot_date != latest_auto:
                continue
            if row.code not in holding_codes:
                tracked[row.code] = (row.name, "手动观察池" if row.source == "manual" else "自动观察池")
        for row in db.query(NextDayPlan).filter(
            NextDayPlan.plan_date == started.date().isoformat(),
            NextDayPlan.plan_type == "limit_up_auction",
        ).all():
            if row.code not in holding_codes:
                tracked[row.code] = (row.name, "打板预案")
        stage = current_expectation_stage(started)
        _queue_depth = len(holdings) + len(tracked)
        run.holding_count = len(holdings)
        if not _is_market_watch_time(started):
            notes.append("当前不在交易采样时段（交易日09:15-15:00），不生成盘后证据和操作建议。")
            holdings = []
            tracked = {}
        elif not holdings:
            notes.append("暂无持仓，后台采集跳过。")
        seesaw_by_code: dict[str, object] = {}
        if holdings:
            monitor = _run_with_resilience(
                "板块资金拐点",
                lambda: _market_seesaw_monitor(holdings, force_refresh=True),
                notes,
                on_error=db.rollback,
            )
            if monitor is not None:
                seesaw_by_code = {
                    _normalize_code(item.code): item
                    for item in monitor.holding_alerts
                }
        for holding in holdings:
            result = _run_with_resilience(
                f"持仓:{holding.code}",
                lambda holding=holding: collect_holding_evidence(
                    db,
                    holding,
                    stage=stage,
                    now=started,
                    seesaw=seesaw_by_code.get(_normalize_code(holding.code)),
                ),
                notes,
                on_error=db.rollback,
            )
            _queue_depth = max(0, _queue_depth - 1)
            if result is None:
                db.rollback()
                continue
            _volume, state, _sample = result
            snapshot_count += 1
            notes.append(f"{holding.code} {holding.name} 已采集 {stage}。")
            recommendation = state.recommendation
            if recommendation and recommendation.id and recommendation.id not in _notified_recommendations and recommendation.level in {"WARNING", "CRITICAL"}:
                try:
                    from app.services.dingtalk import send_dingtalk_markdown
                    send_dingtalk_markdown(
                        f"{holding.name} 风险提醒",
                        f"### {holding.name}（{holding.code}）\n\n- 风险级别：{recommendation.level}\n- 操作建议：{recommendation.action}\n- 当前状态：{recommendation.state}\n\n请登录知行交易驾驶舱核对完整证据。",
                    )
                    _notified_recommendations.add(recommendation.id)
                except RuntimeError:
                    pass
                except Exception as notify_exc:
                    notes.append(f"钉钉通知失败：{notify_exc.__class__.__name__}")
        for code, (name, base_hint) in tracked.items():
            result = _run_with_resilience(
                f"跟踪:{code}",
                lambda code=code, name=name, base_hint=base_hint: collect_tracked_stock_evidence(db, code, name, base_hint, stage=stage, now=started),
                notes,
                on_error=db.rollback,
            )
            _queue_depth = max(0, _queue_depth - 1)
            if result is None:
                db.rollback()
                continue
            snapshot_count += 1
            notes.append(f"{code} {name}（{base_hint}）已采集 {stage}。")
        run.status = "success"
    except Exception as exc:
        db.rollback()
        run = db.get(IntradayCollectionRun, run.id) or run
        run.status = "failed"
        run.error_message = str(exc)
        notes.append(f"采集失败：{exc}")
    finally:
        _queue_depth = 0
        event_after = db.query(IntradayEvidenceEvent).count()
        run.snapshot_count = snapshot_count
        run.event_count = max(0, event_after - event_before)
        run.notes_json = _json_dumps(notes)
        run.finished_at = _shanghai_now_naive()
        db.add(run)
        db.commit()
        db.refresh(run)
        db.close()
    return run


async def _collector_iteration() -> None:
    """Run one collector minute while isolating every independent workload."""

    global _collector_running, _close_expectation_date, _close_shadow_equity_date
    global _collector_last_success_at, _collector_last_error
    global _simulation_match_last_error, _simulation_shadow_last_error, _opportunity_radar_last_error
    if COLLECTOR_ENABLED and _is_market_watch_time():
        _collector_running = True
        try:
            await asyncio.to_thread(run_intraday_collection_once, "scheduler")
            _collector_last_success_at = _shanghai_now_naive()
            _collector_last_error = ""
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Initialization/finalization can fail before the collection-run
            # service reaches its own transaction guard.  Keep the scheduler
            # alive and expose the failure instead of leaving `running=true`.
            _collector_last_error = f"{exc.__class__.__name__}: {exc}"
        finally:
            _collector_running = False

        # Opportunity/news persistence is server-driven rather than tied to a
        # visible browser page.  It still runs when holding collection failed,
        # because it owns a different session and error boundary.
        try:
            await asyncio.to_thread(run_opportunity_radar_collection_once, "scheduler")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _opportunity_radar_last_error = f"{exc.__class__.__name__}: {exc}"
    if COLLECTOR_ENABLED and _is_simulation_match_time():
        # Deliberately separate from holding collection: simulation/provider
        # failures must never interrupt the real evidence sampling path.
        try:
            await asyncio.to_thread(run_simulation_matching_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _simulation_match_last_error = f"{exc.__class__.__name__}: {exc}"
        # New decisions are generated only after the previous minute's
        # orders have been matched, so no signal can fill on its own bar.
        try:
            await asyncio.to_thread(run_simulation_shadow_once)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _simulation_shadow_last_error = f"{exc.__class__.__name__}: {exc}"
    now = _shanghai_now_naive()
    if COLLECTOR_ENABLED and now.weekday() < 5 and now.time() > time(15, 0) and _close_expectation_date != now.date().isoformat():
        from app.services.next_day_expectations import generate_next_day_expectations
        db = SessionLocal()
        try:
            await asyncio.to_thread(generate_next_day_expectations, db)
            _close_expectation_date = now.date().isoformat()
        except asyncio.CancelledError:
            raise
        except Exception:
            db.rollback()
        finally:
            db.close()
    if (
        COLLECTOR_ENABLED
        and now.weekday() < 5
        and now.time() >= time(15, 5)
        and _close_shadow_equity_date != now.date().isoformat()
    ):
        result = await asyncio.to_thread(run_simulation_shadow_equity_once, now=now)
        _record_close_shadow_equity_completion(result, now.date().isoformat())


async def _collector_loop() -> None:
    global _collector_last_error
    while True:
        try:
            await _collector_iteration()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Last-resort boundary: a future workload added to an iteration
            # must not be able to permanently terminate the background task.
            _collector_last_error = f"{exc.__class__.__name__}: {exc}"
        await asyncio.sleep(COLLECTOR_INTERVAL_SECONDS)


async def _market_regime_loop() -> None:
    """Independent low-frequency loop so full-market I/O cannot stall stock samples."""
    while True:
        if COLLECTOR_ENABLED and _is_market_regime_watch_time():
            await asyncio.to_thread(run_market_regime_collection_once, "scheduler")
        await asyncio.sleep(MARKET_REGIME_INTERVAL_SECONDS)


def start_intraday_collector() -> None:
    global _collector_task, _market_regime_task
    if "pytest" in sys.modules:
        return
    if _collector_task is None or _collector_task.done():
        _collector_task = asyncio.create_task(_collector_loop())
    if _market_regime_task is None or _market_regime_task.done():
        _market_regime_task = asyncio.create_task(_market_regime_loop())


async def stop_intraday_collector() -> None:
    global _collector_task, _collector_running, _market_regime_task, _market_regime_running
    tasks = [task for task in (_collector_task, _market_regime_task) if task is not None]
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
    _collector_task = None
    _market_regime_task = None
    _collector_running = False
    _market_regime_running = False


def latest_collection_run() -> IntradayCollectionRun | None:
    db = SessionLocal()
    try:
        return db.query(IntradayCollectionRun).order_by(IntradayCollectionRun.started_at.desc(), IntradayCollectionRun.id.desc()).first()
    finally:
        db.close()


def collector_status() -> dict[str, object]:
    return {
        "enabled": COLLECTOR_ENABLED,
        "interval_seconds": COLLECTOR_INTERVAL_SECONDS,
        "running": _collector_running,
        "last_success_at": _collector_last_success_at,
        "last_error": _collector_last_error,
        "market_regime_running": _market_regime_running,
        "market_regime_interval_seconds": MARKET_REGIME_INTERVAL_SECONDS,
        "market_regime_last_success_at": _market_regime_last_success_at,
        "market_regime_last_error": _market_regime_last_error,
        "opportunity_radar_running": _opportunity_radar_running,
        "opportunity_radar_last_success_at": _opportunity_radar_last_success_at,
        "opportunity_radar_last_error": _opportunity_radar_last_error,
        "simulation_match_running": _simulation_match_running,
        "simulation_match_last_success_at": _simulation_match_last_success_at,
        "simulation_match_last_error": _simulation_match_last_error,
        "simulation_shadow_running": _simulation_shadow_running,
        "simulation_shadow_last_success_at": _simulation_shadow_last_success_at,
        "simulation_shadow_last_error": _simulation_shadow_last_error,
        "simulation_shadow_equity_last_success_at": _simulation_shadow_equity_last_success_at,
        "simulation_shadow_equity_last_error": _simulation_shadow_equity_last_error,
        "queue_depth": _queue_depth,
        "open_circuits": [key for key, until in _circuit_until.items() if until > clock.time()],
        "failure_counts": dict(_failure_counts),
        "last_run": latest_collection_run(),
    }


def collection_notes(row: IntradayCollectionRun | None) -> list[str]:
    return _json_list(row.notes_json if row else "[]")
