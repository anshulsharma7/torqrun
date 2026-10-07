#!/usr/bin/env bash
# Dependency and image vulnerability scans (same as CI). Fails on any known vulnerability in
# Python or web dependencies, and on fixable HIGH/CRITICAL ones in the built images.
#   tools/ops/security-scan.sh            # build images first: make up (or docker compose build)
set -euo pipefail
cd "$(dirname "$0")/../.."
TRIVY=aquasec/trivy:0.63.0
tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT

echo "== Python dependencies (pip-audit)"
uv export --frozen --all-packages --no-dev --no-emit-workspace --no-hashes > "$tmp"
uvx pip-audit --strict --disable-pip --no-deps -r "$tmp"

echo "== Web dependencies (pnpm audit)"
docker run --rm -e CI=true -e COREPACK_ENABLE_DOWNLOAD_PROMPT=0 -v "$PWD/apps/web:/app" -w /app node:22-alpine \
  sh -c "corepack enable >/dev/null 2>&1 && pnpm audit --audit-level low"

echo "== Container images (Trivy: fixable HIGH/CRITICAL)"
for image in torqrun/api:dev torqrun/scheduler:dev torqrun/agent:dev torqrun/web:dev; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache "$TRIVY" \
    image --quiet --scanners vuln --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 "$image"
  echo "$image: clean"
done
