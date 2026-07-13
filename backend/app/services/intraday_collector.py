from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, time

from app.api.helpers.decision import current_expectation_stage
from app.core.database import SessionLocal
from app.models.trading import Holding, IntradayCollectionRun, IntradayEvidenceEvent, NextDayPlan, WatchlistEntry
from app.services.intraday_evidence_engine import collect_holding_evidence, collect_tracked_stock_evidence

COLLECTOR_INTERVAL_SECONDS = 60
COLLECTOR_ENABLED = True
_collector_task: asyncio.Task | None = None
_collector_running = False
_close_expectation_date: str | None = None
_notified_recommendations: set[int] = set()


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


def run_intraday_collection_once(trigger: str = "manual") -> IntradayCollectionRun:
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
        run.holding_count = len(holdings)
        if not _is_market_watch_time(started):
            notes.append("当前不在交易采样时段（交易日09:15-15:00），不生成盘后证据和操作建议。")
            holdings = []
            tracked = {}
        elif not holdings:
            notes.append("暂无持仓，后台采集跳过。")
        for holding in holdings:
            _volume, state, _sample = collect_holding_evidence(db, holding, stage=stage, now=started)
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
            collect_tracked_stock_evidence(db, code, name, base_hint, stage=stage, now=started)
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
            finally:
                db.close()
        await asyncio.sleep(COLLECTOR_INTERVAL_SECONDS)


def start_intraday_collector() -> None:
    global _collector_task
    if "pytest" in sys.modules:
        return
    if _collector_task is None or _collector_task.done():
        _collector_task = asyncio.create_task(_collector_loop())


async def stop_intraday_collector() -> None:
    global _collector_task, _collector_running
    task = _collector_task
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    _collector_task = None
    _collector_running = False


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
        "last_run": latest_collection_run(),
    }


def collection_notes(row: IntradayCollectionRun | None) -> list[str]:
    return _json_list(row.notes_json if row else "[]")
