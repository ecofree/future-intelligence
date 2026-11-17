#!/usr/bin/env bash
# Drill in a NEW database on the same local cluster. Never overwrite fi.
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "$0")" && pwd)
backup=${1:?Provide a completed trusted local backup directory}
test -f "$backup/COMPLETE"
(cd "$backup" && sha256sum --check SHA256SUMS)
target="fi_restore_$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
[[ "$target" =~ ^fi_restore_[0-9a-f]{16}$ ]] || exit 2
compose=(docker compose -f "$root/compose.yaml")
if [ "${FI_COMPARE_QUIESCENT:-0}" = 1 ]; then
  before=$("${compose[@]}" exec -T db psql -XAt -U postgres -d fi -v ON_ERROR_STOP=1 < "$root/fingerprint.sql")
fi
"${compose[@]}" exec -T db createdb -U postgres -T template0 "$target"
trap '"${compose[@]}" exec -T db dropdb -U postgres "$target" >/dev/null' EXIT
"${compose[@]}" exec -T db pg_restore -U postgres --exit-on-error -d "$target" < "$backup/database.dump"
"${compose[@]}" exec -T db psql -U postgres -d "$target" -v ON_ERROR_STOP=1 -c 'SELECT count(*) AS restored_migrations FROM fi_meta.schema_migrations; SELECT count(*) AS restored_experiments FROM fi.experiments;'
if [ "${FI_COMPARE_QUIESCENT:-0}" = 1 ]; then
  after=$("${compose[@]}" exec -T db psql -XAt -U postgres -d "$target" -v ON_ERROR_STOP=1 < "$root/fingerprint.sql")
  test "$before" = "$after" || { echo 'Restored data fingerprint mismatch' >&2; exit 1; }
  echo 'PASS: all fi and fi_meta table contents match quiescent original.'
fi
printf '%s\n' 'PASS: archive restored into a new disposable database; original database unchanged. Cross-host credential recovery and RPO/RTO remain separate acceptance.'
