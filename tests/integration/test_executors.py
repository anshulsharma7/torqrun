"""Executor capabilities: container jobs only go to agents that offer Docker."""

from typing import Any

from fastapi.testclient import TestClient
from tests.integration.test_agent_protocol import client, enroll  # noqa: F401 - fixture

CONTAINER = {"executor": "docker", "container": {"image": "python:3.12-slim"}}


def job(c: TestClient, name: str, **spec: Any) -> str:
    r = c.post(
        "/api/v1/jobs", json={"name": name, "spec": {"runtime": "shell", "script": "true", **spec}}
    )
    assert r.status_code == 201, r.text
    run = c.post(f"/api/v1/jobs/{r.json()['id']}/runs").json()
    return str(run["id"])


def claim(c: TestClient, auth: dict[str, str], n: int = 5) -> list[str]:
    r = c.post("/api/v1/agent/claim", json={"free_slots": n, "wait_seconds": 0}, headers=auth)
    assert r.status_code == 200, r.text
    return [a["run_id"] for a in r.json()["assignments"]]


def test_container_jobs_wait_for_a_docker_agent(client: TestClient) -> None:  # noqa: F811
    docker_runs = [job(client, f"in-container-{i}", **CONTAINER) for i in range(30)]
    plain = job(client, "plain")
    process_only = enroll(client, "process-only")
    # A backlog of container jobs doesn't hide the process job queued behind it.
    assert claim(client, process_only) == [plain]
    assert claim(client, process_only) == []

    docker_agent = enroll(client, "with-docker")
    hb = client.post(
        "/api/v1/agent/heartbeat",
        json={"running": [], "free_slots": 5, "capabilities": ["process", "docker"]},
        headers=docker_agent,
    )
    assert hb.status_code == 200, hb.text
    got = claim(client, docker_agent)
    assert len(got) == 2 and set(got) <= set(docker_runs)  # max_slots=2 at enrollment
    agent = next(a for a in client.get("/api/v1/agents").json() if a["name"] == "with-docker")
    assert "docker" in agent["capabilities"]


def test_container_spec_is_validated(client: TestClient) -> None:  # noqa: F811
    for bad in (
        {"executor": "docker"},
        {"executor": "docker", "container": {"image": "--privileged"}},
        {"container": {"image": "alpine"}},
        {"executor": "docker", "container": {"image": "alpine", "network": "host"}},
    ):
        r = client.post(
            "/api/v1/jobs",
            json={"name": "bad", "spec": {"runtime": "shell", "script": "x", **bad}},
        )
        assert r.status_code == 422, bad
