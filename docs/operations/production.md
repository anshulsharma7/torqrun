# Running Torqrun in production

Torqrun is **pre-1.0**. What's described here has been tested: the test suites, a backup and
restore drill, load benchmarks and vulnerability scans, all run on every push. It hasn't yet
been run by many people under real long-term load. Start with non-critical workloads, keep
backups, and read the [CHANGELOG](../../CHANGELOG.md) before upgrading.

## Architecture in one paragraph

- **PostgreSQL** holds all state.
- One or more **API** replicas serve the UI, the REST API and the agent protocol. They're
  stateless apart from the artifact directory.
- The **scheduler** handles leases, retries, schedules, workflows and notifications. Any
  number of scheduler replicas is safe.
- **nginx** (`web`) serves the UI and proxies the API.
- **Agents** run on your machines and connect *out* to the API over HTTPS.

## Deployment checklist

- [ ] **Host:** Docker Engine with Compose v2. For a small team, 2 vCPU and 4 GB of RAM is
      plenty; see the sizing section below.
- [ ] **`.env`:**
  - `TORQRUN_ENVIRONMENT=production`. This requires `TORQRUN_SECRET_KEY`, rejects the
    development enrollment token, and turns on secure cookies.
  - A strong `POSTGRES_PASSWORD`.
  - Remove `TORQRUN_DEV_ENROLLMENT_TOKEN`.
  - Set `TORQRUN_PUBLIC_URL=https://torqrun.example.com`.
- [ ] **HTTPS:** `make up-tls` with `TORQRUN_DOMAIN`. Caddy gets a Let's Encrypt certificate;
      ports 80 and 443 must be reachable. Or put your own TLS proxy in front of `web:8080`.
      Nothing else needs to be exposed. Torqrun doesn't change firewall rules for you.
- [ ] **First admin:** create it at first start, or set
      `TORQRUN_BOOTSTRAP_ADMIN_EMAIL`/`_PASSWORD` and remove the password afterwards.
- [ ] **Backups:** `make backup` from cron, copied off the host. `TORQRUN_SECRET_KEY` stored
      separately. Run `make backup-drill` once on your setup
      ([backup-restore.md](backup-restore.md)).
- [ ] **Agents:** enroll each with a single-use token from **Fleet → Connect agent**. They run
      as the unprivileged `torqrun` user. Only use `--docker` on hosts dedicated to Torqrun
      ([containers](../guides/containers-and-artifacts.md)).
- [ ] **Monitoring:** scrape `/metrics` (internal port, or `TORQRUN_METRICS_TOKEN`) and load
      the alert rules below.
- [ ] **Notifications** (Team/Enterprise): at least one channel for `run.failed` and
      `agent.offline` ([guide](../guides/notifications.md)).
- [ ] **Security:** read [security.md](security.md): sign-in and API tokens; with Team or
      Enterprise also roles, secrets and the audit log.

## Sizing and scaling

From the [benchmarks](benchmarks.md) on a laptop:
- One API process handles about **50 complete run lifecycles per second**, with dispatch
  latency under 300 ms at p99.
- Three replicas handle about 110, six about 135. Past that, PostgreSQL is the limit.

Typical scheduled-job workloads need a fraction of one replica.

Scale the API with `deploy/compose/docker-compose.scale.yml`:

```bash
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/docker-compose.scale.yml \
  --project-directory . up -d --scale api=3
```

On a single host, all replicas share the `artifacts` volume. Across hosts, that directory
must be shared storage. Login throttling is per replica.

Log volume is the main driver of database size. Each attempt keeps up to
`TORQRUN_AGENT_MAX_LOG_BYTES` (10 MB by default) of output in PostgreSQL. Keep chatty jobs'
output in artifacts instead.

### Database connections

Each API replica opens up to `TORQRUN_DB_POOL_SIZE + TORQRUN_DB_MAX_OVERFLOW` connections
(10 + 10). The scheduler opens 4, and you need some for migrations and admin. Keep:

```
api_replicas × (pool + overflow) + 4 + 10  ≤  POSTGRES_MAX_CONNECTIONS (default 200 in Compose)
```

If PostgreSQL refuses connections anyway, the API answers **503 with `Retry-After`**, which
agents retry, rather than failing requests outright.

## Monitoring

Prometheus metrics: [security.md → Metrics](security.md#metrics). Example alert rules are in
[`deploy/monitoring/prometheus-rules.yml`](../../deploy/monitoring/prometheus-rules.yml):

| Alert | Meaning |
|---|---|
| `TorqrunApiDown` | Prometheus can't scrape any API replica. |
| `TorqrunNoAgentsOnline` | No agent is connected, so nothing runs. |
| `TorqrunQueueBacklog` | Runs have been waiting for more than 10 minutes and the backlog keeps growing: not enough agent slots, a paused queue, or no agent for that queue or executor. |
| `TorqrunServerErrors` | More than 1% of requests are failing with 5xx. |
| `TorqrunRunsLost` | Runs are being declared lost (agents dying mid-run). |

Also watch PostgreSQL itself: disk space, connections, replication if you use it. And watch
the scheduler container's health; its healthcheck fails if its loop stalls.

Logs are JSON lines on stdout (`TORQRUN_LOG_FORMAT=json`), with a `request_id` that is also
returned in the `X-Request-ID` header (and, with Team or Enterprise, recorded in the audit
log).

## Known limitations

- Artifacts and logs are stored locally. There is no object storage (S3) backend yet.
- There's no SSO/OIDC and no MFA: passwords and API tokens only.
- Login throttling is in memory, per replica.
- Notifications are at least once (receivers should de-duplicate) and have no digests or
  throttling.
- The process executor isn't a sandbox. Use the container executor for isolation.
- There is no OpenTelemetry tracing yet. The request ID ties logs, audit events and responses
  together.
