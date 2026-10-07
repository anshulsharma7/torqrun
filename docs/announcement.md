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
- **Live logs**, artifacts, a **Docker container executor**, encrypted **secrets**, **roles**
  and an **audit log**.
- **Notifications** to Slack, Teams, email or webhooks when something fails or an agent goes
  offline.
- **Self-hosted in one command**: `git clone … && make up`.

The Community Edition is free and Apache-2.0 licensed, with every feature and no limits on
jobs, agents or users. For teams who'd rather not run it themselves, the **Team** plan gives
you a managed control plane and priority support. **Enterprise** adds SSO, an SLA and help
with on-prem or air-gapped deployments.

⭐ GitHub: https://github.com/anshulsharma7/torqrun
🌐 Website: https://anshulsharma7.github.io/torqrun/
✉️ Plans and questions: anshulshrm12@gmail.com

It's pre-1.0, and I'd love feedback: try it, break it, open issues.

## Short post (X / Mastodon / Bluesky)

I just open-sourced Torqrun, a self-hosted job orchestrator: schedules, DAG workflows,
retries and live logs, with outbound-only agents on your own machines. `make up` and you're
running. Apache-2.0, free forever; managed plans available.
https://github.com/anshulsharma7/torqrun

## Show HN

**Show HN: Torqrun – open-source orchestrator for scripts on your own machines**

I built Torqrun because cron on a dozen servers doesn't tell you when things fail. It's a
Postgres-backed control plane plus lightweight agents that pull work over HTTPS. You get cron
and DAG workflows, retries with leases and crash recovery, live logs, a Docker executor,
secrets, RBAC, an audit log and Slack/Teams/webhook alerts.

Engineering details I care about:
- The run state machine survives agent and control-plane crashes, with lease fencing and
  ack-before-spawn.
- A schedule slot never runs twice.
- Benchmarks are in the repo (~50 complete runs/s per API process; it scales horizontally).
- A backup/restore drill runs in CI.

Apache-2.0; paid plans are hosting and support. Happy to answer questions.
