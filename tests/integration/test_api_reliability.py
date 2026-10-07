"""M2 endpoints over HTTP: cancel, retry, rerun, idempotency keys, queues, cancel signalling."""

import asyncio
from collections.abc import Iterator
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
from torqrun_db.models import Run
from torqrun_db.runs import utcnow
from torqrun_scheduler.main import Scheduler, SchedulerSettings

TOKEN = "test-enrollment-token"
NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    migrate.upgrade(database_url)
    settings = Settings(
        database_url=SecretStr(database_url),
        environment="development",
        dev_enrollment_token=SecretStr(TOKEN),
        claim_poll_interval_seconds=0.05,
    )
    with TestClient(create_app(settings)) as c:
        authenticate(c)
        yield c


def enroll(c: TestClient, name: str = "a1") -> dict[str, str]:
    r = c.post(
        "/api/v1/agent/enroll",
        json={
            "enrollment_token": TOKEN,
            "name": name,
            "hostname": "h",
            "os": "linux",
            "arch": "x",
            "agent_version": "t",
            "max_slots": 4,
        },
    )
    return {"Authorization": f"Bearer {r.json()['credential']}"}


def job(c: TestClient, name: str = "job", **spec: Any) -> str:
    r = c.post(
        "/api/v1/jobs", json={"name": name, "spec": {"runtime": "shell", "script": "x", **spec}}
    )
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def start(c: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    [a] = c.post(
        "/api/v1/agent/claim", json={"free_slots": 1, "wait_seconds": 1}, headers=auth
    ).json()["assignments"]
    base, lease = f"/api/v1/agent/attempts/{a['attempt_id']}", {"lease_token": a["lease_token"]}
    assert c.post(f"{base}/ack", json=lease, headers=auth).status_code == 204
    assert (
        c.post(
            f"{base}/started", json={**lease, "pid": 7, "started_at": NOW}, headers=auth
        ).status_code
        == 204
    )
    return {**a, "base": base, "lease": lease}


def complete(
    c: TestClient, auth: dict[str, str], a: dict[str, Any], outcome: str, code: int | None = 1
) -> int:
    body = {**a["lease"], "outcome": outcome, "exit_code": code, "finished_at": NOW}
    return int(c.post(f"{a['base']}/complete", json=body, headers=auth).status_code)


def test_cancel_running_job_is_signalled_on_heartbeat_and_logs(client: TestClient) -> None:
    auth = enroll(client)
    run = client.post(f"/api/v1/jobs/{job(client)}/runs").json()
    a = start(client, auth)
    r = client.post(f"/api/v1/runs/{run['id']}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "CANCEL_REQUESTED"
    hb = client.post(
        "/api/v1/agent/heartbeat",
        json={
            "running": [{"attempt_id": a["attempt_id"], "lease_token": a["lease_token"]}],
            "free_slots": 0,
        },
        headers=auth,
    ).json()
    assert hb["cancel"] == [a["attempt_id"]]
    logs = client.post(
        f"{a['base']}/logs",
        json={**a["lease"], "chunks": [{"seq": 0, "stream": "stdout", "ts": NOW, "data": "x"}]},
        headers=auth,
    ).json()
    assert logs["cancel_requested"] is True
    assert complete(client, auth, a, "cancelled", 143) == 204
    detail = client.get(f"/api/v1/runs/{run['id']}").json()
    assert detail["status"] == "CANCELLED"
    assert [e["to_status"] for e in detail["events"]][-2:] == ["CANCEL_REQUESTED", "CANCELLED"]
    assert client.post(f"/api/v1/runs/{run['id']}/cancel").status_code == 409


def test_cancel_queued_and_missing_run(client: TestClient) -> None:
    run = client.post(f"/api/v1/jobs/{job(client)}/runs").json()
    assert client.post(f"/api/v1/runs/{run['id']}/cancel").json()["status"] == "CANCELLED"
    assert (
        client.post("/api/v1/runs/00000000-0000-0000-0000-000000000000/cancel").status_code == 404
    )


def test_failed_run_retries_automatically_via_scheduler(
    client: TestClient, database_url: str
) -> None:
    auth = enroll(client)
    run = client.post(
        f"/api/v1/jobs/{job(client, retry={'max_attempts': 2, 'backoff_seconds': 1})}/runs"
    ).json()
    assert run["max_attempts"] == 2
    a = start(client, auth)
    assert complete(client, auth, a, "failed") == 204
    waiting = client.get(f"/api/v1/runs/{run['id']}").json()
    assert waiting["status"] == "RETRY_WAIT"
    assert waiting["next_attempt_at"] is not None

    async def tick_when_due() -> None:
        engine = create_engine(database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s, s.begin():
            await s.execute(update(Run).values(next_attempt_at=utcnow()))
        await Scheduler(SchedulerSettings(database_url=SecretStr(database_url)), maker).tick()
        await engine.dispose()

    asyncio.run(tick_when_due())
    queued = client.get(f"/api/v1/runs/{run['id']}").json()
    assert (queued["status"], queued["current_attempt"]) == ("QUEUED", 2)
    a2 = start(client, auth)
    assert a2["attempt_no"] == 2
    assert complete(client, auth, a2, "succeeded", 0) == 204
    final = client.get(f"/api/v1/runs/{run['id']}").json()
    assert final["status"] == "SUCCEEDED"
    assert [x["status"] for x in final["attempts"]] == ["FAILED", "SUCCEEDED"]


def test_manual_retry_and_rerun(client: TestClient) -> None:
    auth = enroll(client)
    job_id = job(client)
    run = client.post(f"/api/v1/jobs/{job_id}/runs").json()
    complete(client, auth, start(client, auth), "failed")
    retried = client.post(f"/api/v1/runs/{run['id']}/retry")
    assert retried.status_code == 201
    assert (retried.json()["id"], retried.json()["current_attempt"]) == (run["id"], 2)
    assert client.post(f"/api/v1/runs/{run['id']}/retry").status_code == 409  # already queued again
    client.put(f"/api/v1/jobs/{job_id}", json={"spec": {"runtime": "shell", "script": "v2"}})
    current = client.post(f"/api/v1/runs/{run['id']}/rerun").json()
    original = client.post(f"/api/v1/runs/{run['id']}/rerun?version=original").json()
    assert current["id"] != run["id"]
    assert (current["rerun_of"], current["job_version"], current["trigger"]) == (
        run["id"],
        2,
        "rerun",
    )
    assert original["job_version"] == 1


def test_idempotency_key_returns_the_same_run(client: TestClient) -> None:
    url = f"/api/v1/jobs/{job(client)}/runs"
    first = client.post(url, headers={"Idempotency-Key": "deploy-42"})
    again = client.post(url, headers={"Idempotency-Key": "deploy-42"})
    other = client.post(url, headers={"Idempotency-Key": "deploy-43"})
    assert first.status_code == again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert other.json()["id"] != first.json()["id"]
    assert client.get("/api/v1/runs").json()["total"] == 2


def test_queues_list_and_update(client: TestClient) -> None:
    job(client, "gpu-job", queue="gpu")
    names = {q["name"] for q in client.get("/api/v1/queues").json()}
    assert {"default", "gpu"} <= names
    r = client.put("/api/v1/queues/gpu", json={"max_concurrency": 2, "paused": True})
    assert r.status_code == 200
    assert (r.json()["max_concurrency"], r.json()["paused"]) == (2, True)
    assert client.put("/api/v1/queues/Bad Name", json={}).status_code == 422
