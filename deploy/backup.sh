#!/usr/bin/env bash
set -euo pipefail
umask 077
root=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?Provide a NEW private backup directory}
mkdir -m 700 -- "$out" # never overwrite
complete=false
trap 'if [ "$complete" != true ]; then printf "%s\n" "INCOMPLETE backup retained; do not restore it." >&2; fi' EXIT
compose=(docker compose -f "$root/compose.yaml")
"${compose[@]}" exec -T db pg_dump -U postgres -d fi --format=custom > "$out/database.dump"
"${compose[@]}" exec -T db pg_dumpall -U postgres --roles-only --no-role-passwords > "$out/roles.sql"
(cd "$out" && sha256sum database.dump roles.sql > SHA256SUMS)
printf '%s\n' 'PRIVATE: roles have no password hashes; preserve secret manager separately. Archive includes privileged SQL; restore only trusted archives.' > "$out/README"
printf '%s\n' COMPLETE > "$out/COMPLETE"
complete=true
printf '%s\n' 'Backup created. This is not yet a restore validation.'
