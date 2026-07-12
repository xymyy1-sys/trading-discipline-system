from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.helpers.execution import _position_quantities
from app.core.database import get_db
from app.models.trading import AuditLog, Holding
from app.services.audit import verify_audit_chain
from app.services.intraday_collector import collector_status
from app.services.replay_engine import ReplayEngine

router = APIRouter()


@router.get("/audit-log")
def audit_log(limit: int = 100, db: Session = Depends(get_db)):
    safe_limit = max(1, min(limit, 500))
    chain_valid, total = verify_audit_chain(db)
    entries = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(safe_limit).all()
    return {
        "chain_valid": chain_valid,
        "total": total,
        "entries": [
            {
                "id": row.id, "created_at": row.created_at, "actor": row.actor,
                "method": row.method, "path": row.path, "status_code": row.status_code,
                "request_id": row.request_id, "previous_hash": row.previous_hash,
                "entry_hash": row.entry_hash,
            }
            for row in entries
        ],
    }


@router.get("/acceptance/report")
def acceptance_report(code: str | None = None, trade_date: str | None = None, download: bool = False, db: Session = Depends(get_db)):
    now = datetime.now()
    validations = []
    for holding in db.query(Holding).order_by(Holding.code).all():
        sellable, today_buy, yesterday = _position_quantities(db, holding, now.date().isoformat())
        validations.append({
            "code": holding.code, "name": holding.name, "current_quantity": holding.quantity,
            "today_buy_quantity": today_buy, "yesterday_quantity": yesterday,
            "sellable_quantity": sellable, "passed": sellable <= holding.quantity and sellable + today_buy <= holding.quantity,
        })
    try:
        migration_version = db.execute(text("select version_num from alembic_version")).scalar_one_or_none()
    except Exception:
        migration_version = None
    replay = ReplayEngine(db).replay(code, trade_date) if code and trade_date else None
    status = collector_status()
    payload = {
        "generated_at": now.isoformat(),
        "security": {"authentication_required": True, "backend_public_port_disabled": True, "https_must_be_verified_at_deployment": True},
        "sse": {"endpoint": "/api/intraday-events/stream", "authenticated": True, "recovery_ux": True},
        "collector": {"enabled": status["enabled"], "running": status["running"], "interval_seconds": status["interval_seconds"]},
        "migration_version": migration_version,
        "audit_log": dict(zip(("chain_valid", "entry_count"), verify_audit_chain(db))),
        "t_plus_one_validations": validations,
        "t_plus_one_passed": all(item["passed"] for item in validations),
        "replay": replay.model_dump(mode="json") if replay else None,
    }
    headers = {"Content-Disposition": f'attachment; filename="acceptance-{now:%Y%m%d-%H%M%S}.json"'} if download else None
    return JSONResponse(payload, headers=headers)
