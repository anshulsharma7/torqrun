"""Artifact upload rules: lease checks, names, size and count limits, idempotent re-upload,
safe downloads, retention."""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.auth import authenticate
from tests.integration.test_agent_protocol import TOKEN, claim_one, enroll, make_run

from torqrun_api.main import create_app
from torqrun_api.routes_artifacts import purge_expired
from torqrun_api.settings import Settings
from torqrun_db import create_engine, migrate
from torqrun_db.runs import utcnow


@pytest.fixture
def settings(database_url: str, tmp_path: Path) -> Settings:
    migrate.upgrade(database_url)
    return Settings(
        database_url=SecretStr(database_url),
        environment="development",
        dev_enrollment_token=SecretStr(TOKEN),
        claim_poll_interval_seconds=0.05,
        artifacts_dir=str(tmp_path / "store"),
        max_artifact_bytes=10_000,
        max_attempt_artifact_bytes=15_000,
        max_artifacts_per_attempt=3,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        authenticate(c)
        yield c


def running(client: TestClient) -> tuple[dict[str, Any], dict[str, str]]:
    make_run(client)
    auth = enroll(client)
    a = claim_one(client, auth)
    return a, auth


def put(
    client: TestClient,
    a: dict[str, Any],
    auth: dict[str, str],
    name: str,
    data: bytes,
    lease: str | None = None,
) -> Any:
    return client.put(
        f"/api/v1/agent/attempts/{a['attempt_id']}/artifacts/{name}",
        content=data,
        headers={**auth, "X-Torqrun-Lease": lease or a["lease_token"]},
    )


def test_upload_rules(client: TestClient, settings: Settings) -> None:
    a, auth = running(client)
    assert put(client, a, auth, "out.txt", b"hello").status_code == 201
    again = put(client, a, auth, "out.txt", b"hello!")  # re-upload replaces
    assert again.status_code == 201 and again.json()["size_bytes"] == 6
    assert put(client, a, auth, "x.txt", b"1", lease="wrong").status_code == 409
    for bad in ("..%2Fescape", ".hidden", "a b"):
        assert put(client, a, auth, bad, b"1").status_code in (404, 422), bad
    assert put(client, a, auth, "big.bin", b"x" * 10_001).status_code == 413
    assert put(client, a, auth, "nine.bin", b"x" * 9_000).status_code == 201
    over_total = put(client, a, auth, "more.bin", b"x" * 7_000)  # 6 + 9000 + 7000 > 15000
    assert over_total.status_code == 413
    assert put(client, a, auth, "third.bin", b"x").status_code == 201
    assert put(client, a, auth, "fourth.bin", b"x").status_code == 413  # count limit
    # Only complete, accepted files are kept on disk.
    store = Path(settings.artifacts_dir)
    names = sorted(p.name for p in store.rglob("*") if p.is_file())
    assert names == ["nine.bin", "out.txt", "third.bin"]

    other = enroll(client, "other-agent")
    assert put(client, a, other, "y.txt", b"1").status_code == 409  # not this agent's attempt

    listed = client.get(f"/api/v1/runs/{a['run_id']}/artifacts").json()
    assert [x["name"] for x in listed] == ["nine.bin", "out.txt", "third.bin"]
    out = next(x for x in listed if x["name"] == "out.txt")
    r = client.get(f"/api/v1/runs/{a['run_id']}/artifacts/{out['id']}/download")
    assert r.content == b"hello!" and r.headers["x-content-type-options"] == "nosniff"
    assert (
        client.get(f"/api/v1/runs/{a['run_id']}/artifacts/{a['attempt_id']}/download").status_code
        == 404
    )

    signed_out = TestClient(client.app)
    assert signed_out.get(f"/api/v1/runs/{a['run_id']}/artifacts").status_code == 401


async def test_retention_deletes_rows_and_files(
    client: TestClient, settings: Settings, database_url: str
) -> None:
    a, auth = running(client)
    put(client, a, auth, "old.txt", b"old")
    engine = create_engine(database_url, pool_size=2)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    assert await purge_expired(maker, settings) == 0
    later = utcnow() + timedelta(days=settings.artifact_retention_days + 1)
    assert await purge_expired(maker, settings, now=later) == 1
    await engine.dispose()
    assert client.get(f"/api/v1/runs/{a['run_id']}/artifacts").json() == []
    assert not any(p.is_file() for p in Path(settings.artifacts_dir).rglob("*"))
