#!/usr/bin/env bash
# Backup/restore drill on a throwaway stack (own project, ports and volumes): create real data,
# back up, destroy the control plane's volumes (database and artifacts; agents keep their
# identity on their own machines), restore into fresh ones, and prove it all came back:
# users, jobs and runs, artifacts (byte-identical), and encrypted secrets (a job that needs one
# runs successfully after the restore). Prints timings. Never touches your normal stack.
set -euo pipefail
cd "$(dirname "$0")/../.."

export COMPOSE_PROJECT_NAME=torqrun-drill WEB_PORT=28080 API_PORT=28000 POSTGRES_PORT=55434
export TORQRUN_BOOTSTRAP_ADMIN_EMAIL=drill@example.com TORQRUN_BOOTSTRAP_ADMIN_PASSWORD=drill-password-1
BASE=http://127.0.0.1:$WEB_PORT
COMPOSE=(docker compose -f deploy/compose/docker-compose.yml --project-directory .)
WORK=$(mktemp -d)
LOG="$WORK/compose.log"
cleanup() {
  status=$?
  if [ "$status" -ne 0 ] && [ -f "$LOG" ]; then
    echo "--- last compose output ---" >&2; tail -40 "$LOG" >&2
    "${COMPOSE[@]}" ps -a >&2 || true
    "${COMPOSE[@]}" logs --no-color --tail 30 >&2 || true
  fi
  "${COMPOSE[@]}" down -v >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT
say() { printf '\033[1;36m[drill]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[drill] FAILED:\033[0m %s\n' "$*" >&2; exit 1; }

tools/dev/init-env.sh >/dev/null
say "starting a throwaway stack ($COMPOSE_PROJECT_NAME)"
"${COMPOSE[@]}" up --build -d >>"$LOG" 2>&1
tools/dev/wait-for-ready.sh "$BASE" 300 >/dev/null
TOKEN=$(TORQRUN_PASSWORD=$TORQRUN_BOOTSTRAP_ADMIN_PASSWORD tools/dev/api-token.sh "$BASE" "$TORQRUN_BOOTSTRAP_ADMIN_EMAIL" drill)
api() { curl -fsS -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' "$@"; }

run_job() {  # run_job <job_id> -> prints final run JSON
  local run_id status
  run_id=$(api -X POST "$BASE/api/v1/jobs/$1/runs" | jq -r .id)
  for _ in $(seq 1 60); do
    status=$(api "$BASE/api/v1/runs/$run_id" | jq -r .status)
    case "$status" in SUCCEEDED|FAILED|TIMED_OUT|CANCELLED|LOST) break ;; esac
    sleep 1
  done
  api "$BASE/api/v1/runs/$run_id"
}

# Secrets are a Team/Enterprise feature: exercised only when the stack is licensed for them.
SECRETS=$(curl -fsS "$BASE/api/v1/system/info" | jq -r '.features | index("secrets") != null')
artifact_script='head -c 200000 /dev/urandom > "$TORQRUN_ARTIFACTS_DIR/data.bin"; echo ok'
if [ "$SECRETS" = true ]; then
  say "creating data: a secret, a job that needs it and writes an artifact, a run"
  api -X POST "$BASE/api/v1/secrets" -d '{"name":"drill-pass","value":"correct-value-42"}' >/dev/null
  script='test "$PASS" = "correct-value-42" || { echo "wrong secret" >&2; exit 9; }; '"$artifact_script"
  spec=$(jq -n --arg s "$script" '{runtime:"shell", script:$s, secrets:{PASS:"drill-pass"}}')
else
  say "creating data: a job that writes an artifact, a run (Community Edition: no secrets)"
  spec=$(jq -n --arg s "$artifact_script" '{runtime:"shell", script:$s}')
fi
job_id=$(jq -n --argjson spec "$spec" '{name:"drill-job", spec:$spec}' | api -X POST "$BASE/api/v1/jobs" -d @- | jq -r .id)
run=$(run_job "$job_id")
[ "$(jq -r .status <<<"$run")" = SUCCEEDED ] || fail "seed run did not succeed: $run"
run_id=$(jq -r .id <<<"$run")
artifact=$(api "$BASE/api/v1/runs/$run_id/artifacts" | jq '.[0]')
sha_before=$(jq -r .sha256 <<<"$artifact")
runs_before=$(api "$BASE/api/v1/runs?limit=1" | jq .total)

say "backing up"
t0=$(date +%s)
tools/ops/backup.sh "$WORK" >>"$LOG" 2>&1
backup_s=$(( $(date +%s) - t0 ))
backup_dir=$(ls -d "$WORK"/torqrun-*)
size=$(du -sh "$backup_dir" | cut -f1)

say "destroying the control plane: database and artifact volumes"
"${COMPOSE[@]}" down >>"$LOG" 2>&1
docker volume rm "${COMPOSE_PROJECT_NAME}_pgdata" "${COMPOSE_PROJECT_NAME}_artifacts" >/dev/null
"${COMPOSE[@]}" up -d postgres migrate api >>"$LOG" 2>&1
for _ in $(seq 1 60); do
  [ "$(curl -fsS "http://127.0.0.1:$API_PORT/api/v1/system/info" 2>/dev/null | jq -r .database.status)" = ok ] && break
  sleep 2
done
empty=$("${COMPOSE[@]}" exec -T postgres psql -U torqrun -d torqrun -tAc "SELECT count(*) FROM runs")
[ "$empty" = 0 ] || fail "fresh database is not empty"
say "fresh control plane is empty (0 runs); restoring"

t0=$(date +%s)
tools/ops/restore.sh "$backup_dir" --yes >>"$LOG" 2>&1
restore_s=$(( $(date +%s) - t0 ))
tools/dev/wait-for-ready.sh "$BASE" 300 >/dev/null

say "verifying"
TOKEN=$(TORQRUN_PASSWORD=$TORQRUN_BOOTSTRAP_ADMIN_PASSWORD tools/dev/api-token.sh "$BASE" "$TORQRUN_BOOTSTRAP_ADMIN_EMAIL" verify)
[ "$(api "$BASE/api/v1/runs?limit=1" | jq .total)" = "$runs_before" ] || fail "run count changed"
[ "$(api "$BASE/api/v1/runs/$run_id" | jq -r .status)" = SUCCEEDED ] || fail "restored run missing"
api "$BASE/api/v1/runs/$run_id/artifacts/$(jq -r .id <<<"$artifact")/download" -o "$WORK/data.bin"
[ "$(sha256sum "$WORK/data.bin" | cut -d' ' -f1)" = "$sha_before" ] || fail "artifact differs after restore"
if [ "$SECRETS" = true ]; then
  [ "$(api "$BASE/api/v1/secrets" | jq -r '.[0].readable')" = true ] || fail "secret not readable"
fi
rerun=$(run_job "$job_id")
[ "$(jq -r .status <<<"$rerun")" = SUCCEEDED ] || fail "job using the restored secret failed: $(jq -r .error_summary <<<"$rerun")"

also=""; [ "$SECRETS" = true ] && also=", and secret (decrypted by a new run)"
say "PASSED: backup ${backup_s}s (${size}), restore ${restore_s}s; runs, artifact (sha256 match),"
say "        users${also} survived losing the database and artifacts."
