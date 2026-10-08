# Launch announcement (drafts)

## Blog / LinkedIn post

**Introducing Torqrun: run your jobs on your own machines, orchestrated from one place**

Most teams have scripts that need to run somewhere: backups, reports, data pulls, cleanups.
They end up in crontabs on servers nobody remembers, with no history, no retries and no idea
whether last night's run worked.

Torqrun is an open-source job orchestrator built for exactly that:

- **Agents on your machines** (laptops, Linux servers, cloud VMs) that connect *out* over
  HTTPS. No inbound ports, no SSH keys.
- **Schedules** (cron and intervals, time-zone and DST aware), **workflows** (YAML DAGs) and
  **retries, timeouts and concurrency limits** built in.
- **Live logs**, artifacts and a **Docker container executor**.
- **Self-hosted in one command**: `git clone … && make up`.

The Community Edition is free and Apache-2.0 licensed, with no limits on jobs, runs or agents.
When a team relies on it, the **Team** plan adds multiple users with roles, a secrets
manager, an audit log and Slack/Teams/email/webhook alerts. It can be self-hosted (upgrade in
place with one command, keeping all your data) or managed by us. **Enterprise** adds SSO, an
SLA and help with on-prem or air-gapped deployments.

⭐ GitHub: https://github.com/anshulsharma7/torqrun
🌐 Website: https://torqrun.thelasthumanteam.com
✉️ Plans and questions: anshulshrm12@gmail.com

It's pre-1.0, and I'd love feedback: try it, break it, open issues.

## Short post (X / Mastodon / Bluesky)

I just open-sourced Torqrun, a self-hosted job orchestrator: schedules, DAG workflows,
retries and live logs, with outbound-only agents on your own machines. `make up` and you're
running. Apache-2.0 core; Team and Enterprise plans add users, secrets, audit and alerts.
https://github.com/anshulsharma7/torqrun

## Show HN

**Show HN: Torqrun – open-source orchestrator for scripts on your own machines**

I built Torqrun because cron on a dozen servers doesn't tell you when things fail. It's a
Postgres-backed control plane plus lightweight agents that pull work over HTTPS. You get cron
and DAG workflows, retries with leases and crash recovery, live logs and a Docker executor.
Paid plans add RBAC, secrets, an audit log and Slack/Teams/webhook alerts.

Engineering details I care about:
- The run state machine survives agent and control-plane crashes, with lease fencing and
  ack-before-spawn.
- A schedule slot never runs twice.
- Benchmarks are in the repo (~50 complete runs/s per API process; it scales horizontally).
- A backup/restore drill runs in CI.

The core is Apache-2.0; paid plans (open core, like Dagster) add team features, hosting and
support, and upgrading keeps all your data. Happy to answer questions.
