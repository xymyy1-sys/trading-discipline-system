#!/usr/bin/env bash
set -euo pipefail

retention_days="${BACKUP_RETENTION_DAYS:-14}"
stamp="$(date +%Y%m%d-%H%M%S)"
backup_dir="/app/data/backups"
backup_file="${backup_dir}/trading-${stamp}.db"
host_backup_dir="${BACKUP_OUTPUT_DIR:-$(pwd)/backups}"
host_backup_file="${host_backup_dir}/trading-${stamp}.db"
container="$(docker-compose ps -q backend)"
[[ -n "$container" ]] || { echo "Backend container not found"; exit 3; }

docker-compose exec -T backend python -c "import os, sqlite3; os.makedirs('${backup_dir}', exist_ok=True); source=sqlite3.connect('/app/data/trading_discipline.db'); target=sqlite3.connect('${backup_file}'); source.backup(target); target.execute('PRAGMA integrity_check'); target.close(); source.close()"
docker-compose exec -T backend python -c "from pathlib import Path; import time; cutoff=time.time()-${retention_days}*86400; [path.unlink() for path in Path('${backup_dir}').glob('trading-*.db') if path.stat().st_mtime < cutoff]"
mkdir -p "$host_backup_dir"
docker cp "${container}:${backup_file}" "$host_backup_file"
python3 -c "import sqlite3; db=sqlite3.connect('${host_backup_file}'); result=db.execute('PRAGMA integrity_check').fetchone()[0]; db.close(); assert result == 'ok', result"
find "$host_backup_dir" -maxdepth 1 -type f -name 'trading-*.db' -mtime "+${retention_days}" -delete
echo "Database backup verified: ${host_backup_file}"
