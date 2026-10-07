"""API behaviour when the database is unreachable (no Postgres needed)."""

import json
import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from torqrun_api.logging import JsonFormatter, request_id_var
from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db.migrate import head_revision

# Port 1 on loopback refuses connections immediately, so checks fail fast.
UNREACHABLE = "postgresql://torqrun:secret-password@127.0.0.1:1/torqrun"


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        database_url=SecretStr(UNREACHABLE), environment="test", readiness_timeout_seconds=2
    )
    with TestClient(create_app(settings)) as c:
        yield c


def test_healthz_is_ok_without_database(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_readyz_is_503_when_database_unreachable(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "not_ready"
    assert body["database"]["status"] == "unreachable"
    assert body["database"]["expected_revision"] == head_revision()


def test_responses_never_leak_database_credentials(client: TestClient) -> None:
    for path in ("/readyz", "/api/v1/system/info"):
        text = client.get(path).text
        assert "secret-password" not in text
        assert "127.0.0.1" not in text


def test_system_info_reports_version_and_environment(client: TestClient) -> None:
    body = client.get("/api/v1/system/info").json()
    assert body["name"] == "torqrun"
    assert body["environment"] == "test"
    assert body["version"]
    assert body["database"]["status"] == "unreachable"


def test_request_id_is_echoed_when_valid(client: TestClient) -> None:
    r = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_request_id_is_generated_when_missing_or_invalid(client: TestClient) -> None:
    generated = client.get("/healthz").headers["X-Request-ID"]
    assert len(generated) == 32
    replaced = client.get("/healthz", headers={"X-Request-ID": "x" * 200}).headers["X-Request-ID"]
    assert replaced != "x" * 200


def test_openapi_is_served_under_v1(client: TestClient) -> None:
    spec = client.get("/api/v1/openapi.json").json()
    assert "/api/v1/system/info" in spec["paths"]


def test_settings_repr_hides_database_password() -> None:
    settings = Settings(database_url=SecretStr(UNREACHABLE))
    assert "secret-password" not in repr(settings)


def test_json_formatter_includes_extras_and_request_id() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.path = "/x"
    token = request_id_var.set("rid-1")
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)
    assert line["msg"] == "hello world"
    assert line["level"] == "info"
    assert line["path"] == "/x"
    assert line["request_id"] == "rid-1"


def test_requests_needing_the_database_get_503_and_retry_after(client: TestClient) -> None:
    # Transient database trouble must look transient: agents retry 503, not 500.
    for method, path in (
        ("GET", "/api/v1/auth/status"),
        ("POST", "/api/v1/agent/heartbeat"),
    ):
        r = client.request(
            method,
            path,
            json={"running": [], "free_slots": 1},
            headers={"Authorization": "Bearer tqa_x"},
        )
        assert r.status_code == 503, (path, r.text)
        assert r.headers["retry-after"] == "2"
        assert "secret-password" not in r.text
