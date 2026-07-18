#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Missing .env; copy .env.example and configure production secrets first." >&2
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  echo "Docker Compose is not installed." >&2
  exit 1
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="backups/${timestamp}"
mkdir -p "${backup_dir}"
git rev-parse HEAD > "${backup_dir}/code-before.txt"

"${compose[@]}" exec -T backend python - > "${backup_dir}/counts-before.json" <<'PY'
import json
from pathlib import Path
import sqlite3

source = sqlite3.connect("/app/data/trading_discipline.db")
integrity = source.execute("PRAGMA quick_check").fetchone()[0]
if integrity != "ok":
    raise SystemExit("live database failed PRAGMA quick_check before deployment")
backup_path = Path("/app/data/predeploy-backup.db")
backup_path.unlink(missing_ok=True)
backup = sqlite3.connect("/app/data/predeploy-backup.db")
source.backup(backup)
if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
    raise SystemExit("backup database failed PRAGMA quick_check")
backup.close()
counts = {}
missing_tables = []
for table in (
    "holdings",
    "trade_logs",
    "next_day_plans",
    "action_recommendations",
    "action_recommendation_revisions",
    "recommendation_feedback",
    "recommendation_outcomes",
):
    exists = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        missing_tables.append(table)
        continue
    counts[table] = source.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
print(json.dumps({
    "counts": counts,
    "missing_tables": missing_tables,
    "integrity": integrity,
}, ensure_ascii=False))
source.close()
PY

backend_container_id="$("${compose[@]}" ps -q backend)"
if [[ -z "${backend_container_id}" ]]; then
  echo "Cannot resolve the backend container id; backup remains inside the backend volume." >&2
  exit 1
fi
docker cp "${backend_container_id}:/app/data/predeploy-backup.db" "${backup_dir}/trading_discipline.db"

if command -v python3 >/dev/null 2>&1; then
  host_python=python3
elif command -v python >/dev/null 2>&1; then
  host_python=python
else
  echo "Python is required on the host to verify the copied SQLite backup." >&2
  exit 1
fi
"${host_python}" - "${backup_dir}/trading_discipline.db" > "${backup_dir}/backup-check.json" <<'PY'
import json
from pathlib import Path
import sqlite3
import sys

path = Path(sys.argv[1])
db = sqlite3.connect(path)
integrity = db.execute("PRAGMA quick_check").fetchone()[0]
page_count = int(db.execute("PRAGMA page_count").fetchone()[0])
page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
db.close()
payload = {
    "path": str(path),
    "integrity": integrity,
    "size_bytes": path.stat().st_size,
    "sqlite_page_bytes": page_count * page_size,
}
print(json.dumps(payload, ensure_ascii=False), flush=True)
if integrity != "ok":
    raise SystemExit(f"host backup failed PRAGMA quick_check: {integrity}")
PY

git pull --ff-only
git rev-parse HEAD > "${backup_dir}/code-after.txt"
"${compose[@]}" up -d --build

for attempt in {1..30}; do
  if curl --fail --silent http://127.0.0.1:5173/api/health >/dev/null; then
    break
  fi
  if [[ "${attempt}" == "30" ]]; then
    echo "Health check failed; backup is in ${backup_dir}." >&2
    exit 1
  fi
  sleep 2
done

before_json="$(tr -d '\r\n' < "${backup_dir}/counts-before.json")"
"${compose[@]}" exec -T -e "COUNTS_BEFORE=${before_json}" backend python - > "${backup_dir}/counts-after.json" <<'PY'
import json
import os
import sqlite3

tables = (
    "holdings",
    "trade_logs",
    "next_day_plans",
    "action_recommendations",
    "action_recommendation_revisions",
    "recommendation_feedback",
    "recommendation_outcomes",
)
before_payload = json.loads(os.environ["COUNTS_BEFORE"])
before = before_payload.get("counts", before_payload)
db = sqlite3.connect("/app/data/trading_discipline.db")
integrity = db.execute("PRAGMA quick_check").fetchone()[0]
if integrity != "ok":
    raise SystemExit(f"live database failed PRAGMA quick_check after deployment: {integrity}")
after = {}
missing_after = []
for table in tables:
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        missing_after.append(table)
        continue
    after[table] = db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
db.close()
decreased = {
    table: {"before": int(before[table]), "after": int(after[table])}
    for table in tables
    if table in before and table in after and int(after[table]) < int(before[table])
}
payload = {
    "counts": after,
    "delta": {
        table: int(after[table]) - int(before[table])
        for table in tables
        if table in before and table in after
    },
    "integrity": integrity,
    "missing_before": before_payload.get("missing_tables", []),
    "missing_after": missing_after,
    "decreased": decreased,
}
print(json.dumps(payload, ensure_ascii=False), flush=True)
if missing_after:
    raise SystemExit(f"critical tables are missing after deployment: {missing_after}")
if decreased:
    raise SystemExit(f"critical row counts decreased: {decreased}")
PY

current_rev="$("${compose[@]}" exec -T backend python -m alembic current | awk 'NF {print $1; exit}')"
head_rev="$("${compose[@]}" exec -T backend python -m alembic heads | awk 'NF {print $1; exit}')"
if [[ -z "${current_rev}" || "${current_rev}" != "${head_rev}" ]]; then
  echo "Alembic revision mismatch: current=${current_rev:-missing}, head=${head_rev:-missing}" >&2
  exit 1
fi
echo "Alembic current/head: ${current_rev}"
"${compose[@]}" ps

echo "Upgrade completed. Backup and count evidence: ${backup_dir}"
