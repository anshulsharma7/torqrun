#!/usr/bin/env bash
# Switch this stack back from Team/Enterprise to the Community Edition (e.g. at the end of a
# subscription). All data stays in the database: users, secrets, audit events and notification
# channels are kept but inactive, and come back if you upgrade again.
#
#   make downgrade
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] || { echo "no .env here: run this in your Torqrun directory" >&2; exit 1; }
say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }

cp .env .env.pre-downgrade
chmod 600 .env.pre-downgrade
tmp=$(mktemp)
grep -vE '^(TORQRUN_EDITION|TORQRUN_EE_REGISTRY|TORQRUN_EE_VERSION|TORQRUN_LICENSE_KEY)=' .env > "$tmp" || true
cat "$tmp" > .env && rm -f "$tmp"
say "license and edition removed from .env (previous file: .env.pre-downgrade)"

WEB_PORT=$(grep -E '^WEB_PORT=' .env | tail -1 | cut -d= -f2-); WEB_PORT=${WEB_PORT:-8080}  # last wins, like Compose
say "rebuilding and restarting the Community Edition"
docker compose -f deploy/compose/docker-compose.yml --project-directory . up --build -d >/dev/null 2>&1
tools/dev/wait-for-ready.sh "http://127.0.0.1:$WEB_PORT" 300 >/dev/null
say "done: Community Edition. Your jobs, runs and agents are unchanged."
echo "    Paid data (extra users, secrets, audit log, alerts) is kept but inactive. Existing"
echo "    accounts can still sign in. Upgrade again any time: make upgrade LICENSE=<key>"
