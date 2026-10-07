#!/usr/bin/env bash
# Run tools/bench/bench.py against a throwaway stack (own project, ports, volumes) and tear it
# down afterwards. Extra arguments go to bench.py, e.g. --runs 5000 --agents 50.
#   tools/bench/run-bench.sh --json /tmp/bench.json
set -euo pipefail
cd "$(dirname "$0")/../.."
export COMPOSE_PROJECT_NAME=torqrun-bench WEB_PORT=38080 API_PORT=38000 POSTGRES_PORT=55435
export TORQRUN_BOOTSTRAP_ADMIN_EMAIL=bench@example.com TORQRUN_BOOTSTRAP_ADMIN_PASSWORD=bench-password-1
export TORQRUN_LOG_LEVEL=WARNING  # per-request INFO logs would measure the log pipeline instead
REPLICAS=${API_REPLICAS:-1}  # >1: API replicas behind nginx (benchmarked through the web port)
COMPOSE=(docker compose -f deploy/compose/docker-compose.yml --project-directory .)
BASE="http://127.0.0.1:$API_PORT"
if [ "$REPLICAS" -gt 1 ]; then
  COMPOSE+=(-f deploy/compose/docker-compose.scale.yml)
  BASE="http://127.0.0.1:$WEB_PORT"
fi
[ -n "${KEEP:-}" ] || trap '"${COMPOSE[@]}" down -v >/dev/null 2>&1 || true' EXIT

tools/dev/init-env.sh >/dev/null
echo "starting a throwaway stack ($COMPOSE_PROJECT_NAME)…"
"${COMPOSE[@]}" up --build -d --scale api="$REPLICAS" >/dev/null 2>&1
tools/dev/wait-for-ready.sh "http://127.0.0.1:$WEB_PORT" 300 >/dev/null
token=$(TORQRUN_PASSWORD=$TORQRUN_BOOTSTRAP_ADMIN_PASSWORD tools/dev/api-token.sh "$BASE" "$TORQRUN_BOOTSTRAP_ADMIN_EMAIL" bench)
enroll=$(grep '^TORQRUN_DEV_ENROLLMENT_TOKEN=' .env | cut -d= -f2)
echo "API replicas: $REPLICAS (benchmarking $BASE)"
uv run python tools/bench/bench.py --base-url "$BASE" --token "$token" --enrollment-token "$enroll" "$@"
