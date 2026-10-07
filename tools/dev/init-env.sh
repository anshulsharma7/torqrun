#!/usr/bin/env bash
# Create .env from .env.example on first run, filling in fresh random development secrets.
# Never overwrites an existing value; adds secrets that are missing from an older .env.
set -euo pipefail
cd "$(dirname "$0")/../.."

rand() { openssl rand -hex "$1" 2>/dev/null || head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "created .env"
fi

ensure() {  # ensure NAME VALUE: set NAME if it is missing or empty
  local name=$1 value=$2
  if grep -q "^${name}=.\+" .env; then return; fi
  if grep -q "^${name}=" .env; then
    sed -i.bak "s|^${name}=.*|${name}=${value}|" .env && rm -f .env.bak
  else
    echo "${name}=${value}" >> .env
  fi
  echo "generated ${name} in .env"
}

ensure TORQRUN_DEV_ENROLLMENT_TOKEN "$(rand 24)"
ensure TORQRUN_SECRET_KEY "$(rand 32)"
