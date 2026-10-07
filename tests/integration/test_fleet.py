"""M3: enrollment tokens, agent revoke/drain, credential rotation, installer endpoints."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, text, update
from sqlalchemy.orm import Session
from tests.auth import authenticate

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import migrate
from torqrun_db.models import AgentCredential
from torqrun_db.runs import utcnow

NOW = "2026-01-01T00:00:00Z"


@pytest.fixture
def assets(tmp_path: Path) -> Path:
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "torqrun_agent-0.1.0-py3-none-any.whl").write_bytes(b"PK fake")
    (tmp_path / "install-agent.sh").write_text(
        'SERVER_URL="${TORQRUN_SERVER_URL:-__TORQRUN_SERVER_URL__}"\n'
    )
    return tmp_path


@pytest.fixture
def client(database_url: str, assets: Path) -> Iterator[TestClient]:
    migrate.upgrade(database_url)
    settings = Settings(
        database_url=SecretStr(database_url),
        environment="development",
        claim_poll_interval_seconds=0.05,
        agent_assets_dir=str(assets),
    )
    with TestClient(create_app(settings)) as c:
        authenticate(c)
        yield c


def token(c: TestClient, **body: Any) -> dict[str, Any]:
    r = c.post("/api/v1/enrollment-tokens", json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


def enroll(c: TestClient, tok: str, name: str = "srv-1", **extra: Any) -> Any:
    return c.post(
        "/api/v1/agent/enroll",
        json={
            "enrollment_token": tok,
            "name": name,
            "hostname": "h",
            "os": "linux",
            "arch": "x86_64",
            "agent_version": "t",
            "max_slots": 2,
            **extra,
        },
    )


def auth(credential: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {credential}"}


def hb(c: TestClient, cred: str) -> Any:
    return c.post(
        "/api/v1/agent/heartbeat", json={"running": [], "free_slots": 1}, headers=auth(cred)
    )


def test_single_use_token_enrolls_once_and_applies_presets(client: TestClient) -> None:
    t = token(client, description="web-01", queues=["gpu"], tags=["eu"])
    assert t["token"].startswith("tqe_")
    assert t["install_command"].endswith(f"--token {t['token']}")
    assert "/agent/install.sh | sudo bash" in t["install_command"]
    first = enroll(client, t["token"], queues=["default"], tags=["ssd"])
    assert first.status_code == 201
    assert (first.json()["queues"], first.json()["tags"]) == (["gpu"], ["eu", "ssd"])
    second = enroll(client, t["token"], name="srv-2")
    assert second.status_code == 401
    assert second.json()["detail"] == "enrollment token already used"
    listed = client.get("/api/v1/enrollment-tokens", params={"include_inactive": True}).json()
    assert listed[0]["state"] == "used"
    assert "token" not in listed[0]  # the secret is never shown again


def test_expired_revoked_and_unknown_tokens_are_rejected(
    client: TestClient, database_url: str
) -> None:
    expired, revoked = token(client), token(client)
    with Session(create_engine(database_url)) as s:
        s.execute(
            text(
                "update enrollment_tokens set expires_at = now() - interval '1 second' where id = :i"
            ),
            {"i": expired["id"]},
        )
        s.commit()
    assert client.delete(f"/api/v1/enrollment-tokens/{revoked['id']}").status_code == 204
    assert enroll(client, expired["token"]).json()["detail"] == "enrollment token expired"
    assert enroll(client, revoked["token"]).json()["detail"] == "invalid enrollment token"
    assert enroll(client, "tqe_made-up-token-value").status_code == 401
    assert client.get("/api/v1/enrollment-tokens").json() == []  # only active ones by default


def test_multi_use_token(client: TestClient) -> None:
    t = token(client, max_uses=3)
    assert [enroll(client, t["token"], name=f"n{i}").status_code for i in range(4)] == [
        201,
        201,
        201,
        401,
    ]


def test_revoke_cuts_off_agent_and_loses_its_running_work(client: TestClient) -> None:
    cred = enroll(client, token(client)["token"]).json()["credential"]
    agent_id = client.get("/api/v1/agents").json()[0]["id"]
    job = client.post(
        "/api/v1/jobs", json={"name": "j", "spec": {"runtime": "shell", "script": "x"}}
    ).json()
    run = client.post(f"/api/v1/jobs/{job['id']}/runs").json()
    [a] = client.post(
        "/api/v1/agent/claim", json={"free_slots": 1, "wait_seconds": 1}, headers=auth(cred)
    ).json()["assignments"]
    lease = {"lease_token": a["lease_token"]}
    client.post(f"/api/v1/agent/attempts/{a['attempt_id']}/ack", json=lease, headers=auth(cred))
    client.post(
        f"/api/v1/agent/attempts/{a['attempt_id']}/started",
        json={**lease, "pid": 1, "started_at": NOW},
        headers=auth(cred),
    )

    assert client.post(f"/api/v1/agents/{agent_id}/revoke").json()["status"] == "REVOKED"
    assert hb(client, cred).status_code == 401  # credential is dead immediately
    detail = client.get(f"/api/v1/runs/{run['id']}").json()
    assert detail["status"] == "LOST"
    assert "revoked" in detail["error_summary"]
    assert client.post(f"/api/v1/agents/{agent_id}/drain").status_code == 409


def test_revoking_before_ack_requeues_the_run(client: TestClient) -> None:
    cred = enroll(client, token(client)["token"]).json()["credential"]
    agent_id = client.get("/api/v1/agents").json()[0]["id"]
    job = client.post(
        "/api/v1/jobs", json={"name": "j", "spec": {"runtime": "shell", "script": "x"}}
    ).json()
    run = client.post(f"/api/v1/jobs/{job['id']}/runs").json()
    client.post(
        "/api/v1/agent/claim", json={"free_slots": 1, "wait_seconds": 1}, headers=auth(cred)
    )
    client.post(f"/api/v1/agents/{agent_id}/revoke")
    assert client.get(f"/api/v1/runs/{run['id']}").json()["status"] == "QUEUED"


def test_drain_stops_new_work_and_resume_restores_it(client: TestClient) -> None:
    cred = enroll(client, token(client)["token"]).json()["credential"]
    agent_id = client.get("/api/v1/agents").json()[0]["id"]
    job = client.post(
        "/api/v1/jobs", json={"name": "j", "spec": {"runtime": "shell", "script": "x"}}
    ).json()
    client.post(f"/api/v1/jobs/{job['id']}/runs")
    assert client.post(f"/api/v1/agents/{agent_id}/drain").json()["status"] == "DRAINING"
    assert hb(client, cred).json()["agent_status"] == "DRAINING"
    claim = {"free_slots": 1, "wait_seconds": 0.2}
    assert (
        client.post("/api/v1/agent/claim", json=claim, headers=auth(cred)).json()["assignments"]
        == []
    )
    client.post(f"/api/v1/agents/{agent_id}/resume")
    assert (
        len(
            client.post("/api/v1/agent/claim", json=claim, headers=auth(cred)).json()["assignments"]
        )
        == 1
    )


def test_agent_can_drain_itself(client: TestClient) -> None:
    cred = enroll(client, token(client)["token"]).json()["credential"]
    assert client.post("/api/v1/agent/drain", json={}, headers=auth(cred)).status_code == 204
    assert client.get("/api/v1/agents").json()[0]["status"] == "DRAINING"


def test_credential_rotation_with_overlap(client: TestClient, database_url: str) -> None:
    old = enroll(client, token(client)["token"]).json()["credential"]
    new = client.post("/api/v1/agent/credentials/rotate", json={}, headers=auth(old)).json()[
        "credential"
    ]
    assert new != old and new.startswith("tqa_")
    assert hb(client, new).status_code == 200
    assert hb(client, old).status_code == 200  # still valid during the overlap window
    with Session(create_engine(database_url)) as s:
        s.execute(
            update(AgentCredential)
            .where(AgentCredential.expires_at.is_not(None))
            .values(expires_at=utcnow() - timedelta(seconds=1))
        )
        s.commit()
    assert hb(client, old).status_code == 401
    assert hb(client, new).status_code == 200


def test_capabilities_reported_on_heartbeat(client: TestClient, database_url: str) -> None:
    cred = enroll(client, token(client)["token"]).json()["credential"]
    client.post(
        "/api/v1/agent/heartbeat",
        json={"running": [], "free_slots": 1, "capabilities": ["process", "docker"]},
        headers=auth(cred),
    )
    with Session(create_engine(database_url)) as s:
        assert s.execute(text("select capabilities from agents")).scalar_one() == [
            "process",
            "docker",
        ]


def test_installer_and_packages_are_served(client: TestClient) -> None:
    script = client.get("/agent/install.sh")
    assert script.status_code == 200
    assert (
        "${TORQRUN_SERVER_URL:-http://testserver}" in script.text
    )  # URL baked in from the request
    assert client.get("/agent/dist/").json() == ["torqrun_agent-0.1.0-py3-none-any.whl"]
    assert client.get("/agent/dist/torqrun_agent-0.1.0-py3-none-any.whl").content == b"PK fake"
    assert client.get("/agent/dist/..%2Finstall-agent.sh").status_code == 404
    assert client.get("/agent/dist/evil.sh").status_code == 404
