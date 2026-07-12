#!/usr/bin/env bash
set -euo pipefail

: "${BASE_URL:?Set BASE_URL, for example https://trade.example.com}"
: "${AUTH_USERNAME:?Set AUTH_USERNAME}"
: "${AUTH_PASSWORD:?Set AUTH_PASSWORD}"

if [[ "${BASE_URL}" != https://* && "${ALLOW_HTTP:-false}" != "true" ]]; then
  echo "Refusing production acceptance over plain HTTP." >&2
  exit 1
fi

cookie_jar="$(mktemp)"
trap 'rm -f "${cookie_jar}"' EXIT

unauthorized="$(curl --silent --output /dev/null --write-out '%{http_code}' "${BASE_URL}/api/holdings")"
[[ "${unauthorized}" == "401" ]]

curl --fail --silent --cookie-jar "${cookie_jar}" \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"username":sys.argv[1],"password":sys.argv[2]}))' "${AUTH_USERNAME}" "${AUTH_PASSWORD}")" \
  "${BASE_URL}/api/auth/login" >/dev/null

curl --fail --silent --cookie "${cookie_jar}" "${BASE_URL}/api/holdings" >/dev/null
report="$(curl --fail --silent --cookie "${cookie_jar}" "${BASE_URL}/api/acceptance/report")"
python3 -c 'import json,sys; r=json.loads(sys.argv[1]); assert r["migration_version"] == "k7a4c9d2f1b3", r; assert r["audit_log"]["chain_valid"]; assert r["t_plus_one_passed"]' "${report}"

headers="$(curl --silent --head "${BASE_URL}/")"
grep -qi '^strict-transport-security:' <<<"${headers}"
grep -qi '^x-content-type-options:' <<<"${headers}"

echo "Production smoke acceptance passed for ${BASE_URL}."
