#!/usr/bin/env bash
# Wait until the stack answers through the web proxy with a healthy DB and a connected agent.
# Usage: tools/dev/wait-for-ready.sh [base_url] [timeout_seconds]
set -euo pipefail
base=${1:-http://127.0.0.1:${WEB_PORT:-8080}}
timeout=${2:-180}
deadline=$(( $(date +%s) + timeout ))
while (( $(date +%s) < deadline )); do
  # system/info is public: database state plus the number of connected agents.
  if body=$(curl -fsS "$base/api/v1/system/info" 2>/dev/null) && grep -q '"status":"ok"' <<<"$body" \
     && grep -Eq '"agents_online":[1-9]' <<<"$body"; then
    echo "ready: $body"
    exit 0
  fi
  sleep 2
done
echo "stack not ready after ${timeout}s at $base" >&2
exit 1
