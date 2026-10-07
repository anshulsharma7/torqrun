# Users, access control, secrets and auditing

> **Editions.** The Community Edition has one admin account with sign-in, API tokens, CSRF
> protection and metrics. **Multiple users and roles, the secrets manager and the audit log are
> part of Torqrun Team and Enterprise** ([plans](../editions.md)); their sections below are
> marked *(Team/Enterprise)*.

This page covers sign-in, roles, API tokens, job secrets, the audit log and metrics, plus
what to check before exposing Torqrun beyond your own machine.

## First sign-in

On a fresh install, the web UI shows **Welcome to Torqrun** and asks you to create the first
administrator. That page works only while no user exists, so do it right after `make up`.

For unattended installs, set both of these instead. The admin is created on startup only if
the database has no users:

```bash
TORQRUN_BOOTSTRAP_ADMIN_EMAIL=ops@example.com
TORQRUN_BOOTSTRAP_ADMIN_PASSWORD='a long random password'
```

Remove the password from the environment after the first start and change it under
**Account**.

## Roles (Team/Enterprise)

| Role | Can |
|---|---|
| **viewer** | See everything: jobs, runs, logs, workflows, schedules, queues and the fleet. Secret values stay hidden. |
| **operator** | Everything a viewer can, plus create, edit, run, cancel and retry jobs and workflows; manage schedules and queues. |
| **admin** | Everything an operator can, plus manage users, secrets, enrollment tokens and agents (drain, revoke), and read the audit log. |

The API enforces roles on every endpoint, and the UI hides actions you can't use. Exempt
endpoints:
- `/healthz`, `/readyz` and `/api/v1/system/info`: version, database state and the number of
  connected agents, with no names.
- The agent protocol (`/api/v1/agent/*`), which uses agent credentials.
- The installer under `/agent/`.

Admins manage people under **Admin → Users**. Changing someone's role or password, or
disabling them, signs them out everywhere. The last active admin can't be demoted, disabled
or deleted.

## Sessions and CSRF

- Browser sessions use an `HttpOnly`, `SameSite=Strict` cookie that expires after
  `TORQRUN_SESSION_TTL_HOURS` (12 by default). The cookie is `Secure` in production; override
  with `TORQRUN_COOKIE_SECURE`.
- Sessions are stored hashed. Signing out deletes the session, and the scheduler purges
  expired ones.
- Requests authenticated by cookie that change anything must send `X-Torqrun-Client`. The web
  UI always does. A hostile site can't add custom headers to a cross-site request without a
  CORS preflight, and this API never grants one.
- Failed logins are throttled per client IP and email: `TORQRUN_LOGIN_MAX_ATTEMPTS` (10) per
  `TORQRUN_LOGIN_WINDOW_SECONDS` (300). The limiter is in memory, per API replica.
- An unknown email and a wrong password get the same answer.
- Passwords are hashed with Argon2id and must be at least 10 characters.

## API tokens (scripts, CI)

Create a token under **Account → API tokens**. It's shown once. Use it as a bearer token:

```bash
export TORQRUN_TOKEN=tqt_…
curl -H "Authorization: Bearer $TORQRUN_TOKEN" http://127.0.0.1:8080/api/v1/jobs
examples/api/run_job.sh examples/python-jobs/disk_report.py
```

- A token has its owner's role and is stored only as a SHA-256 hash.
- It can expire, and it stops working when revoked or when its owner is disabled or deleted.
- Token requests don't need the CSRF header.
- `tools/dev/api-token.sh <url> <email>` mints a one-day token from a password (CI uses it).

## Secrets (Team/Enterprise)

Jobs often need passwords or API keys. Don't put those in a job's environment variables:
anyone who can view the job can read those. Use secrets instead.

1. An admin adds a secret under **Admin → Secrets**, for example `prod-db-password`. The value
   is write-only: nobody can read it back through the UI or API.
2. In the job's **Secrets** field, map an environment variable to it:
   `DB_PASSWORD=prod-db-password`.
3. When an agent claims a run, the control plane decrypts the value and sends it in that run's
   assignment over the agent's TLS connection. The script sees `$DB_PASSWORD`.
4. The agent replaces the value, and its base64 form, with `***` in output and error summaries
   before anything is sent back.
5. If a referenced secret doesn't exist, or can't be decrypted, the run fails without starting
   the script, and the run's log says which secret is missing.

**Encryption.** Values are encrypted with AES-256-GCM. The key is derived from
`TORQRUN_SECRET_KEY` (at least 32 characters; `tools/dev/init-env.sh` generates one). The
secret's name is bound as associated data, so a ciphertext can't be moved onto another secret.

**Back up the key separately from the database.** Without it, stored secrets can't be
recovered. If the API starts with a different key, the old secrets show as *not readable*,
and runs that use them fail until each value is set again.

**Limits, stated plainly:**
- Masking is a safety net. A script that transforms a secret (reverses it, encrypts it, prints
  it one character at a time) can still reveal it.
- An agent, and the jobs it runs, can read every secret its jobs reference. Give agents only
  the queues they need.
- Secrets are injected as environment variables. Other processes running as the same OS user
  on that machine may be able to read them.

## Audit log (Team/Enterprise)

Every request that changes something (`POST`, `PUT`, `PATCH` or `DELETE` under `/api/`)
records:
- who made it: user email, or `anonymous`
- the route
- the target (job, run, user…) and its ID
- the HTTP result
- the client IP and the request ID

Failed sign-ins are recorded too, with the email that was tried. Request bodies are never
stored, because they can contain scripts and secrets. Agent protocol traffic isn't recorded,
except enrollment; per-run history lives in each run's events.

Admins read the log under **Admin → Audit log** or at `GET /api/v1/audit?actor=…&before=…`.

## Metrics

`GET /metrics` serves Prometheus metrics:

| Metric | Labels | What it counts |
|---|---|---|
| `torqrun_http_requests_total` | method, route template, status | Requests |
| `torqrun_http_request_duration_seconds` | | Request latency; long-polls and SSE are excluded |
| `torqrun_runs` | status | Runs by status |
| `torqrun_queue_depth` | queue | Queued runs |
| `torqrun_agents` | state: `online`, `offline`, `draining`, `revoked` | Agents |
| `torqrun_schedules_enabled` | | Enabled schedules |

The run, queue, agent and schedule gauges are read from the database at scrape time, so every
API replica reports the same totals. Aggregate them with `max`, not `sum`.

The bundled nginx doesn't proxy `/metrics`. Scrape the API directly on its internal port, or
set `TORQRUN_METRICS_TOKEN` and send `Authorization: Bearer <token>`.

## Before exposing Torqrun to a network

- [ ] `TORQRUN_ENVIRONMENT=production`. This requires `TORQRUN_SECRET_KEY`, rejects the
      development enrollment token, and turns on secure cookies.
- [ ] HTTPS in front: `make up-tls` (Caddy) or your own proxy. Set `TORQRUN_PUBLIC_URL=https://…`.
- [ ] A strong, unique `POSTGRES_PASSWORD`; the database stays on a private network.
- [ ] `TORQRUN_SECRET_KEY` backed up somewhere other than the database backups.
- [ ] The first admin created, and the bootstrap password removed from the environment.
- [ ] Agents enrolled with single-use tokens, running as an unprivileged user (the installer
      does this).
- [ ] `/metrics` either unreachable from outside or protected by `TORQRUN_METRICS_TOKEN`.

Nothing here opens ports or changes firewall rules for you. All published ports bind to
`127.0.0.1` until you change that yourself.
