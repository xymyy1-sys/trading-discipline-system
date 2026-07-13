#!/usr/bin/env bash
set -euo pipefail

if [[ "${CONFIRM_DATABASE_RESTORE:-}" != "RESTORE" ]]; then
  echo "Refusing restore. Set CONFIRM_DATABASE_RESTORE=RESTORE after verifying the backup path."
  exit 2
fi
if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "Usage: CONFIRM_DATABASE_RESTORE=RESTORE $0 /absolute/path/to/backup.db"
  exit 2
fi

backup_file="$(realpath "$1")"
python3 -c "import sqlite3; db=sqlite3.connect('$backup_file'); result=db.execute('PRAGMA integrity_check').fetchone()[0]; db.close(); assert result == 'ok', result"
container="$(docker-compose ps -q backend)"
[[ -n "$container" ]] || { echo "Backend container not found"; exit 3; }

./scripts/backup_database.sh
docker-compose stop backend
docker cp "$backup_file" "${container}:/app/data/trading_discipline.db"
docker start "$container"
for attempt in {1..20}; do
  if docker-compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5)"; then
    echo "Database restore completed and health check passed."
    exit 0
  fi
  sleep 2
done
echo "Restore completed but backend health check failed; use the pre-restore backup to roll back." >&2
exit 4
