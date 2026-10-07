# Torqrun

Self-hostable job and workflow orchestration for your own machines — laptop, Linux servers,
EC2 — through lightweight, outbound-only agents and a PostgreSQL-backed control plane.

> **Status: pre-alpha — milestones M1–M8 complete.** Python/shell jobs and YAML workflows on your own
> agents, on a schedule or on demand, with live output, retries, crash recovery, a container executor,
> artifacts, metrics, backups and benchmarks. Teams can add users and roles, a secrets manager, an audit log
> and alerts with a [Team or Enterprise plan](docs/editions.md).
> Everything listed below is covered by automated tests (304 Python, 33 web unit, 24 browser), a
> backup/restore drill and vulnerability scans that run on every push. It has **not** yet had long-term use
> by many people: read [production.md](docs/operations/production.md) and start with non-critical work.

## What works today

| Area | State |
|---|---|
| Docker Compose stack: Postgres, migrations, API, web UI, bundled agent | ✅ |
| Jobs: Python & shell scripts, args, env vars, timeout, queue, priority; immutable versions | ✅ |
| Runs: manual trigger, durable state machine with full event history, exit code, duration | ✅ |
| Agent: enrollment (dev token), heartbeats, long-poll dispatch with leases, ack-before-spawn, timeouts that kill the whole process tree | ✅ |
| Logs: stdout/stderr captured separately, live streaming (SSE), download, size cap | ✅ |
| Web UI: dashboard with activity chart, jobs with run-history strips, script editor with templates, live run view (progress stepper, searchable terminal log), **Fleet** with live host stats, ⌘K command palette, light/dark | ✅ |
| Fleet: agents report OS, CPU load, memory, disk and uptime; "Connect agent" guide in the UI | ✅ |
| Retries with exponential backoff + jitter; manual retry; rerun | ✅ M2 |
| Cancel in any state (queued, dispatched, running, waiting to retry) | ✅ M2 |
| Crash recovery: lease reaper (`LOST`), restarted agents clean up and report orphans, results spooled across API outages | ✅ M2 |
| Per-job and per-queue concurrency limits, queue pause; `Idempotency-Key` on run-creating requests | ✅ M2 |
| Remote agents: single-use enrollment tokens, one-line Linux installer (systemd), drain / revoke, credential rotation, HTTPS via Caddy | ✅ M3 |
| Schedules: cron and interval, time zones with defined DST behaviour, missed-run and overlap policies, pause/resume, never a duplicate run per slot ([guide](docs/guides/schedules.md)) | ✅ M4 |
| Workflows: YAML DAGs of jobs, parallel branches, trigger rules (`all_success`/`all_done`/`one_failed`), cancel, rerun-failed, live graph, schedulable ([guide](docs/guides/workflows.md)) | ✅ M5 |
| Sign-in, API tokens, CSRF protection, Prometheus `/metrics` ([guide](docs/operations/security.md)) | ✅ M6 |
| **Team/Enterprise:** multiple users and roles, secrets manager, audit log ([plans](docs/editions.md)) | 💼 paid |
| **Team/Enterprise:** notifications to Slack, Teams, email and signed webhooks ([guide](docs/guides/notifications.md)) | 💼 paid |
| Container executor (Docker, per-run container, no capabilities, network/memory/CPU limits) and run artifacts ([guide](docs/guides/containers-and-artifacts.md)) | ✅ M7 |
| Operations: [benchmarks](docs/operations/benchmarks.md) (≈50 runs/s per API replica, 135 runs/s with 6), [backup/restore](docs/operations/backup-restore.md) with an automated drill, [upgrade](docs/operations/upgrades.md) and agent-compatibility tests, vulnerability scans (pip-audit, pnpm audit, Trivy) in CI, [production guide](docs/operations/production.md) with alert rules | ✅ M8 |

Design and roadmap: [docs/architecture/ARCHITECTURE.md](docs/architecture/ARCHITECTURE.md).

## Editions

| **Community** (this repository) | **Team** | **Enterprise** |
|---|---|---|
| Free and open source (Apache-2.0), self-hosted. The full orchestration engine (jobs, schedules, workflows, retries, remote agents, containers, artifacts, metrics) with **one admin account** and no limits on jobs or agents. | Everything in Community, plus **multiple users and roles**, a **secrets manager**, an **audit log**, and **Slack/Teams/email/webhook alerts**. Self-hosted with a license key, or managed by us. Priority support. | Everything in Team, plus SSO (SAML/OIDC) and SCIM, per-project permissions (roadmap, built on request), SLA, on-prem or air-gapped deployment, custom terms. |
| [Get started ↓](#quick-start) | [Contact sales](mailto:anshulshrm12@gmail.com?subject=Torqrun%20Team%20plan) | [Talk to us](mailto:anshulshrm12@gmail.com?subject=Torqrun%20Enterprise%20plan) |

Full comparison: [docs/editions.md](docs/editions.md). Contact: **anshulshrm12@gmail.com**.

## Quick start

Requirements: **Docker Engine with Compose v2**, **git**, **make**.

```bash
git clone https://github.com/anshulsharma7/torqrun.git && cd torqrun
make up        # creates .env, builds images, starts everything, waits for a connected agent
```

Open <http://127.0.0.1:8080> and create the first administrator account (the page only exists
until one does). The overview then walks you through three steps: an agent is already
connected (the bundled `compose-agent`), so choose **New job**, pick a template, **Create job**,
then **Run now** — the output streams in live.

### Where do agents live?

An **agent** is a small process on each machine that should run jobs. It connects *out* to the
control plane (no inbound ports), claims work, runs scripts and streams their output back.

* `make up` starts one for you: the **`agent`** container, shown as `compose-agent` under **Fleet**.
  Jobs it runs execute inside that container.
* To run jobs on another machine, open **Fleet → Connect agent**, create a token and run the
  one-line installer it shows on that server. See
  [docs/operations/remote-agents.md](docs/operations/remote-agents.md) (HTTPS, EC2, lifecycle).

From the command line instead:

```bash
export TORQRUN_TOKEN=tqt_…   # create one under Account → API tokens
examples/api/run_job.sh examples/python-jobs/disk_report.py     # needs curl + jq
```

`make help` lists all commands; `make down` stops; `make clean` also deletes all data.
API reference (OpenAPI): <http://127.0.0.1:8080/api/docs>. Ports bind to `127.0.0.1` only.

### Run an agent on your own machine (from source)

Same as the UI's *Connect agent → From source* tab (needs [uv](https://docs.astral.sh/uv/)):

```bash
TORQRUN_AGENT_SERVER_URL=http://127.0.0.1:8000 \
TORQRUN_AGENT_ENROLLMENT_TOKEN=$(grep ^TORQRUN_DEV_ENROLLMENT_TOKEN= .env | cut -d= -f2) \
TORQRUN_AGENT_NAME=my-laptop \
uv run torq-agent start
```

Jobs then run as your user — **this is not a sandbox**; only run scripts you trust.

## Development

See [docs/development/getting-started.md](docs/development/getting-started.md) for the
toolchain (uv, pnpm), running tests and checks, and the repository layout.

## Contributing & security

- [CONTRIBUTING.md](CONTRIBUTING.md) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).

## License

[Apache-2.0](LICENSE)
