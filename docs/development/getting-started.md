# Development guide

## 1. Install prerequisites (Ubuntu / Debian)

```bash
sudo apt update
sudo apt install -y git make curl docker.io docker-compose-v2
sudo usermod -aG docker "$USER"     # then log out and back in so `docker` works without sudo
docker compose version              # must print v2.x

# Python toolchain (only needed for running checks/tests outside Docker)
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Node.js is **not** required on the host: `make web-check` runs the frontend checks in a
`node:22-alpine` container. If you want the Vite dev server with hot reload, install Node 22+
and run `corepack enable`.

For other distributions, install Docker Engine per <https://docs.docker.com/engine/install/>.

## 2. Run the stack

```bash
make up      # create .env (first run), build + start + wait for a healthy stack and agent
make ps      # status
make logs    # follow logs
make down    # stop (data kept in the `pgdata` volume)
make clean   # stop and delete data
```

| Service | Purpose | Host address |
|---|---|---|
| `postgres` | metadata store | `127.0.0.1:55432` (user/db `torqrun`) |
| `migrate` | one-shot `alembic upgrade head`; must exit 0 before `api` starts | — |
| `api` | FastAPI control plane | `127.0.0.1:8000` |
| `scheduler` | lease reaper, retry promotion, requeue of unacknowledged work, key cleanup | — |
| `agent` | bundled agent (`compose-agent`); runs jobs inside its container as uid 10001 | — (connects out to `api`) |
| `web` | nginx serving the UI, proxying `/api`, `/healthz`, `/readyz` to `api` | `127.0.0.1:8080` |

## 3. Checks and tests

```bash
uv sync                 # create .venv with all workspace packages + dev tools
make lint typecheck     # ruff + mypy --strict
make test-unit          # no database needed
make up && make test-integration   # integration + e2e (real API and agent processes); fresh DB per test
make web-check          # frontend typecheck, unit tests, build (in Docker)
make e2e-ui             # Playwright browser tests against the running stack (in Docker)
make check              # everything CI runs except e2e-ui
```

Integration and e2e tests read `TORQRUN_TEST_DATABASE_URL` (a server URL whose user may
create databases). Without it they are **skipped**, not silently passed — pytest prints the
reason. The e2e tests in `tests/e2e/` start real `torqrun-api` and `torqrun-agent`
processes and run real scripts.

| Test layer | Where | Needs |
|---|---|---|
| Unit | `packages/*/tests`, `apps/*/tests`, `apps/web/src/**/*.test.tsx` | nothing (agent tests spawn `python3`/`bash`) |
| Integration | `tests/integration` | PostgreSQL |
| End-to-end (API + agent processes) | `tests/e2e` | PostgreSQL |
| Browser | `apps/web/e2e` (Playwright) | running stack |

### Frontend with hot reload

```bash
make up                       # API on 127.0.0.1:8000
cd apps/web && pnpm install && pnpm dev   # http://127.0.0.1:5173, proxies /api to :8000
```

### API without Docker

```bash
export TORQRUN_DATABASE_URL=postgresql://torqrun:torqrun_dev@127.0.0.1:55432/torqrun
uv run python -m torqrun_db.migrate upgrade
TORQRUN_LOG_FORMAT=console uv run torqrun-api   # http://127.0.0.1:8000
```

## 4. Database migrations

Migrations live in `packages/torqrun-db/src/torqrun_db/migrations/` and ship inside the
package, so containers run them without the source tree.

```bash
uv run python -m torqrun_db.migrate heads                   # latest revision in this build
uv run python -m torqrun_db.migrate current                 # revision of $TORQRUN_DATABASE_URL
uv run python -m torqrun_db.migrate upgrade                 # to head
uv run python -m torqrun_db.migrate downgrade <rev|base>
uv run python -m torqrun_db.migrate revision -m "add runs" --autogenerate
```

`/readyz` returns 503 until the database schema matches the build's head revision, so a
container never serves traffic against a schema it doesn't understand.

## 5. Configuration

### API / control plane

All server settings are environment variables prefixed `TORQRUN_`:

| Variable | Default | Notes |
|---|---|---|
| `TORQRUN_DATABASE_URL` | — (required) | `postgresql://user:pass@host:port/db`; only PostgreSQL is supported |
| `TORQRUN_ENVIRONMENT` | `development` | `development` / `test` / `production` |
| `TORQRUN_API_HOST` | `127.0.0.1` (`0.0.0.0` in the container) | bind address |
| `TORQRUN_API_PORT` | `8000` | |
| `TORQRUN_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `TORQRUN_LOG_FORMAT` | `json` | `json` or `console` |
| `TORQRUN_DB_POOL_SIZE` / `TORQRUN_DB_MAX_OVERFLOW` | `10` / `10` | SQLAlchemy pool |
| `TORQRUN_READINESS_TIMEOUT_SECONDS` | `3` | DB check timeout for `/readyz` |
| `TORQRUN_DEV_ENROLLMENT_TOKEN` | unset | Shared token agents use to enroll. **Development only** (rejected otherwise; 16+ chars) |
| `TORQRUN_LEASE_TTL_SECONDS` | `60` | How long an agent's claim on a run stays valid without a heartbeat |
| `TORQRUN_AGENT_OFFLINE_AFTER_SECONDS` | `30` | UI shows an agent offline after this long without a heartbeat |
| `TORQRUN_CLAIM_POLL_INTERVAL_SECONDS` / `TORQRUN_STREAM_POLL_INTERVAL_SECONDS` | `0.5` / `0.5` | Long-poll and live-stream check interval |

| `TORQRUN_SECRET_KEY` | unset (generated into `.env`) | Master key for job secrets, 32+ chars. **Required in production.** Back it up |
| `TORQRUN_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | unset | Create this admin on startup if no user exists |
| `TORQRUN_SESSION_TTL_HOURS` | `12` | Browser session lifetime |
| `TORQRUN_COOKIE_SECURE` | on in production | `Secure` flag on the session cookie (needs HTTPS) |
| `TORQRUN_LOGIN_MAX_ATTEMPTS` / `TORQRUN_LOGIN_WINDOW_SECONDS` | `10` / `300` | Failed sign-ins allowed per IP + email per window |
| `TORQRUN_METRICS_TOKEN` | unset | If set, `GET /metrics` requires `Authorization: Bearer <token>` |
| `TORQRUN_ARTIFACTS_DIR` | `/var/lib/torqrun/artifacts` | Where run artifacts are stored (shared storage if you run several API replicas) |
| `TORQRUN_MAX_ARTIFACT_BYTES` / `TORQRUN_MAX_ATTEMPT_ARTIFACT_BYTES` / `TORQRUN_MAX_ARTIFACTS_PER_ATTEMPT` | 100 MiB / 500 MiB / 100 | Artifact limits |
| `TORQRUN_ARTIFACT_RETENTION_DAYS` | `30` | Artifacts older than this are deleted |

Production mode requires `TORQRUN_SECRET_KEY`, refuses the development enrollment token and
an `http://` public URL. See [security](../operations/security.md) for the checklist.

### Scheduler (`torqrun-scheduler`)

| Variable | Default | Notes |
|---|---|---|
| `TORQRUN_DATABASE_URL` | — (required) | |
| `TORQRUN_LEASE_TTL_SECONDS` | `60` | **Must match the API.** The reaper waits this long after an API came up before declaring silent agents lost |
| `TORQRUN_DISPATCH_ACK_TIMEOUT_SECONDS` | `30` | Claimed-but-unacknowledged runs return to the queue |
| `TORQRUN_SCHEDULER_INTERVAL_SECONDS` | `1` | Loop period |
| `TORQRUN_SECRET_KEY` | unset | **Same value as the API.** Decrypts notification channel settings |
| `TORQRUN_PUBLIC_URL` | unset | Base URL for links in notifications |
| `TORQRUN_NOTIFY_AGENT_OFFLINE_AFTER_SECONDS` | `120` | Silence before an `agent.offline` notification |
| `TORQRUN_NOTIFICATION_RETENTION_DAYS` | `30` | Delivery history kept this long |
| `TORQRUN_SMTP_HOST` / `_PORT` / `_USERNAME` / `_PASSWORD` / `_FROM` / `_STARTTLS` | unset / 587 / – / – / – / true | Email notifications |
| `TORQRUN_IDEMPOTENCY_TTL_HOURS` | `24` | How long `Idempotency-Key`s are remembered |

Run several replicas if you like: every step uses `SELECT … FOR UPDATE SKIP LOCKED`.

### Agent

| Variable | Default | Notes |
|---|---|---|
| `TORQRUN_AGENT_SERVER_URL` | — (required) | `https://…`; plain `http://` only for localhost unless the next setting is on |
| `TORQRUN_AGENT_ALLOW_INSECURE_HTTP` | `false` | Allow `http://` to a non-local host (trusted private networks only) |
| `TORQRUN_AGENT_ENROLLMENT_TOKEN` | unset | Needed only for the first start (registration) |
| `TORQRUN_AGENT_NAME` | hostname | Unique per control plane |
| `TORQRUN_AGENT_STATE_DIR` | `~/.local/state/torqrun-agent` (`/var/lib/torqrun-agent` as root) | Identity file (mode 0600) and job workspaces |
| `TORQRUN_AGENT_MAX_SLOTS` | `2` | Jobs run concurrently |
| `TORQRUN_AGENT_QUEUES` / `TORQRUN_AGENT_TAGS` | `default` / none | Comma-separated |
| `TORQRUN_AGENT_JOB_PATH` | `/usr/local/bin:/usr/bin:/bin` | `PATH` given to jobs (`python3` and `bash` are resolved from it) |
| `TORQRUN_AGENT_MAX_LOG_BYTES` | 10 MiB | Output beyond this is dropped with a marker line |
| `TORQRUN_AGENT_KILL_GRACE_SECONDS` | `10` | SIGTERM → SIGKILL delay on timeout |
| `TORQRUN_AGENT_DOCKER` | `auto` | `auto`/`on`/`off`: offer the container executor ([guide](../guides/containers-and-artifacts.md)) |
| `TORQRUN_AGENT_SHUTDOWN_GRACE_SECONDS` | `20` | On SIGTERM, wait this long for running jobs before killing them |
| `TORQRUN_AGENT_HEARTBEAT_INTERVAL_SECONDS` | `5` | Also how quickly a cancel reaches a job that prints nothing |

Agent commands (also shown in the UI under **Fleet → Connect agent**): `torq-agent register` (enroll and save identity), `torq-agent start`
(registers first if needed), `torq-agent status` (identity + connectivity check).

Compose-level variables (`POSTGRES_PASSWORD`, `POSTGRES_PORT`, `API_PORT`, `WEB_PORT`) are
documented in `.env.example`.

## 6. Repository layout

```
apps/api/                     FastAPI control plane (torqrun_api)
apps/agent/                   agent daemon + CLI (torqrun_agent)
apps/web/                     React + TypeScript + Vite + Tailwind UI
packages/torqrun-core/      pure domain logic: run state machine, ids (no I/O)
packages/torqrun-protocol/  wire contract shared by API and agent (Pydantic models)
packages/torqrun-db/        SQLAlchemy models, run lifecycle operations, Alembic migrations
deploy/docker/            Dockerfiles + nginx config
deploy/compose/           docker-compose.yml
tests/integration/            tests that need a real PostgreSQL
tests/e2e/                    real API + agent processes
examples/                     example jobs and API usage scripts
tools/dev/                    developer scripts
docs/                         architecture, development, (later) operations & security
```

`apps/scheduler` is added with M2/M4 (retry promotion, lease reaper, cron), not before.

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `permission denied ... docker.sock` | You're not in the `docker` group yet: `sudo usermod -aG docker $USER`, then log out/in. |
| `port is already allocated` | Change `WEB_PORT` / `API_PORT` / `POSTGRES_PORT` in `.env`. |
| UI says "Migrations pending" | `make migrate`, then check `make logs` for the `migrate` service. |
| `make up` times out | `docker compose -f deploy/compose/docker-compose.yml --project-directory . logs migrate api` |
| Integration tests skipped | Set `TORQRUN_TEST_DATABASE_URL` or use `make test-integration` with the stack running. |
| Agent exits with `invalid agent credential` | Its saved identity belongs to a database that was reset. Remove the identity: `docker compose -f deploy/compose/docker-compose.yml --project-directory . down agent && docker volume rm torqrun_agentstate`, then `make up`. Outside Docker delete `$TORQRUN_AGENT_STATE_DIR/identity.json`. |
| Agent: `an agent named … is already enrolled` | Names are unique. Set a different `TORQRUN_AGENT_NAME` (revoking agents arrives in M3). |
| Run marked *Lost* | The agent stopped heartbeating for longer than the lease (crash, power-off, long network partition). Lost runs retry only if the job sets **If the agent dies mid-run → Retry**. |
| Runs stay *Queued* | Check **Queues** (paused? at its limit?), the job's *Max concurrent runs*, and that an online agent serves the queue. |
| Job can't find `python3` | Set `TORQRUN_AGENT_JOB_PATH` to include the interpreter's directory. |
