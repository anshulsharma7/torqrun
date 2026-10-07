#!/usr/bin/env bash
# Sign in with email + password and print a new API token (for scripts and CI).
# Usage: tools/dev/api-token.sh <base_url> <email> [token_name]   (password from TORQRUN_PASSWORD)
set -euo pipefail
base=${1:?usage: api-token.sh <base_url> <email> [token_name]}
email=${2:?usage: api-token.sh <base_url> <email> [token_name]}
name=${3:-cli-$(date +%s)}
: "${TORQRUN_PASSWORD:?set TORQRUN_PASSWORD}"
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
jq -n --arg e "$email" --arg p "$TORQRUN_PASSWORD" '{email: $e, password: $p}' |
  curl -fsS -c "$jar" -H 'content-type: application/json' -H 'X-Torqrun-Client: cli' \
    -X POST "$base/api/v1/auth/login" -d @- >/dev/null
jq -n --arg n "$name" '{name: $n, expires_in_days: 1}' |
  curl -fsS -b "$jar" -H 'content-type: application/json' -H 'X-Torqrun-Client: cli' \
    -X POST "$base/api/v1/auth/tokens" -d @- | jq -r .token
curl -fsS -b "$jar" -H 'X-Torqrun-Client: cli' -X POST "$base/api/v1/auth/logout" >/dev/null
