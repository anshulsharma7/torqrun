"""Agent protocol over HTTP, against a real database."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.auth import authenticate

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import migrate

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


def enroll(client: TestClient, name: str = "agent-1") -> dict[str, str]:
    r = client.post(
        "/api/v1/agent/enroll",
        json={
            "enrollment_token": TOKEN,
            "name": name,
            "hostname": "h",
            "os": "linux",
            "arch": "x86_64",
            "agent_version": "test",
            "max_slots": 2,
        },
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['credential']}"}


def make_run(client: TestClient, name: str = "job") -> dict[str, Any]:
    job = client.post(
        "/api/v1/jobs", json={"name": name, "spec": {"runtime": "shell", "script": "echo hi"}}
    )
    assert job.status_code == 201, job.text
    run = client.post(f"/api/v1/jobs/{job.json()['id']}/runs")
    assert run.status_code == 201
    return run.json()  # type: ignore[no-any-return]


def claim_one(client: TestClient, auth: dict[str, str]) -> dict[str, Any]:
    r = client.post("/api/v1/agent/claim", json={"free_slots": 1, "wait_seconds": 1}, headers=auth)
    assert r.status_code == 200, r.text
    [assignment] = r.json()["assignments"]
    return assignment  # type: ignore[no-any-return]


def test_enrollment_requires_valid_token_and_unique_name(client: TestClient) -> None:
    bad = client.post(
        "/api/v1/agent/enroll",
        json={
            "enrollment_token": "wrong-token-123",
            "name": "x",
            "hostname": "h",
            "os": "linux",
            "arch": "x",
            "agent_version": "t",
            "max_slots": 1,
        },
    )
    assert bad.status_code == 401
    enroll(client, "dup")
    again = client.post(
        "/api/v1/agent/enroll",
        json={
            "enrollment_token": TOKEN,
            "name": "dup",
            "hostname": "h",
            "os": "linux",
            "arch": "x",
            "agent_version": "t",
            "max_slots": 1,
        },
    )
    assert again.status_code == 409


def test_agent_endpoints_require_credential(client: TestClient) -> None:
    body = {"running": [], "free_slots": 1}
    assert client.post("/api/v1/agent/heartbeat", json=body).status_code == 401
    bad = {"Authorization": "Bearer tqa_not-a-real-credential"}
    assert client.post("/api/v1/agent/heartbeat", json=body, headers=bad).status_code == 401


def test_full_protocol_flow(client: TestClient) -> None:
    auth = enroll(client)
    run = make_run(client)
    hb = client.post("/api/v1/agent/heartbeat", json={"running": [], "free_slots": 2}, headers=auth)
    assert hb.status_code == 200
    a = claim_one(client, auth)
    assert a["run_id"] == run["id"]
    assert a["spec"]["script"] == "echo hi"
    base, lease = f"/api/v1/agent/attempts/{a['attempt_id']}", {"lease_token": a["lease_token"]}

    assert client.post(f"{base}/ack", json=lease, headers=auth).status_code == 204
    assert client.post(f"{base}/ack", json=lease, headers=auth).status_code == 204  # retried ack
    assert (
        client.post(
            f"{base}/started", json={**lease, "pid": 42, "started_at": NOW}, headers=auth
        ).status_code
        == 204
    )
    batch = {**lease, "chunks": [{"seq": 0, "stream": "stdout", "ts": NOW, "data": "hi\n"}]}
    assert client.post(f"{base}/logs", json=batch, headers=auth).json() == {
        "accepted": 1,
        "cancel_requested": False,
    }
    assert client.post(f"{base}/logs", json=batch, headers=auth).json()["accepted"] == 0
    done = {**lease, "outcome": "succeeded", "exit_code": 0, "finished_at": NOW, "last_log_seq": 0}
    assert client.post(f"{base}/complete", json=done, headers=auth).status_code == 204
    # The response was "lost" and the agent retries: same outcome is accepted idempotently.
    assert client.post(f"{base}/complete", json=done, headers=auth).status_code == 204

    detail = client.get(f"/api/v1/runs/{run['id']}").json()
    assert detail["status"] == "SUCCEEDED"
    assert detail["exit_code"] == 0
    assert [e["to_status"] for e in detail["events"]] == [
        "QUEUED",
        "DISPATCHED",
        "STARTING",
        "RUNNING",
        "SUCCEEDED",
    ]
    logs = client.get(f"/api/v1/runs/{run['id']}/logs").json()
    assert logs["complete"] is True
    assert [c["data"] for c in logs["chunks"]] == ["hi\n"]
    agents = client.get("/api/v1/agents").json()
    assert agents[0]["connected"] is True
    assert agents[0]["status"] == "ONLINE"


def test_stale_lease_is_rejected_with_409(client: TestClient) -> None:
    auth = enroll(client)
    make_run(client)
    a = claim_one(client, auth)
    r = client.post(
        f"/api/v1/agent/attempts/{a['attempt_id']}/ack", json={"lease_token": "stale"}, headers=auth
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "lease_revoked"


def test_other_agent_cannot_report_on_attempt(client: TestClient) -> None:
    owner, intruder = enroll(client, "owner"), enroll(client, "intruder")
    make_run(client)
    a = claim_one(client, owner)
    r = client.post(
        f"/api/v1/agent/attempts/{a['attempt_id']}/ack",
        json={"lease_token": a["lease_token"]},
        headers=intruder,
    )
    assert r.status_code == 409


def test_cannot_succeed_without_starting(client: TestClient) -> None:
    auth = enroll(client)
    make_run(client)
    a = claim_one(client, auth)
    base, lease = f"/api/v1/agent/attempts/{a['attempt_id']}", {"lease_token": a["lease_token"]}
    client.post(f"{base}/ack", json=lease, headers=auth)
    r = client.post(
        f"{base}/complete",
        json={**lease, "outcome": "succeeded", "exit_code": 0, "finished_at": NOW},
        headers=auth,
    )
    assert r.status_code == 409
    assert "invalid_transition" in r.json()["detail"]


def test_heartbeat_reports_leases_the_agent_no_longer_holds(client: TestClient) -> None:
    auth = enroll(client)
    make_run(client)
    a = claim_one(client, auth)
    hb = client.post(
        "/api/v1/agent/heartbeat",
        json={
            "running": [{"attempt_id": a["attempt_id"], "lease_token": "not-mine"}],
            "free_slots": 1,
        },
        headers=auth,
    ).json()
    assert hb["revoked_leases"] == [a["attempt_id"]]


def test_empty_claim_returns_after_wait(client: TestClient) -> None:
    auth = enroll(client)
    r = client.post(
        "/api/v1/agent/claim", json={"free_slots": 1, "wait_seconds": 0.2}, headers=auth
    )
    assert r.json() == {"assignments": []}


def test_job_edit_creates_version_and_old_run_keeps_snapshot(client: TestClient) -> None:
    run = make_run(client, "versioned")
    job_id = run["job_id"]
    edited = client.put(
        f"/api/v1/jobs/{job_id}", json={"spec": {"runtime": "shell", "script": "echo v2"}}
    )
    assert edited.json()["current_version"] == 2
    unchanged = client.put(
        f"/api/v1/jobs/{job_id}",
        json={"description": "d", "spec": {"runtime": "shell", "script": "echo v2"}},
    )
    assert unchanged.json()["current_version"] == 2  # same spec: no new version
    old = client.get(f"/api/v1/runs/{run['id']}").json()
    assert (old["job_version"], old["spec"]["script"]) == (1, "echo hi")
    versions = client.get(f"/api/v1/jobs/{job_id}/versions").json()
    assert [v["version"] for v in versions] == [2, 1]


def test_heartbeat_stats_and_active_runs_are_exposed(client: TestClient) -> None:
    auth = enroll(client, "stats-agent")
    run = make_run(client, "busy-job")
    stats = {
        "cpu_count": 8,
        "load_1m": 0.5,
        "mem_total_bytes": 16 * 2**30,
        "os_pretty": "Ubuntu 24.04",
    }
    client.post(
        "/api/v1/agent/heartbeat",
        json={"running": [], "free_slots": 2, "system": stats},
        headers=auth,
    )
    a = claim_one(client, auth)
    [agent] = client.get("/api/v1/agents").json()
    assert agent["system"]["cpu_count"] == 8
    assert agent["system"]["os_pretty"] == "Ubuntu 24.04"
    assert agent["running"] == 1
    assert agent["active_runs"][0]["job_name"] == "busy-job"
    assert agent["active_runs"][0]["run_id"] == a["run_id"]
    detail = client.get(f"/api/v1/agents/{agent['id']}").json()
    assert detail["name"] == "stats-agent"
    assert client.get("/api/v1/agents/00000000-0000-0000-0000-000000000000").status_code == 404
    runs = client.get("/api/v1/runs", params={"agent_id": agent["id"]}).json()
    assert [r["id"] for r in runs["items"]] == [run["id"]]


def test_heartbeat_without_stats_is_still_accepted(client: TestClient) -> None:
    auth = enroll(client, "old-agent")
    r = client.post("/api/v1/agent/heartbeat", json={"running": [], "free_slots": 1}, headers=auth)
    assert r.status_code == 200
    assert client.get("/api/v1/agents").json()[0]["system"] is None
