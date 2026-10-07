# Benchmarks

Measured on 2026-10-07 with `tools/bench/run-bench.sh` against a throwaway Docker Compose stack.
Raw results are in [`benchmarks/`](benchmarks/). These are numbers from **one developer
laptop**, not a tuned server. Treat them as an order of magnitude and a regression baseline,
not as a capacity guarantee for your hardware.

**Machine:** Intel Core Ultra 7 255H (16 threads), 15 GB RAM, Linux 7.0, Docker 29.1.
PostgreSQL 16 in a container with default settings, except `max_connections=200`.
Everything ran on the same machine: database, API, scheduler, the load generator and up to
40 simulated agents.

## What is measured

- **Simulated agents** speak the real agent protocol over HTTP, with real leases:
  claim → ack → started → logs (5 lines) → complete. They finish each job instantly, so these
  scenarios measure the **control plane** (API + PostgreSQL), not job execution.
- **Trigger:** 20 clients create runs as fast as they can (`POST /api/v1/jobs/{id}/runs`).
- **Drain:** a backlog of 3,000–6,000 queued runs is emptied by the simulated agents. Result
  is complete run lifecycles per second.
- **Steady:** runs arrive at a fixed rate while agents long-poll. Measures dispatch latency
  (run created → claimed by an agent) and end-to-end latency (created → completed). Meanwhile
  5 "dashboards" poll `GET /api/v1/runs?limit=50` every 0.5 s.
- **Real agent:** 100 trivial `true` jobs on the bundled agent (4 slots), as real processes.

## Results

| API replicas | Trigger | Drain (end to end) | Steady load | Dispatch p50 / p99 | End to end p50 / p99 | Dashboard read p99 | Errors |
|---|---|---|---|---|---|---|---|
| 1 | 270 runs/s | **53 runs/s** | 20 runs/s | 70 / 264 ms | 184 / 514 ms | 131 ms | 0 |
| 3 | 432 runs/s | **109 runs/s** | 20 runs/s | 79 / 337 ms | 216 / 516 ms | 86 ms | 0 |
| 6 | 540 runs/s | **135 runs/s** | 40 runs/s | 81 / 274 ms | 213 / 437 ms | 86 ms | 0 |

The 1-replica stack was benchmarked on the API's own port; 3 and 6 replicas went through the
bundled nginx (`deploy/compose/docker-compose.scale.yml`). Agent protocol calls (ack, started,
logs, complete) took 20–35 ms at p50 and under 150 ms at p99 in every run.

**Real agent** (one container, 4 slots): 100/100 `true` jobs in about 2.7 s, **37 runs/s**.
Process start → exit took p50 18 ms, p99 62 ms.

To put these numbers in perspective: 53 runs/s is about 4.5 million runs a day on a single API
process. Typical cron/ETL workloads need well under 1 run/s.

## Where the limits are

**One API replica is CPU-bound.** During the drain the API process sat at ~100% of one core
(it's a single async Python process) while PostgreSQL used about one core. Adding replicas
helps until PostgreSQL becomes the limit. At 6 replicas the replicas each ran at 70–80% and
PostgreSQL at ~3.5 cores, and the gain from 3 to 6 replicas was only +24%.

**Found and fixed by this benchmark: dispatch was serialized.** The first runs with 3 replicas
were *slower* than with 1 (41 vs 53 runs/s; see
[`2026-10-07-api3-before-claim-fix.json`](benchmarks/2026-10-07-api3-before-claim-fix.json)).
Every claim held a per-queue advisory lock for its whole transaction, so all agents on a
queue waited for each other, and more replicas only added contention.

The lock is needed only to enforce concurrency limits exactly; `SKIP LOCKED` already prevents
double dispatch. The fix:
- Queue locks are now taken only for queues that have a concurrency limit.
- Per-job limits use a non-blocking `pg_try_advisory_xact_lock`.
- Claimers lock only as many rows as they need.

A stress test now checks the limits under a racing fleet (12 agents, a capped queue,
three limited jobs). A mutation check confirmed it catches a broken limit.

**Found and fixed: connection exhaustion at 6 replicas.** Six replicas × (10 pool + 10
overflow) connections exceed PostgreSQL's default `max_connections=100`. That produced
`sorry, too many clients already` and HTTP 500s. Now:
- Compose sets `max_connections` (`POSTGRES_MAX_CONNECTIONS`, default 200).
- Database connection failures return **503 with `Retry-After`**, which agents retry.
- The connection budget is documented in [production.md](production.md#database-connections).

**One measurement artefact:** an early steady-state run showed a single 30.9 s dispatch. The
benchmark's simulated agent had dropped an `ack` after a transport error without retrying.
The control plane behaved as designed: after 30 s it put the unacknowledged dispatch back in
the queue. The real agent retries transport errors immediately; the simulator now does too.
3,000 further steady-state runs had a maximum dispatch of 302 ms and no run dispatched twice.

## Reproduce

```bash
make bench                                  # 1 API replica, defaults below
API_REPLICAS=3 tools/bench/run-bench.sh --json /tmp/bench.json
tools/bench/run-bench.sh --runs 6000 --agents 40 --rate 40 --duration 30
```

Defaults: 2,000 runs, 20 clients, 20 agents × 4 slots, 5 log lines per run, 20 runs/s for
30 s, 5 dashboard readers, 100 real runs. The script starts and removes its own stack
(`torqrun-bench`, ports 38000/38080) and never touches your normal one.
