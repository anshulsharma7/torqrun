# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/) (pre-1.0: minor versions may break compatibility).

## [Unreleased]

### Changed — Editions (open core)
- Torqrun is now open core. The **Community Edition** (Apache-2.0, public repository) contains
  the orchestration engine with one admin account. **Team and Enterprise** add multiple users
  and roles, the secrets manager, the audit log and notifications, via a proprietary extension
  package activated by a signed license key (checked offline; seats; 14-day grace after
  expiry).
- Community behaviour: user-management, secrets, audit and notification APIs are absent
  (404); jobs using `secrets` are refused with HTTP 402 and an upgrade hint; the UI shows those
  features as locked, with an upgrade path; `/api/v1/system/info` reports `edition`,
  `features` and `license`.
- Extension points: `torqrun_api.edition`, `torqrun_db.events`, scheduler `periodic_steps` /
  `after_tick` / `on_stop`, and a web extension registry.
- `TORQRUN_SECRET_KEY` is only required in production when Enterprise features are licensed.

### Added — M8: production hardening
- Load test tool (`tools/bench`, `make bench`) with simulated agents speaking the real protocol;
  results and method in docs/operations/benchmarks.md.
- Backup and restore (`make backup`, `make restore`) with checksums, a secret-key fingerprint
  check and automatic migration; `make backup-drill` proves it on a throwaway stack (also in CI).
- Upgrade tests: an M3-era database migrates to head and its queued run still executes; every
  migration reverts. Agent/control-plane wire-compatibility tests.
- Security scans in CI (`make security`): pip-audit, pnpm audit, Trivy on all images.
- `deploy/compose/docker-compose.scale.yml` for several API replicas; example Prometheus alert
  rules; production, upgrade, backup and benchmark guides; SECURITY.md.

### Changed
- Dispatch no longer serializes all claimers of a queue: advisory locks only where concurrency
  limits exist (non-blocking for per-job limits), and claimers lock only the rows they need.
  With 3 API replicas, end-to-end throughput went from 41 to 109 runs/s.
- Agent protocol messages ignore unknown fields, and job specs are sent without default-valued
  fields, so agents and control plane can be one release apart.
- Database connection failures return 503 with `Retry-After` (agents retry) instead of 500.
- Compose sets PostgreSQL `max_connections` (`POSTGRES_MAX_CONNECTIONS`, default 200).
- Web image on nginx 1.30 with Alpine security updates applied at build (was 42 fixable
  HIGH/CRITICAL findings, now 0).

### Added — M7: notifications, container executor, artifacts
- Notification channels (admin): Slack, Microsoft Teams (Adaptive Cards), email (SMTP) and
  generic webhooks signed with HMAC-SHA256 (`X-Torqrun-Signature`). Events: run failed/timed
  out/lost after retries, run succeeded, workflow failed/succeeded, agent offline; per-channel
  queue filter; test button; delivery history with retry.
- Transactional outbox (`notification_deliveries`): queued in the same transaction as the event,
  delivered by the scheduler with backoff (8 attempts), deduplicated per event. Channel
  destinations are encrypted; link-local/metadata destinations are refused.
- Container executor: `executor: "docker"` with `container.image`, network (`bridge`/`none`),
  memory and CPU limits, pull policy. Per-run `docker run --rm` as the agent's user with all
  capabilities dropped and `no-new-privileges`; stop on timeout/cancel; leftover containers
  removed after an agent crash. Agents advertise a `docker` capability; claims are filtered by
  it. Installer `--docker` option.
- Artifacts: files in `$TORQRUN_ARTIFACTS_DIR` are uploaded after each attempt (symlinks and
  directories refused), stored with SHA-256, listed and downloaded from the run page, size and
  count limits, retention.
- UI: Admin → Notifications, executor settings in the job form and job page, Artifacts card on
  runs, `docker` badge on agents.

### Added — M6: users & security
- Users with Argon2id passwords and roles (viewer / operator / admin). First-run setup page, or
  `TORQRUN_BOOTSTRAP_ADMIN_EMAIL`/`_PASSWORD`. Admin user management: role changes, disable,
  password reset; the last admin is protected.
- Session cookies (`HttpOnly`, `SameSite=Strict`, `Secure` in production), CSRF header check,
  login throttling, password change that signs out other sessions.
- Personal API tokens (`tqt_…`) with expiry and revocation; `tools/dev/api-token.sh`.
- Role checks on every user-facing endpoint; run events record the acting user.
- Encrypted job secrets (AES-256-GCM, `TORQRUN_SECRET_KEY`): write-only API, referenced from
  jobs as `ENV=secret-name`, injected only into the agent's assignment, masked in logs (plain
  and base64). Runs with a missing secret fail before starting.
- Audit log of every change and every sign-in attempt (`/api/v1/audit`).
- Prometheus metrics at `/metrics` (optional bearer token).
- Production mode is allowed now (requires a secret key; rejects development shortcuts).
- UI: sign-in and setup pages, user menu, Admin section (Users, Secrets, Audit log), Account
  page (password, API tokens), Secrets field on jobs, actions hidden from roles that can't
  use them.
- `make e2e-ui-isolated` runs the browser tests against a throwaway stack.

### Changed
- `examples/api/run_job.*` need `TORQRUN_TOKEN`. `/api/v1/system/info` reports
  `agents_online`, used by the readiness script.

### Added — M5: workflows
- Workflows: versioned YAML DAGs whose tasks run existing jobs; validation of cycles, unknown
  tasks and unknown jobs with clear messages (`/api/v1/workflows/validate`).
- Engine (`torqrun_core.workflow`, `torqrun_db.workflows`): dependency-ordered execution with
  parallel branches, trigger rules `all_success` / `all_done` / `one_failed`, cascading skips,
  advanced by the scheduler under a row lock.
- Cancel a workflow run; rerun only failed tasks (reusing successful ones) or run again.
- Schedules can target workflows (unique per slot, like job schedules).
- Task runs receive `TORQRUN_WORKFLOW_RUN_ID` and `TORQRUN_TASK`.
- UI: Workflows list, editor with live validation and graph preview, workflow page with
  runs/schedules/definition, live workflow-run graph with per-task links.

### Added — M4: schedules
- Cron (5-field, names, ranges, steps, `@daily`-style aliases) and interval schedules in any IANA
  time zone. Own dependency-free cron engine with defined DST behaviour (skipped times fire after
  the gap, repeated times once).
- Misfire policies (run once / run all with cap / skip) and overlap policy (allow / skip);
  pause, resume and edit take effect from now.
- Scheduler step fires due schedules; runs are unique per `(schedule_id, scheduled_for)` and
  schedules are claimed with `SKIP LOCKED`, so replicas and replays never duplicate a slot.
- API `/api/v1/schedules` (+ `/preview`, `/timezones`); UI **Schedules** page, schedule editor
  with presets, time-zone picker and live preview, Schedules tab on jobs.
- `tzdata` dependency so time zones work on hosts without system zone data.

### Fixed
- Closed dialogs no longer keep their form fields in the page; revoked agents no longer count
  toward the fleet size.

### Added — M3: remote agents
- Enrollment tokens (`/api/v1/enrollment-tokens`): single-use by default, expiring, hashed at
  rest, optional queue/tag presets; created from **Fleet → Connect agent**.
- One-line Linux installer served by the control plane (`/agent/install.sh`, wheels under
  `/agent/dist/`): private Python 3.12 via uv, `torqrun` system user, systemd service.
  Verified on a clean Ubuntu 22.04 container.
- Agent drain / resume / revoke (UI and API); `torq-agent drain` for self-drain. Revoking kills
  credentials immediately, marks running work lost and requeues unacknowledged work.
- Automatic credential rotation (7 days, 5-minute overlap) and `torq-agent rotate-credential`.
- Optional HTTPS front end (`make up-tls`, Caddy, Let's Encrypt or internal CA);
  `TORQRUN_AGENT_CA_FILE` / `--ca-file` for private CAs. Agents report capabilities.
- Operations guide: `docs/operations/remote-agents.md`.

### Fixed
- nginx forwarded the Host header without the port, so generated URLs lost it.

### Added — M2: reliability
- Retries with exponential backoff and jitter (`retry` in the job spec), manual **Retry**, and
  **Rerun** (new run linked via `rerun_of`, current or original job version).
- Cancellation in every state; the agent terminates the whole process group.
- New `torqrun-scheduler` service: lease reaper (`LOST`), retry promotion, requeue of
  unacknowledged work, idempotency-key cleanup. Safe to run several replicas.
- `interrupt_policy` (`fail` default / `retry`) decides whether lost runs are retried.
- Agent crash recovery (kills and reports orphaned jobs on restart) and a durable result spool
  that survives API outages and agent restarts.
- Per-job `max_concurrent`, per-queue limits and pause (`/api/v1/queues`, **Queues** page).
- `Idempotency-Key` header on run-creating endpoints; the UI sends one per click.
- UI: Cancel / Retry / Run again, attempt tabs with per-attempt logs, retry countdown,
  Reliability section in the job form, Queues page, run filters for retrying/lost/cancelled.
- Fault-injection end-to-end tests (agent SIGKILL, API restart, outage longer than the lease,
  duplicate requests, concurrency) and the same scenarios verified on the Docker stack.

### Fixed
- Lease reaper could mark healthy runs lost during a control-plane outage (now gated on API
  liveness).
- API shutdown waited up to 20 s for agents' long-poll requests; long-polls and live streams
  now end as soon as shutdown begins.
- A failed final log upload skipped reporting the result; unsent lines are spooled with it.
- Live log streams for a past attempt now end when that attempt ends.

### Changed — rename and UI redesign
- **Renamed the project from Fleetherd to Torqrun.** Packages are `torqrun-*`, the agent command
  is `torq-agent`, environment variables use the `TORQRUN_` prefix, the agent credential
  prefix is `tqa_`, Docker images are `torqrun/*`. Existing `.env` files are migrated by
  renaming keys; old Compose volumes (`fleetherd_*`) are not reused.
- Redesigned web UI: Inter/JetBrains Mono (self-hosted, CSP-safe), Lucide icons, layered dark
  theme with light mode, sidebar with live counts, ⌘K command palette, onboarding checklist,
  activity chart and p50/p95 durations on the overview, run-history strips per job, script
  templates and a line-numbered editor, run progress stepper, searchable terminal-style log
  viewer, and a **Fleet** page (replaces Agents) with per-agent CPU/memory/disk meters, active
  jobs and a "Connect agent" guide.

### Added
- Agents report host stats (OS, kernel, CPU count/load, memory, disk, uptime) on every
  heartbeat (optional field; older agents keep working). Migration `0003_agent_system_stats`.
- `GET /api/v1/agents/{id}`, `agent_id` filter on `GET /api/v1/runs`, `active_runs` on agents,
  `recent_runs` on jobs.

### Fixed
- Live log "follow" scrolled the whole page instead of the log panel.
- Responsive grids overflowed phone screens when content (long agent names) was wide.
- Box alignment: overview panels are one grid so side-by-side boxes share top and bottom
  edges; job form columns start on the same line; job stat values share a baseline;
  breadcrumbs line up with page titles. Covered by a browser test.

### Added — M1: first end-to-end slice
- Jobs with immutable versions (Python and shell; args, env, timeout, queue, priority) and
  runs with a durable state machine (`torqrun-core`) and full event history.
- Agent (`torqrun-agent`): enrollment with a development token, persistent identity,
  heartbeats, long-poll claiming with leases, ack-before-spawn, process-group timeouts,
  separate stdout/stderr capture, batched idempotent log upload, graceful shutdown.
- Agent protocol endpoints and wire contract (`torqrun-protocol`); claim uses
  `SELECT … FOR UPDATE SKIP LOCKED`; unacknowledged dispatches are requeued.
- Run history, logs, log download and live Server-Sent Events stream endpoints.
- Web UI: overview, jobs, job editor, job detail, runs, live run view with log viewer, agents.
- Compose: bundled agent, per-checkout dev enrollment token (`tools/dev/init-env.sh`).
- Tests: unit, integration (PostgreSQL), end-to-end with real API/agent processes, Playwright
  browser tests; CI runs the full Compose stack end to end.
- Examples: `examples/api/run_job.sh`, `examples/api/run_job.py`, sample jobs.

### Fixed
- Long-poll requests ignored client disconnects (replaced `BaseHTTPMiddleware`).
- Agent container: killed background job processes stayed as zombies because the agent was
  PID 1; the image now runs `tini` as init.
- nginx: security headers (CSP, nosniff, referrer policy) were dropped on the UI and asset
  responses because location-level `add_header` overrides server-level ones; now included
  in every location.

### Added — M0: foundation
- M0 foundation: uv workspace (`torqrun-api`, `torqrun-db`), FastAPI app with
  `/healthz`, `/readyz` and `/api/v1/system/info`, structured JSON logging with request IDs,
  Alembic baseline migration and `python -m torqrun_db.migrate` CLI.
- Web UI shell (React, TypeScript, Vite, Tailwind) with a live system-status page.
- Docker Compose stack (Postgres, migrate, api, web) bound to localhost; Makefile; CI
  (lint, mypy, unit + integration tests, web build, Compose smoke test).
- Architecture package (`docs/architecture/ARCHITECTURE.md`).
