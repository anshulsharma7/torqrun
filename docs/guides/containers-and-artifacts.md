# Containers and artifacts

## Running a job in a container

By default a job runs as a **process** on the agent's machine, as the agent's OS user. That's
simple and fast, but it isn't a sandbox. For isolation, or to run a job with its own
dependencies, choose **Executor → Container (Docker)** in the job form, or set this in the API:

```json
"spec": {
  "runtime": "python",
  "script": "import pandas; print(pandas.__version__)",
  "executor": "docker",
  "container": {"image": "python:3.12-slim", "network": "bridge", "memory_mb": 512, "cpus": 1}
}
```

Each run gets a fresh container:
- It runs `docker run --rm --init` as the **agent's UID/GID** (not root inside the container)
  with `--cap-drop ALL`, `--security-opt no-new-privileges` and `--pids-limit 1024`.
- `network: "none"` cuts the job off from the network. `memory_mb` and `cpus` are hard limits.
- The workspace (the script, `$HOME` and `$TORQRUN_ARTIFACTS_DIR`) is bind-mounted at
  `/workspace`.
- Environment variables and secrets are forwarded with `docker run --env NAME`, so their values
  never appear on a command line.
- Timeouts and cancellation stop the container (`docker stop`, then `kill`). After an agent
  crash, containers it started are removed on the next start.
- `pull` is `missing` (the default), `always` or `never`. Pull progress appears in the run's
  output.
- The image must contain `python3` (Python jobs) or `bash` (shell jobs).

### Agents with Docker

The agent offers the container executor when a local Docker daemon answers.
`TORQRUN_AGENT_DOCKER` is `auto` by default for agents run from source; the installer sets it
to `off` unless you pass `--docker`. The Fleet page shows a **docker** badge on such agents.
Container jobs are only ever handed to those agents. Agents without Docker skip them, and
process jobs queued behind a backlog of container jobs still run.

```bash
curl -fsSL https://torqrun.example.com/agent/install.sh | sudo bash -s -- --token tqe_… --docker
```

**Read before enabling:**
- Access to the Docker socket is root-equivalent on that host. A user who can create jobs on a
  Docker agent can't escape the container through Torqrun's flags, but the agent user itself
  is in the `docker` group. Use dedicated machines for Docker agents.
- The daemon must be local, because bind mounts refer to the agent's filesystem.
- The bundled `compose-agent` has no Docker, by design: mounting the host's socket into it
  would give every job root on your machine. Run a separate agent on a Docker host instead.

## Artifacts

Every run gets `TORQRUN_ARTIFACTS_DIR`, an empty directory. Files the job writes there are
uploaded when it ends, whatever its outcome, and can be downloaded from the run page
(**Artifacts**) or the API:

```bash
echo "rows,42" > "$TORQRUN_ARTIFACTS_DIR/report.csv"
```

```
GET /api/v1/runs/{run_id}/artifacts
GET /api/v1/runs/{run_id}/artifacts/{artifact_id}/download
```

Rules:
- Only regular files directly in the directory are uploaded. Symbolic links and directories are
  skipped, with a note in the run's log; archive a directory (`tar czf …`) if you need one.
  Names may use letters, digits, `.`, `-` and `_`.
- Limits per file, per attempt and per number of files: `TORQRUN_MAX_ARTIFACT_BYTES` (100 MiB),
  `TORQRUN_MAX_ATTEMPT_ARTIFACT_BYTES` (500 MiB) and `TORQRUN_MAX_ARTIFACTS_PER_ATTEMPT` (100).
  A file over a limit is reported in the log; the run's outcome doesn't change.
- Each attempt keeps its own files. A SHA-256 is recorded for every file.
- Downloads are always `application/octet-stream` attachments, so an uploaded HTML file can't
  run in the UI. Anyone who can view runs (the viewer role) can download.
- Files are stored on the API's disk under `TORQRUN_ARTIFACTS_DIR` (the `artifacts` volume in
  Compose) and deleted after `TORQRUN_ARTIFACT_RETENTION_DAYS` (30). Running several API
  replicas requires shared storage there; object storage is planned.
- Remote agents upload through the proxy: the bundled nginx allows 110 MiB bodies on the
  upload route only. Raise it if you raise the limits.
