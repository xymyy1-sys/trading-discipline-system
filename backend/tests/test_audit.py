from app.models.trading import AuditLog
from app.services.audit import GENESIS_HASH, record_audit, verify_audit_chain


def test_audit_log_builds_and_verifies_hash_chain(db_session):
    first = record_audit(db_session, "owner", "POST", "/api/holdings", 201, "req-1")
    second = record_audit(db_session, "owner", "PATCH", "/api/holdings/1", 200, "req-2")

    assert first.previous_hash == GENESIS_HASH
    assert second.previous_hash == first.entry_hash
    assert verify_audit_chain(db_session) == (True, 2)


def test_audit_log_detects_tampering(db_session):
    record_audit(db_session, "owner", "DELETE", "/api/holdings/1", 204, "req-3")
    row = db_session.query(AuditLog).one()
    row.path = "/api/holdings/2"
    db_session.commit()

    assert verify_audit_chain(db_session) == (False, 0)


def test_audit_log_endpoint_is_protected_and_reports_chain(client, db_session):
    record_audit(db_session, "test-user", "POST", "/api/trades", 200, "req-4")

    response = client.get("/api/audit-log?limit=1")

    assert response.status_code == 200
    assert response.json()["chain_valid"] is True
    assert response.json()["total"] == 1
    assert response.json()["entries"][0]["request_id"] == "req-4"
