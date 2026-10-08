#!/usr/bin/env bash
# Upgrade this Torqrun stack from the Community Edition to Team/Enterprise, in place.
#
#   make upgrade LICENSE=<license key> TOKEN=<registry token>
#   tools/ops/upgrade.sh --license <key> [--registry-token <token>] [--version <tag>]
#
# Everything you have (jobs, runs, logs, schedules, workflows, agents, your admin account) stays
# where it is: both editions use the same database and volumes. The script:
#   1. checks the license key and backs up the database and artifacts,
#   2. saves the license in .env and switches the stack to the licensed images
#      (deploy/compose/docker-compose.enterprise.yml) of the version you're running,
#   3. restarts and verifies that the paid features are active.
# If any step fails, .env and the running images are put back as they were.
# You get the license key and the registry token when you buy a plan: anshulshrm12@gmail.com.
set -euo pipefail
cd "$(dirname "$0")/../.."

LICENSE="" TOKEN="" VERSION="" REGISTRY="ghcr.io/anshulsharma7" PULL=1 BACKUP=1
usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --license) LICENSE="$2"; shift 2 ;;
    --registry-token) TOKEN="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --registry) REGISTRY="$2"; shift 2 ;;
    --no-pull) PULL=0; shift ;;       # images are already present locally (testing)
    --no-backup) BACKUP=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;35m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[ -n "$LICENSE" ] || die "a license key is required: make upgrade LICENSE=<key> (plans: anshulshrm12@gmail.com)"
[[ "$LICENSE" =~ ^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]] || die "that doesn't look like a Torqrun license key"
[ -f .env ] || die "no .env here: run this in your Torqrun directory (where you ran make up)"
command -v docker >/dev/null || die "docker is required"
command -v curl >/dev/null || die "curl is required"

ENV_VAL() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- ; }
WEB_PORT=$(ENV_VAL WEB_PORT); WEB_PORT=${WEB_PORT:-8080}
BASE="http://127.0.0.1:$WEB_PORT"
COMMUNITY=(docker compose -f deploy/compose/docker-compose.yml --project-directory .)
ENTERPRISE=("${COMMUNITY[@]}" -f deploy/compose/docker-compose.enterprise.yml)

if [ "$(ENV_VAL TORQRUN_EDITION)" = enterprise ]; then
  say "already on the licensed images; updating the license key only"
fi

# The licensed images must match the version you run, so the database schema is the same.
if [ -z "$VERSION" ]; then
  VERSION=$(curl -fsS "$BASE/api/v1/system/info" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p' || true)
  VERSION=${VERSION:-latest}
fi
say "upgrading to Torqrun Team/Enterprise $VERSION"

if [ "$BACKUP" = 1 ] && "${COMMUNITY[@]}" ps --status running --services 2>/dev/null | grep -qx postgres; then
  say "backing up first (backups/)"
  tools/ops/backup.sh backups >/dev/null
  echo "    $(ls -dt backups/torqrun-* | head -1)"
fi

cp .env .env.pre-upgrade
chmod 600 .env.pre-upgrade
rollback() {
  printf '\033[1;31m==> upgrade failed: restoring the previous setup\033[0m\n' >&2
  cp .env.pre-upgrade .env
  if [ "$(ENV_VAL TORQRUN_EDITION)" = enterprise ]; then "${ENTERPRISE[@]}" up -d >/dev/null 2>&1 || true
  else "${COMMUNITY[@]}" up -d >/dev/null 2>&1 || true; fi
  echo "    nothing was lost: your data never left its volumes. Details above; help: anshulshrm12@gmail.com" >&2
}
trap 'rollback' ERR

set_env() {  # set_env NAME VALUE
  if grep -qE "^$1=" .env; then
    local tmp; tmp=$(mktemp)
    awk -v k="$1" -v v="$2" 'BEGIN{FS=OFS="="} $1==k{print k"="v; next} {print}' .env > "$tmp" && cat "$tmp" > .env && rm -f "$tmp"
  else
    printf '%s=%s\n' "$1" "$2" >> .env
  fi
}
say "saving the license and edition in .env"
set_env TORQRUN_LICENSE_KEY "$LICENSE"
set_env TORQRUN_EDITION enterprise
set_env TORQRUN_EE_REGISTRY "$REGISTRY"
set_env TORQRUN_EE_VERSION "$VERSION"
export TORQRUN_EE_REGISTRY="$REGISTRY" TORQRUN_EE_VERSION="$VERSION"

if [ "$PULL" = 1 ]; then
  if [ -n "$TOKEN" ]; then
    say "signing in to ${REGISTRY%%/*}"
    printf '%s' "$TOKEN" | docker login "${REGISTRY%%/*}" -u torqrun --password-stdin >/dev/null
  fi
  say "downloading the licensed images"
  "${ENTERPRISE[@]}" pull --quiet migrate api scheduler web
fi

say "restarting on the licensed images (your data stays in place)"
"${ENTERPRISE[@]}" up -d >/dev/null 2>&1
tools/dev/wait-for-ready.sh "$BASE" 300 >/dev/null

info=$(curl -fsS "$BASE/api/v1/system/info")
edition=$(printf '%s' "$info" | sed -n 's/.*"edition":"\([^"]*\)".*/\1/p')
if [ "$edition" = community ] || [ -z "$edition" ]; then
  echo "the stack started but the license was not accepted (see: docker compose logs api | grep -i license)" >&2
  false
fi
trap - ERR
rm -f .env.pre-upgrade
customer=$(printf '%s' "$info" | sed -n 's/.*"customer":"\([^"]*\)".*/\1/p')
expires=$(printf '%s' "$info" | sed -n 's/.*"expires_at":"\([^"]*\)".*/\1/p')
say "done: Torqrun ${edition^} for ${customer}, valid until ${expires}"
echo "    All your jobs, runs, schedules, workflows, agents and your admin account are unchanged."
echo "    New in the sidebar under Admin: Users, Secrets, Audit log, Notifications."
