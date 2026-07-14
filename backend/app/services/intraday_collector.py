from __future__ import annotations

import asyncio
import json
import sys
import threading
import time as clock
from datetime import datetime, time

from app.api.helpers.decision import current_expectation_stage
from app.core.database import SessionLocal
from app.models.trading import (
    Holding,
    IntradayCollectionRun,
    IntradayEvidenceEvent,
    MarketRegimeSnapshot,
    NextDayPlan,
    WatchlistEntry,
)
from app.schemas.trading import MarketRegimeOut
from app.services.intraday_evidence_engine import collect_holding_evidence, collect_tracked_stock_evidence
from app.services.market_regime import get_market_regime

COLLECTOR_INTERVAL_SECONDS = 60
MARKET_REGIME_INTERVAL_SECONDS = 300
MARKET_REGIME_MIN_PERSIST_SECONDS = 240
COLLECTOR_ENABLED = True
_collector_task: asyncio.Task | None = None
_market_regime_task: asyncio.Task | None = None
_collector_running = False
_market_regime_running = False
_market_regime_guard = threading.Lock()
_market_regime_last_success_at: datetime | None = None
_market_regime_last_error = ""
_close_expectation_date: str | None = None
_notified_recommendations: set[int] = set()
_failure_counts: dict[str, int] = {}
_circuit_until: dict[str, float] = {}
_queue_depth = 0


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
    now = now or datetime.now()
    current = now.time()
    return now.weekday() < 5 and time(9, 15) <= current <= time(15, 0)


def _is_market_regime_watch_time(now: datetime | None = None) -> bool:
    """Collect from the end of auction through a final post-close snapshot."""
    now = now or datetime.now()
    current = now.time()
    return now.weekday() < 5 and time(9, 25) <= current <= time(15, 5)


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
    collected_at = now or datetime.now()
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
        _market_regime_last_success_at = datetime.now()
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


def run_intraday_collection_once(trigger: str = "manual") -> IntradayCollectionRun:
    global _queue_depth
    db = SessionLocal()
    started = datetime.now()
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
        for row in db.query(WatchlistEntry).filter(WatchlistEntry.status == "active").order_by(
            WatchlistEntry.snapshot_rank.asc(), WatchlistEntry.updated_at.desc()
        ).limit(10).all():
            if row.code not in holding_codes:
                tracked[row.code] = (row.name, "自动观察池")
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
        for holding in holdings:
            result = _run_with_resilience(
                f"持仓:{holding.code}",
                lambda holding=holding: collect_holding_evidence(db, holding, stage=stage, now=started),
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
        run.finished_at = datetime.now()
        db.add(run)
        db.commit()
        db.refresh(run)
        db.close()
    return run


async def _collector_loop() -> None:
    global _collector_running, _close_expectation_date
    while True:
        if COLLECTOR_ENABLED and _is_market_watch_time():
            _collector_running = True
            await asyncio.to_thread(run_intraday_collection_once, "scheduler")
            _collector_running = False
        now = datetime.now()
        if COLLECTOR_ENABLED and now.weekday() < 5 and now.time() > time(15, 0) and _close_expectation_date != now.date().isoformat():
            from app.services.next_day_expectations import generate_next_day_expectations
            db = SessionLocal()
            try:
                await asyncio.to_thread(generate_next_day_expectations, db)
                _close_expectation_date = now.date().isoformat()
            except Exception:
                db.rollback()
            finally:
                db.close()
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
        "market_regime_running": _market_regime_running,
        "market_regime_interval_seconds": MARKET_REGIME_INTERVAL_SECONDS,
        "market_regime_last_success_at": _market_regime_last_success_at,
        "market_regime_last_error": _market_regime_last_error,
        "queue_depth": _queue_depth,
        "open_circuits": [key for key, until in _circuit_until.items() if until > clock.time()],
        "failure_counts": dict(_failure_counts),
        "last_run": latest_collection_run(),
    }


def collection_notes(row: IntradayCollectionRun | None) -> list[str]:
    return _json_list(row.notes_json if row else "[]")
