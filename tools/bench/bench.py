"""Torqrun load test: drives a running control plane through its public API and agent protocol.

Scenarios (each prints a result table; ``--json`` writes everything to a file):

1. **trigger**: create runs as fast as ``--concurrency`` clients can (API write latency).
2. **drain**: ``--agents`` simulated agents (real HTTP, real leases, ack/started/logs/complete)
   empty that backlog: end-to-end runs/second of the control plane.
3. **steady**: runs arrive at ``--rate`` per second while agents are idle-polling: dispatch
   latency (trigger -> claimed) and completion latency, plus UI read latency under load.
4. **real**: ``--real-runs`` trivial jobs on the real bundled agent (actual processes).

Simulated agents spend no time "running" jobs, so scenarios 2-3 measure the control plane
(API + PostgreSQL), not job execution. Use a throwaway stack: tools/bench/run-bench.sh.

    uv run python tools/bench/bench.py --base-url http://127.0.0.1:28000 --token tqt_... \\
        --enrollment-token $(grep ^TORQRUN_DEV_ENROLLMENT_TOKEN= .env | cut -d= -f2)
"""

import argparse
import asyncio
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}


def pct(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = max(0, min(len(s) - 1, round(p / 100 * (len(s) - 1))))
    return s[k]


def summary(name: str, values_ms: list[float]) -> dict[str, Any]:
    return {
        "metric": name,
        "n": len(values_ms),
        "p50_ms": round(pct(values_ms, 50), 1),
        "p95_ms": round(pct(values_ms, 95), 1),
        "p99_ms": round(pct(values_ms, 99), 1),
        "max_ms": round(max(values_ms), 1) if values_ms else None,
        "mean_ms": round(statistics.fmean(values_ms), 1) if values_ms else None,
    }


def now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Bench:
    base: str
    token: str
    enrollment_token: str
    tag: str = field(default_factory=lambda: uuid.uuid4().hex[:6])
    errors: list[str] = field(default_factory=list)
    retries: list[str] = field(default_factory=list)

    def user(self, **kw: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=60,
            limits=httpx.Limits(max_connections=kw.get("conns", 50)),
        )

    async def create_job(self, http: httpx.AsyncClient, queue: str, log_lines: int = 0) -> str:
        script = "true" if not log_lines else f"for i in $(seq {log_lines}); do echo line $i; done"
        r = await http.post(
            "/api/v1/jobs",
            json={
                "name": f"bench-{queue}-{self.tag}",
                "spec": {"runtime": "shell", "script": script, "queue": queue},
            },
        )
        r.raise_for_status()
        return str(r.json()["id"])

    async def trigger_many(
        self, job_id: str, n: int, concurrency: int
    ) -> tuple[dict[str, float], list[float], float]:
        """Create n runs; returns run_id -> monotonic trigger time, per-request latencies, wall s."""
        triggered: dict[str, float] = {}
        lat: list[float] = []
        sem = asyncio.Semaphore(concurrency)
        async with self.user(conns=concurrency) as http:

            async def one() -> None:
                async with sem:
                    t0 = time.monotonic()
                    r = await http.post(f"/api/v1/jobs/{job_id}/runs")
                    t1 = time.monotonic()
                    if r.status_code != 201:
                        self.errors.append(f"trigger HTTP {r.status_code}")
                        return
                    lat.append((t1 - t0) * 1000)
                    triggered[r.json()["id"]] = t0

            start = time.monotonic()
            await asyncio.gather(*(one() for _ in range(n)))
            return triggered, lat, time.monotonic() - start


@dataclass
class SimAgent:
    """A protocol-complete agent that finishes every job instantly."""

    bench: Bench
    name: str
    queue: str
    slots: int
    log_lines: int
    claimed: dict[str, float] = field(default_factory=dict)  # run_id -> monotonic claim time
    finished: dict[str, float] = field(default_factory=dict)
    request_ms: dict[str, list[float]] = field(default_factory=dict)
    http: httpx.AsyncClient | None = None

    async def enroll(self) -> None:
        async with httpx.AsyncClient(base_url=self.bench.base, timeout=30) as h:
            r = await h.post(
                "/api/v1/agent/enroll",
                json={
                    "enrollment_token": self.bench.enrollment_token,
                    "name": self.name,
                    "hostname": "bench",
                    "os": "linux",
                    "arch": "x86_64",
                    "agent_version": "bench",
                    "max_slots": self.slots,
                    "queues": [self.queue],
                },
            )
            r.raise_for_status()
            cred = r.json()["credential"]
        self.http = httpx.AsyncClient(
            base_url=self.bench.base,
            headers={"Authorization": f"Bearer {cred}"},
            timeout=60,
            limits=httpx.Limits(max_connections=self.slots + 2),
        )

    async def _post(self, kind: str, path: str, body: dict[str, Any]) -> httpx.Response:
        """Like the real agent: transport errors (e.g. a keep-alive connection the server just
        closed) are retried; each retry is counted so it shows up in the report."""
        assert self.http is not None
        for attempt in range(3):
            t0 = time.monotonic()
            try:
                r = await self.http.post(path, json=body)
                if r.status_code in (502, 503, 504) and attempt < 2:
                    self.bench.retries.append(f"{kind}: HTTP {r.status_code}")
                    await asyncio.sleep(0.5)
                    continue
                break
            except httpx.TransportError as exc:
                self.bench.retries.append(f"{kind}: {type(exc).__name__}")
                if attempt == 2:
                    self.bench.errors.append(f"{kind}: {type(exc).__name__} (gave up)")
                    raise
                await asyncio.sleep(0.2)
        self.request_ms.setdefault(kind, []).append((time.monotonic() - t0) * 1000)
        if r.status_code >= 300:
            self.bench.errors.append(f"{kind} HTTP {r.status_code}: {r.text[:120]}")
        return r

    async def _run(self, a: dict[str, Any]) -> None:
        base = f"/api/v1/agent/attempts/{a['attempt_id']}"
        lease = {"lease_token": a["lease_token"]}
        await self._post("ack", f"{base}/ack", lease)
        await self._post("started", f"{base}/started", {**lease, "pid": 1, "started_at": now()})
        if self.log_lines:
            chunks = [
                {"seq": i, "stream": "stdout", "ts": now(), "data": f"line {i}\n"}
                for i in range(self.log_lines)
            ]
            await self._post("logs", f"{base}/logs", {**lease, "chunks": chunks})
        last = self.log_lines - 1 if self.log_lines else None
        await self._post(
            "complete",
            f"{base}/complete",
            {
                **lease,
                "outcome": "succeeded",
                "exit_code": 0,
                "finished_at": now(),
                "last_log_seq": last,
            },
        )
        self.finished[a["run_id"]] = time.monotonic()

    async def loop(self, stop: asyncio.Event) -> None:
        running: set[asyncio.Task[None]] = set()
        while not stop.is_set():
            free = self.slots - len(running)
            if free <= 0:
                await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
                running = {t for t in running if not t.done()}
                continue
            r = await self._post(
                "claim", "/api/v1/agent/claim", {"free_slots": free, "wait_seconds": 1}
            )
            if r.status_code != 200:
                await asyncio.sleep(0.5)
                continue
            t = time.monotonic()
            for a in r.json()["assignments"]:
                self.claimed[a["run_id"]] = t
                task = asyncio.create_task(self._run(a))
                running.add(task)
            running = {t for t in running if not t.done()}
        if running:
            await asyncio.gather(*running)

    async def close(self) -> None:
        if self.http:
            await self.http.aclose()


async def wait_until(pred: Any, timeout: float, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


async def run_all(args: argparse.Namespace) -> dict[str, Any]:
    bench = Bench(args.base_url.rstrip("/"), args.token, args.enrollment_token)
    results: dict[str, Any] = {
        "config": {k: v for k, v in vars(args).items() if k not in ("token", "enrollment_token")}
    }
    queue = f"bench-{bench.tag}"

    async with bench.user() as http:
        job_id = await bench.create_job(http, queue, args.log_lines)

    # 1. trigger throughput (backlog for scenario 2)
    print(f"[1/4] triggering {args.runs} runs with {args.concurrency} clients…", flush=True)
    triggered, lat, wall = await bench.trigger_many(job_id, args.runs, args.concurrency)
    results["trigger"] = {
        **summary("POST /jobs/{id}/runs", lat),
        "runs": len(triggered),
        "wall_s": round(wall, 2),
        "runs_per_s": round(len(triggered) / wall, 1),
    }

    # 2. drain the backlog with simulated agents
    agents = [
        SimAgent(bench, f"bench-{bench.tag}-{i}", queue, args.slots, args.log_lines)
        for i in range(args.agents)
    ]
    await asyncio.gather(*(a.enroll() for a in agents))
    print(
        f"[2/4] {args.agents} simulated agents x {args.slots} slots draining the backlog…",
        flush=True,
    )
    stop = asyncio.Event()
    start = time.monotonic()
    loops = [asyncio.create_task(a.loop(stop)) for a in agents]
    done = lambda: sum(len(a.finished) for a in agents)  # noqa: E731
    ok = await wait_until(lambda: done() >= len(triggered), timeout=args.timeout)
    drain_wall = time.monotonic() - start
    results["drain"] = {
        "runs": done(),
        "complete": ok,
        "wall_s": round(drain_wall, 2),
        "runs_per_s": round(done() / drain_wall, 1),
        "agents": args.agents,
        "slots_per_agent": args.slots,
        "log_lines_per_run": args.log_lines,
    }
    for a in agents:
        a.claimed.clear()
        a.finished.clear()
        a.request_ms.clear()

    # 3. steady state: arrivals at a fixed rate while agents poll; measure latencies
    total = int(args.rate * args.duration)
    print(f"[3/4] steady load: {args.rate} runs/s for {args.duration}s ({total} runs)…", flush=True)
    trigger_at: dict[str, float] = {}
    trig_lat: list[float] = []
    read_lat: list[float] = []
    async with bench.user(conns=100) as http:

        async def one() -> None:
            t0 = time.monotonic()
            r = await http.post(f"/api/v1/jobs/{job_id}/runs")
            if r.status_code == 201:
                trigger_at[r.json()["id"]] = t0
                trig_lat.append((time.monotonic() - t0) * 1000)
            else:
                bench.errors.append(f"trigger HTTP {r.status_code}")

        async def reader(stop_reading: asyncio.Event) -> None:  # a dashboard polling the run list
            while not stop_reading.is_set():
                t0 = time.monotonic()
                r = await http.get("/api/v1/runs", params={"limit": 50})
                if r.status_code == 200:
                    read_lat.append((time.monotonic() - t0) * 1000)
                await asyncio.sleep(0.5)

        stop_reading = asyncio.Event()
        readers = [asyncio.create_task(reader(stop_reading)) for _ in range(args.readers)]
        tasks = []
        t_start = time.monotonic()
        for i in range(total):
            await asyncio.sleep(max(0.0, t_start + i / args.rate - time.monotonic()))
            tasks.append(asyncio.create_task(one()))
        await asyncio.gather(*tasks)
        await wait_until(
            lambda: sum(len(a.finished) for a in agents) >= len(trigger_at),
            timeout=args.timeout,
            interval=0.5,
        )
        stop_reading.set()
        await asyncio.gather(*readers)

    claimed = {r: t for a in agents for r, t in a.claimed.items()}
    finished = {r: t for a in agents for r, t in a.finished.items()}
    dispatch = [(claimed[r] - t) * 1000 for r, t in trigger_at.items() if r in claimed]
    e2e = [(finished[r] - t) * 1000 for r, t in trigger_at.items() if r in finished]
    proto: dict[str, list[float]] = {}
    for a in agents:
        for k, v in a.request_ms.items():
            proto.setdefault(k, []).extend(v)
    results["steady"] = {
        "rate_per_s": args.rate,
        "duration_s": args.duration,
        "runs": len(trigger_at),
        "completed": len(e2e),
        "latency": [
            summary("trigger request", trig_lat),
            summary("dispatch (trigger -> claimed by an agent)", dispatch),
            summary("end to end (trigger -> completed)", e2e),
            summary("GET /runs?limit=50 (dashboard)", read_lat),
            *(summary(f"agent {k}", v) for k, v in sorted(proto.items()) if k != "claim"),
        ],
    }
    stop.set()
    await asyncio.gather(*loops)
    await asyncio.gather(*(a.close() for a in agents))

    # 4. real agent, real processes
    if args.real_runs:
        print(
            f"[4/4] {args.real_runs} real runs on the bundled agent (queue 'default')…", flush=True
        )
        async with bench.user() as http:
            real_job = await bench.create_job(http, "default")
        real, _, _ = await bench.trigger_many(real_job, args.real_runs, 10)
        t0 = time.monotonic()
        statuses: dict[str, str] = {}
        async with bench.user() as http:
            while time.monotonic() - t0 < args.timeout:
                r = await http.get("/api/v1/runs", params={"job_id": real_job, "limit": 200})
                statuses = {x["id"]: x["status"] for x in r.json()["items"]}
                if len(statuses) >= len(real) and all(s in TERMINAL for s in statuses.values()):
                    break
                await asyncio.sleep(0.5)
            first = (
                await http.get("/api/v1/runs", params={"job_id": real_job, "limit": 200})
            ).json()["items"]
        durations = [
            x["duration_seconds"] * 1000 for x in first if x["duration_seconds"] is not None
        ]
        wall = time.monotonic() - t0
        results["real"] = {
            "runs": len(real),
            "succeeded": sum(1 for s in statuses.values() if s == "SUCCEEDED"),
            "wall_s": round(wall, 2),
            "runs_per_s": round(len(real) / wall, 2),
            "process_duration": summary("process start -> exit (`true`)", durations),
        }

    results["errors"] = {e: bench.errors.count(e) for e in sorted(set(bench.errors))}
    results["retried_transport_errors"] = {
        e: bench.retries.count(e) for e in sorted(set(bench.retries))
    }
    return results


def report(r: dict[str, Any]) -> str:
    lines = [
        "| Scenario | Result |",
        "|---|---|",
        f"| Trigger {r['trigger']['runs']} runs ({r['config']['concurrency']} clients) | "
        f"**{r['trigger']['runs_per_s']} runs/s**; p50 {r['trigger']['p50_ms']} ms, p99 {r['trigger']['p99_ms']} ms |",
        f"| Drain backlog ({r['drain']['agents']} agents × {r['drain']['slots_per_agent']} slots, "
        f"{r['drain']['log_lines_per_run']} log lines/run) | **{r['drain']['runs_per_s']} runs/s** end to end |",
    ]
    if "real" in r:
        lines.append(
            f"| Real agent, {r['real']['runs']} × `true` | {r['real']['runs_per_s']} runs/s, "
            f"{r['real']['succeeded']}/{r['real']['runs']} succeeded |"
        )
    lines += [
        "",
        f"Steady load: {r['steady']['rate_per_s']} runs/s for {r['steady']['duration_s']} s "
        f"({r['steady']['completed']}/{r['steady']['runs']} completed)",
        "",
        "| Latency | n | p50 | p95 | p99 | max |",
        "|---|---|---|---|---|---|",
    ]
    for s in r["steady"]["latency"]:
        lines.append(
            f"| {s['metric']} | {s['n']} | {s['p50_ms']} ms | {s['p95_ms']} ms | {s['p99_ms']} ms | {s['max_ms']} ms |"
        )
    if r["errors"]:
        lines += ["", "Errors: " + ", ".join(f"{k} ×{v}" for k, v in r["errors"].items())]
    if r["retried_transport_errors"]:
        lines += [
            "",
            "Retried (recovered) transport errors: "
            + ", ".join(f"{k} ×{v}" for k, v in r["retried_transport_errors"].items()),
        ]
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--token", required=True, help="user API token (tqt_…), operator role or higher")
    p.add_argument(
        "--enrollment-token", required=True, help="enrollment token for simulated agents"
    )
    p.add_argument("--runs", type=int, default=2000)
    p.add_argument("--concurrency", type=int, default=20)
    p.add_argument("--agents", type=int, default=20)
    p.add_argument("--slots", type=int, default=4)
    p.add_argument("--log-lines", type=int, default=5)
    p.add_argument("--rate", type=float, default=20.0)
    p.add_argument("--duration", type=float, default=30.0)
    p.add_argument("--readers", type=int, default=5)
    p.add_argument("--real-runs", type=int, default=100)
    p.add_argument("--timeout", type=float, default=600.0)
    p.add_argument("--json", help="write full results to this file")
    args = p.parse_args()
    results = asyncio.run(run_all(args))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(results, fh, indent=2)
    print()
    print(report(results))
    return 1 if results["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
