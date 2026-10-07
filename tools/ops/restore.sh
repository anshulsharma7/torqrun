#!/usr/bin/env bash
# Restore a backup made by tools/ops/backup.sh into a Compose deployment. DESTRUCTIVE: the
# current database and artifacts of that stack are replaced.
#
#   tools/ops/restore.sh backups/torqrun-20261007T101500Z --yes
#
# The stack's .env must hold the same TORQRUN_SECRET_KEY as when the backup was taken
# (checked against the manifest). Restoring an older backup into a newer release is fine:
# migrations run afterwards. Restoring into an OLDER release is not supported.
set -euo pipefail
cd "$(dirname "$0")/../.."

dir=${1:?usage: restore.sh <backup_dir> --yes}
[ "${2:-}" = "--yes" ] || { echo "this replaces all data of the stack; re-run with --yes" >&2; exit 2; }
[ -f "$dir/manifest.json" ] || { echo "$dir: not a Torqrun backup (no manifest.json)" >&2; exit 2; }
COMPOSE=(docker compose -f deploy/compose/docker-compose.yml --project-directory .)
say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
started=$(date +%s)

say "Verifying checksums"
(cd "$dir" && sha256sum --quiet -c SHA256SUMS)

say "Checking the secret key"
"${COMPOSE[@]}" up -d postgres api >/dev/null
for _ in $(seq 1 60); do "${COMPOSE[@]}" exec -T api true 2>/dev/null && break; sleep 1; done
want=$(sed -n 's/.*"secret_key_id": "\([^"]*\)".*/\1/p' "$dir/manifest.json")
have=$("${COMPOSE[@]}" exec -T api python -c \
  "import hashlib, os; k=os.environ.get('TORQRUN_SECRET_KEY',''); d=hashlib.sha256(b'torqrun-secrets-v1:'+k.encode()).digest(); print(hashlib.sha256(b'torqrun-key-id:'+d).hexdigest()[:16] if k else '')")
if [ -n "$want" ] && [ "$want" != "$have" ]; then
  echo "TORQRUN_SECRET_KEY differs from the one used for this backup (key id $want, this stack $have)." >&2
  echo "Restore .env's TORQRUN_SECRET_KEY first, or secrets and notification channels will be unreadable." >&2
  exit 1
fi

say "Stopping API, scheduler, agent and web"
"${COMPOSE[@]}" stop api scheduler agent web >/dev/null

say "Restoring database"
"${COMPOSE[@]}" exec -T postgres psql -q -U torqrun -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS torqrun WITH (FORCE)" -c "CREATE DATABASE torqrun OWNER torqrun"
"${COMPOSE[@]}" exec -T postgres pg_restore -U torqrun -d torqrun --no-owner --exit-on-error < "$dir/db.dump"

say "Restoring artifacts"
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api \
  -c 'find /var/lib/torqrun/artifacts -mindepth 1 -delete && tar xzf - -C /var/lib/torqrun/artifacts' \
  < "$dir/artifacts.tar.gz"

say "Applying migrations (if the backup is from an older release) and starting"
"${COMPOSE[@]}" run --rm migrate >/dev/null
"${COMPOSE[@]}" up -d >/dev/null
say "Restore complete in $(( $(date +%s) - started ))s (schema $(sed -n 's/.*"schema_revision": "\([^"]*\)".*/\1/p' "$dir/manifest.json") -> head)"
echo "    The agent keeps its identity (its own volume). Agents enrolled after this backup was taken"
echo "    no longer exist in the database: re-enroll them."
