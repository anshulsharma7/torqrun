"""Prometheus metrics at ``GET /metrics``.

Request counters and latency come from a pure-ASGI middleware; platform gauges (runs by status,
queue depth, agents) are read from the database at scrape time, so every API replica reports
the same cluster-wide values: aggregate them with ``max``, not ``sum``.
"""

import hmac
import time
from datetime import timedelta

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response, status
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import func, select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from torqrun_api.deps import SessionDep, SettingsDep
from torqrun_core.states import RunStatus
from torqrun_db.models import Agent, Run, Schedule
from torqrun_db.runs import utcnow

router = APIRouter(tags=["metrics"])


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        r = self.registry
        self.requests = Counter(
            "torqrun_http_requests", "HTTP requests", ["method", "route", "status"], registry=r
        )
        self.latency = Histogram(
            "torqrun_http_request_duration_seconds",
            "HTTP request latency (long-polls and streams excluded)",
            ["method", "route"],
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
            registry=r,
        )
        self.runs = Gauge("torqrun_runs", "Runs by status", ["status"], registry=r)
        self.queued = Gauge("torqrun_queue_depth", "Queued runs per queue", ["queue"], registry=r)
        self.agents = Gauge("torqrun_agents", "Agents by state", ["state"], registry=r)
        self.schedules = Gauge("torqrun_schedules_enabled", "Enabled schedules", registry=r)


def metrics_of(app: FastAPI) -> Metrics:
    m: Metrics | None = getattr(app.state, "metrics", None)
    if m is None:
        m = app.state.metrics = Metrics()
    return m


UNTIMED = ("/api/v1/agent/claim", "/stream")  # long-polls / SSE would swamp the histogram


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] == "/metrics":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        code = 500

        async def capture(message: Message) -> None:
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            m = metrics_of(scope["app"])
            m.requests.labels(scope["method"], route, str(code)).inc()
            if not scope["path"].endswith(UNTIMED):
                m.latency.labels(scope["method"], route).observe(time.perf_counter() - started)


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request, session: SessionDep, settings: SettingsDep) -> Response:
    if settings.metrics_token is not None:
        expected = f"Bearer {settings.metrics_token.get_secret_value()}"
        if not hmac.compare_digest(request.headers.get("Authorization", ""), expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="metrics token required")
    m = metrics_of(request.app)
    by_status = dict(
        (await session.execute(select(Run.status, func.count()).group_by(Run.status))).all()
    )
    for s in RunStatus:
        m.runs.labels(s.value).set(by_status.get(s.value, 0))
    m.queued.clear()
    for queue, n in await session.execute(
        select(Run.queue, func.count())
        .where(Run.status == RunStatus.QUEUED.value)
        .group_by(Run.queue)
    ):
        m.queued.labels(queue).set(n)
    offline_before = utcnow() - timedelta(seconds=settings.agent_offline_after_seconds)
    agents = (await session.execute(select(Agent.status, Agent.last_seen_at))).all()
    counts = {"online": 0, "offline": 0, "draining": 0, "revoked": 0}
    for st, seen in agents:
        if st == "REVOKED":
            counts["revoked"] += 1
        elif seen is None or seen < offline_before:
            counts["offline"] += 1
        elif st == "DRAINING":
            counts["draining"] += 1
        else:
            counts["online"] += 1
    for k, v in counts.items():
        m.agents.labels(k).set(v)
    m.schedules.set(
        (
            await session.execute(
                select(func.count()).select_from(Schedule).where(Schedule.enabled)
            )
        ).scalar_one()
    )
    await session.commit()
    return Response(generate_latest(m.registry), media_type=CONTENT_TYPE_LATEST)
