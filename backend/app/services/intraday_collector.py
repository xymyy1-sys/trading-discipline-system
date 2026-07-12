from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, time

from app.api.helpers.decision import current_expectation_stage
from app.core.database import SessionLocal
from app.models.trading import Holding, IntradayCollectionRun, IntradayEvidenceEvent
from app.services.intraday_evidence_engine import collect_holding_evidence

COLLECTOR_INTERVAL_SECONDS = 60
COLLECTOR_ENABLED = True
_collector_task: asyncio.Task | None = None
_collector_running = False


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
    return time(9, 15) <= current <= time(15, 5)


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
        stage = current_expectation_stage(started)
        run.holding_count = len(holdings)
        if not holdings:
            notes.append("暂无持仓，后台采集跳过。")
        for holding in holdings:
            collect_holding_evidence(db, holding, stage=stage, now=started)
            snapshot_count += 1
            notes.append(f"{holding.code} {holding.name} 已采集 {stage}。")
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
    global _collector_running
    while True:
        if COLLECTOR_ENABLED and _is_market_watch_time():
            _collector_running = True
            await asyncio.to_thread(run_intraday_collection_once, "scheduler")
            _collector_running = False
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
