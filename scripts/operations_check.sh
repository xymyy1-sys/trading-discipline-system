#!/usr/bin/env bash
set -euo pipefail

threshold="${DISK_WARNING_PERCENT:-80}"
used="$(df -P . | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
echo "disk_used_percent=${used}"
docker system df
if ! curl -fsS --max-time 8 http://127.0.0.1:5173/api/health; then
  echo "service_health=failed"
  exit 3
fi
echo "service_health=ok"
if (( used >= threshold )); then
  echo "disk_warning=usage_${used}_percent_exceeds_${threshold}"
  exit 2
fi
