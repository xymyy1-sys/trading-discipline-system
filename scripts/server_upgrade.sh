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

"${compose[@]}" exec -T backend python - <<'PY'
import json
import sqlite3

source = sqlite3.connect("/app/data/trading_discipline.db")
backup = sqlite3.connect("/app/data/predeploy-backup.db")
source.backup(backup)
backup.close()
counts = {}
for table in ("holdings", "trade_logs", "next_day_plans"):
    try:
        counts[table] = source.execute(f"select count(*) from {table}").fetchone()[0]
    except sqlite3.OperationalError:
        counts[table] = 0
print(json.dumps(counts, ensure_ascii=False))
source.close()
PY

"${compose[@]}" cp backend:/app/data/predeploy-backup.db "${backup_dir}/trading_discipline.db"
"${compose[@]}" exec -T backend python -c 'import json,sqlite3; db=sqlite3.connect("/app/data/trading_discipline.db"); print(json.dumps({t:db.execute(f"select count(*) from {t}").fetchone()[0] for t in ("holdings","trade_logs","next_day_plans")}))' > "${backup_dir}/counts-before.json"

git pull --ff-only
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

"${compose[@]}" exec -T backend python -c 'import json,sqlite3; db=sqlite3.connect("/app/data/trading_discipline.db"); print(json.dumps({t:db.execute(f"select count(*) from {t}").fetchone()[0] for t in ("holdings","trade_logs","next_day_plans")}))' > "${backup_dir}/counts-after.json"
cmp "${backup_dir}/counts-before.json" "${backup_dir}/counts-after.json"
"${compose[@]}" exec -T backend python -m alembic current
"${compose[@]}" ps

echo "Upgrade completed. Backup and count evidence: ${backup_dir}"
