#!/usr/bin/env bash
# Register a job from a script file, run it, wait for the result and print its output.
# Usage: examples/api/run_job.sh <script.py|script.sh> [base_url] [args...]
# Needs curl and jq, and an API token (Account -> API tokens in the UI) in TORQRUN_TOKEN.
set -euo pipefail
: "${TORQRUN_TOKEN:?set TORQRUN_TOKEN to an API token (create one under Account in the web UI)}"
api() { curl -fsS -H "Authorization: Bearer $TORQRUN_TOKEN" "$@"; }
script=${1:?usage: run_job.sh <script.py|script.sh> [base_url] [args...]}
base=${2:-http://127.0.0.1:8080}
shift $(( $# >= 2 ? 2 : 1 ))
case "$script" in *.py) runtime=python ;; *) runtime=shell ;; esac
name="$(basename "${script%.*}" | tr 'A-Z_' 'a-z-')-$(date +%s)"

job=$(jq -n --arg name "$name" --arg runtime "$runtime" --rawfile script "$script" \
  '{name: $name, spec: {runtime: $runtime, script: $script, args: $ARGS.positional}}' --args "$@" |
  api -X POST "$base/api/v1/jobs" -H 'content-type: application/json' -d @-)
job_id=$(jq -r .id <<<"$job")
run_id=$(api -X POST "$base/api/v1/jobs/$job_id/runs" | jq -r .id)
echo "job $name ($job_id) -> run $run_id"

for _ in $(seq 1 120); do
  run=$(api "$base/api/v1/runs/$run_id")
  status=$(jq -r .status <<<"$run")
  case "$status" in SUCCEEDED|FAILED|TIMED_OUT|CANCELLED|LOST) break ;; esac
  sleep 1
done
api "$base/api/v1/runs/$run_id/logs" | jq -r '.chunks[] | (if .stream == "stdout" then "" else "[\(.stream)] " end) + (.data | rtrimstr("\n"))'
jq -r '"status=\(.status) exit_code=\(.exit_code) duration=\(.duration_seconds)s"' <<<"$run"
[[ "$status" == SUCCEEDED ]]
