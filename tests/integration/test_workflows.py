"""M5: workflows over HTTP with a simulated agent and the real advancer."""

import asyncio
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.auth import authenticate

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import create_engine, migrate
from torqrun_db import schedules as sched_ops
from torqrun_db import workflows as wf_ops
from torqrun_db.models import Schedule
from torqrun_db.runs import utcnow

TOKEN = "test-enrollment-token"
NOW = "2026-01-01T00:00:00Z"
DIAMOND = """
tasks:
  extract: {job: extract}
  left:    {job: left, depends_on: [extract]}
  right:   {job: right, depends_on: [extract]}
  join:    {job: join, depends_on: [left, right]}
"""


class Env:
    def __init__(self, client: TestClient, database_url: str) -> None:
        self.c = client
        self.db = database_url
        r = client.post(
            "/api/v1/agent/enroll",
            json={
                "enrollment_token": TOKEN,
                "name": "sim",
                "hostname": "h",
                "os": "linux",
                "arch": "x",
                "agent_version": "t",
                "max_slots": 50,
            },
        )
        self.auth = {"Authorization": f"Bearer {r.json()['credential']}"}
        self.order: list[list[str]] = []

    def jobs(self, *names: str) -> None:
        for n in names:
            assert (
                self.c.post(
                    "/api/v1/jobs", json={"name": n, "spec": {"runtime": "shell", "script": "x"}}
                ).status_code
                == 201
            )

    def advance(self) -> None:
        async def go() -> None:
            engine = create_engine(self.db)
            async with async_sessionmaker(engine, expire_on_commit=False)() as s, s.begin():
                await wf_ops.advance_running(s)
            await engine.dispose()

        asyncio.run(go())

    def step(self, outcomes: dict[str, str]) -> list[str]:
        """Claim everything queued, finish each task per ``outcomes`` (default succeeded), advance."""
        claim = self.c.post(
            "/api/v1/agent/claim", json={"free_slots": 50, "wait_seconds": 0}, headers=self.auth
        ).json()
        names = []
        for a in claim["assignments"]:
            base, lease = (
                f"/api/v1/agent/attempts/{a['attempt_id']}",
                {"lease_token": a["lease_token"]},
            )
            self.c.post(f"{base}/ack", json=lease, headers=self.auth)
            self.c.post(
                f"{base}/started", json={**lease, "pid": 1, "started_at": NOW}, headers=self.auth
            )
            outcome = outcomes.get(a["job_name"], "succeeded")
            code = 0 if outcome == "succeeded" else 1
            self.c.post(
                f"{base}/complete",
                json={**lease, "outcome": outcome, "exit_code": code, "finished_at": NOW},
                headers=self.auth,
            )
            names.append(a["task_key"])
        self.order.append(sorted(names))
        self.advance()
        return sorted(names)

    def run_until_done(self, wr_id: str, outcomes: dict[str, str] | None = None) -> dict[str, Any]:
        for _ in range(20):
            detail = self.c.get(f"/api/v1/workflow-runs/{wr_id}").json()
            if detail["status"] != "RUNNING":
                return dict(detail)
            self.step(outcomes or {})
        raise AssertionError("workflow did not finish")


@pytest.fixture
def env(database_url: str) -> Iterator[Env]:
    migrate.upgrade(database_url)
    settings = Settings(
        database_url=SecretStr(database_url),
        environment="development",
        dev_enrollment_token=SecretStr(TOKEN),
        claim_poll_interval_seconds=0.05,
    )
    with TestClient(create_app(settings)) as c:
        authenticate(c)
        yield Env(c, database_url)


def create(env: Env, source: str, name: str = "pipeline") -> dict[str, Any]:
    r = env.c.post("/api/v1/workflows", json={"name": name, "source": source})
    assert r.status_code == 201, r.text
    return dict(r.json())


def states(detail: dict[str, Any]) -> dict[str, str]:
    return {t["key"]: t["state"] for t in detail["tasks"]}


def test_diamond_runs_in_dependency_order_with_parallel_branches(env: Env) -> None:
    env.jobs("extract", "left", "right", "join")
    wf = create(env, DIAMOND)
    assert wf["layers"] == [["extract"], ["left", "right"], ["join"]]
    wr = env.c.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    detail = env.run_until_done(wr["id"])
    assert detail["status"] == "SUCCEEDED"
    assert env.order == [["extract"], ["left", "right"], ["join"]]  # left and right together
    assert set(states(detail).values()) == {"SUCCEEDED"}
    run_id = detail["tasks"][0]["run_id"]
    assert env.c.get(f"/api/v1/runs/{run_id}").json()["trigger"] == "workflow"


def test_failure_skips_downstream_but_cleanup_and_alert_run(env: Env) -> None:
    env.jobs("work", "report", "cleanup", "alert")
    wf = create(
        env,
        """
tasks:
  work: {job: work}
  report: {job: report, depends_on: [work]}
  cleanup: {job: cleanup, depends_on: [work], trigger_rule: all_done}
  alert: {job: alert, depends_on: [work], trigger_rule: one_failed}
""",
    )
    wr = env.c.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    detail = env.run_until_done(wr["id"], {"work": "failed"})
    assert detail["status"] == "FAILED"
    assert states(detail) == {
        "work": "FAILED",
        "report": "SKIPPED",
        "cleanup": "SUCCEEDED",
        "alert": "SUCCEEDED",
    }
    report = next(t for t in detail["tasks"] if t["key"] == "report")
    assert "trigger rule all_success" in report["note"]


def test_rerun_failed_reuses_successful_tasks(env: Env) -> None:
    env.jobs("extract", "left", "right", "join")
    wf = create(env, DIAMOND)
    wr = env.c.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    first = env.run_until_done(wr["id"], {"right": "failed"})
    assert states(first) == {
        "extract": "SUCCEEDED",
        "left": "SUCCEEDED",
        "right": "FAILED",
        "join": "SKIPPED",
    }
    env.order.clear()
    again = env.c.post(f"/api/v1/workflow-runs/{wr['id']}/rerun?mode=failed").json()
    assert again["rerun_of"] == wr["id"]
    second = env.run_until_done(again["id"])
    assert second["status"] == "SUCCEEDED"
    assert env.order == [["right"], ["join"]]  # extract and left were not run again
    reused = {t["key"] for t in second["tasks"] if t["reused"]}
    assert reused == {"extract", "left"}
    assert env.c.post(f"/api/v1/workflow-runs/{again['id']}/rerun?mode=failed").status_code == 409


def test_cancel_stops_active_tasks_and_skips_pending(env: Env) -> None:
    env.jobs("extract", "left", "right", "join")
    wf = create(env, DIAMOND)
    wr = env.c.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    r = env.c.post(f"/api/v1/workflow-runs/{wr['id']}/cancel")
    assert r.json()["status"] == "CANCELLED"
    detail = env.c.get(f"/api/v1/workflow-runs/{wr['id']}").json()
    assert states(detail)["join"] == "SKIPPED"
    extract_run = next(t for t in detail["tasks"] if t["key"] == "extract")["run_id"]
    assert env.c.get(f"/api/v1/runs/{extract_run}").json()["status"] == "CANCELLED"
    assert env.c.post(f"/api/v1/workflow-runs/{wr['id']}/cancel").status_code == 409


def test_validation_errors_are_explained(env: Env) -> None:
    env.jobs("a")
    cases = {
        "tasks: [": "invalid YAML",
        "tasks: {x: {job: a, depends_on: [y]}}": "unknown task 'y'",
        "tasks: {x: {job: a, depends_on: [y]}, y: {job: a, depends_on: [x]}}": "cycle",
        "tasks: {x: {job: nope}}": "unknown job(s): nope",
    }
    for source, message in cases.items():
        v = env.c.post("/api/v1/workflows/validate", json={"source": source}).json()
        assert v["ok"] is False and message in v["error"], (source, v)
        assert (
            env.c.post("/api/v1/workflows", json={"name": "bad", "source": source}).status_code
            == 422
        )
    ok = env.c.post("/api/v1/workflows/validate", json={"source": "tasks: {x: {job: a}}"}).json()
    assert ok == {
        "ok": True,
        "error": None,
        "tasks": [{"key": "x", "job": "a", "depends_on": [], "trigger_rule": "all_success"}],
        "layers": [["x"]],
    }


def test_editing_creates_versions_and_old_runs_keep_theirs(env: Env) -> None:
    env.jobs("a", "b")
    wf = create(env, "tasks: {x: {job: a}}")
    wr = env.c.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    edited = env.c.put(
        f"/api/v1/workflows/{wf['id']}",
        json={"source": "tasks: {x: {job: a}, y: {job: b, depends_on: [x]}}"},
    ).json()
    assert edited["current_version"] == 2
    assert [t["key"] for t in env.c.get(f"/api/v1/workflow-runs/{wr['id']}").json()["tasks"]] == [
        "x"
    ]


def test_scheduled_workflow_fires_once_per_slot(env: Env) -> None:
    env.jobs("a")
    wf = create(env, "tasks: {x: {job: a}}")
    sched = env.c.post(
        "/api/v1/schedules",
        json={"name": "wf-nightly", "workflow_id": wf["id"], "cron": "* * * * *"},
    ).json()
    assert (sched["target"], sched["job_name"]) == ("workflow", "pipeline")
    assert (
        env.c.post(
            "/api/v1/schedules",
            json={"name": "both", "workflow_id": wf["id"], "job_id": wf["id"], "cron": "@daily"},
        ).status_code
        == 422
    )

    async def fire_twice() -> None:
        engine = create_engine(env.db)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        for _ in range(2):
            async with maker() as s, s.begin():
                await s.execute(
                    update(Schedule).values(
                        next_fire_at=(utcnow() - timedelta(seconds=5)).replace(
                            second=0, microsecond=0
                        )
                    )
                )
            async with maker() as s, s.begin():
                await sched_ops.fire_due(s)
        await engine.dispose()

    asyncio.run(fire_twice())
    runs = env.c.get("/api/v1/workflow-runs", params={"workflow_id": wf["id"]}).json()
    assert runs["total"] == 1  # the replayed slot did not create a second workflow run
    assert runs["items"][0]["trigger"] == "schedule"
