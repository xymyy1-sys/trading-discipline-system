import hashlib
from threading import Lock
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.trading import AuditLog

GENESIS_HASH = "0" * 64
_write_lock = Lock()

def _hash(previous: str, created_at: datetime, actor: str, method: str, path: str, status_code: int, request_id: str) -> str:
    payload = "|".join((previous, created_at.isoformat(), actor, method, path, str(status_code), request_id))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def record_audit(db: Session, actor: str, method: str, path: str, status_code: int, request_id: str) -> AuditLog:
    with _write_lock:
        previous_row = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        previous = previous_row.entry_hash if previous_row else GENESIS_HASH
        created_at = datetime.now()
        safe_actor = actor or "unknown"
        safe_path = path[:255]
        row = AuditLog(
            created_at=created_at, actor=safe_actor, method=method, path=safe_path,
            status_code=status_code, request_id=request_id, previous_hash=previous,
            entry_hash=_hash(previous, created_at, safe_actor, method, safe_path, status_code, request_id),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

def verify_audit_chain(db: Session) -> tuple[bool, int]:
    previous = GENESIS_HASH
    count = 0
    for row in db.query(AuditLog).order_by(AuditLog.id.asc()).all():
        expected = _hash(previous, row.created_at, row.actor, row.method, row.path, row.status_code, row.request_id)
        if row.previous_hash != previous or row.entry_hash != expected:
            return False, count
        previous = row.entry_hash
        count += 1
    return True, count
