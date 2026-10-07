#!/usr/bin/env bash
# Back up a Torqrun Compose deployment: database (pg_dump custom format) + artifact files.
#
#   tools/ops/backup.sh [output_dir]          # default: ./backups
#   COMPOSE_PROJECT_NAME=other tools/ops/backup.sh   # another stack
#
# Writes <output_dir>/torqrun-<UTC timestamp>/ with db.dump, artifacts.tar.gz, manifest.json
# and SHA256SUMS. Consistent: pg_dump takes a snapshot; artifacts are copied afterwards, so
# a backup may hold a few files whose rows are newer than the dump (harmless: restore skips
# nothing, those files are simply unreferenced).
#
# NOT included: TORQRUN_SECRET_KEY. Keep it (and .env) somewhere safe and separate: without
# the same key, restored secrets and notification channels can't be decrypted.
set -euo pipefail
cd "$(dirname "$0")/../.."

COMPOSE=(docker compose -f deploy/compose/docker-compose.yml --project-directory .)
out_root=${1:-backups}
stamp=$(date -u +%Y%m%dT%H%M%SZ)
dir="$out_root/torqrun-$stamp"
mkdir -p "$dir"
chmod 700 "$dir"
started=$(date +%s)

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }

say "Dumping database"
"${COMPOSE[@]}" exec -T postgres pg_dump -U torqrun -d torqrun --format=custom --compress=6 > "$dir/db.dump"

say "Archiving artifacts"
"${COMPOSE[@]}" exec -T api tar czf - -C /var/lib/torqrun/artifacts . > "$dir/artifacts.tar.gz"

say "Writing manifest"
revision=$("${COMPOSE[@]}" exec -T postgres psql -U torqrun -d torqrun -tAc "SELECT version_num FROM alembic_version")
key_id=$("${COMPOSE[@]}" exec -T api python -c \
  "import hashlib, os; k=os.environ.get('TORQRUN_SECRET_KEY',''); d=hashlib.sha256(b'torqrun-secrets-v1:'+k.encode()).digest(); print(hashlib.sha256(b'torqrun-key-id:'+d).hexdigest()[:16] if k else '')")
version=$("${COMPOSE[@]}" exec -T api python -c "import torqrun_api; print(torqrun_api.__version__)")
cat > "$dir/manifest.json" <<JSON
{
  "created_at": "$stamp",
  "torqrun_version": "$version",
  "schema_revision": "$revision",
  "secret_key_id": "$key_id",
  "files": ["db.dump", "artifacts.tar.gz"]
}
JSON
(cd "$dir" && sha256sum db.dump artifacts.tar.gz manifest.json > SHA256SUMS)

say "Backup complete in $(( $(date +%s) - started ))s: $dir"
du -sh "$dir"/* | sed 's/^/    /'
echo "    Remember: back up TORQRUN_SECRET_KEY separately (secret key id: ${key_id:-none})."
