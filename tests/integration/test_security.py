"""Users, sessions, API tokens, roles, CSRF, audit log, secrets and metrics, end to end."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from tests.auth import ADMIN_EMAIL, ADMIN_PASSWORD, CLIENT_HEADER, authenticate
from tests.integration.test_agent_protocol import TOKEN

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import migrate

SECRET_KEY = "integration-secret-key-0123456789abcdef"
PASSWORD = "operator-password-1"


def _settings(database_url: str, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": SecretStr(database_url),
        "environment": "development",
        "dev_enrollment_token": SecretStr(TOKEN),
        "claim_poll_interval_seconds": 0.05,
        "secret_key": SecretStr(SECRET_KEY),
        "login_max_attempts": 3,
        **overrides,
    }
    return Settings(**values)


@pytest.fixture
def app_settings(database_url: str) -> Settings:
    migrate.upgrade(database_url)
    return _settings(database_url)


@pytest.fixture
def anon(app_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(app_settings)) as c:
        yield c


@pytest.fixture
def admin(anon: TestClient) -> TestClient:
    authenticate(anon)
    return anon


def _job(client: TestClient, name: str = "job", **spec: Any) -> dict[str, Any]:
    r = client.post(
        "/api/v1/jobs",
        json={"name": name, "spec": {"runtime": "shell", "script": "echo hi", **spec}},
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------- authentication


def test_everything_user_facing_requires_sign_in(anon: TestClient) -> None:
    assert anon.get("/healthz").status_code == 200
    assert anon.get("/api/v1/system/info").status_code == 200
    for path in (
        "/api/v1/jobs",
        "/api/v1/runs",
        "/api/v1/agents",
        "/api/v1/schedules",
        "/api/v1/workflows",
        "/api/v1/queues",
        "/api/v1/enrollment-tokens",
        "/api/v1/auth/me",
    ):
        assert anon.get(path).status_code == 401, path
    status = anon.get("/api/v1/auth/status").json()
    assert status == {"setup_required": True, "user": None}


def test_first_run_setup_happens_once(anon: TestClient) -> None:
    anon.headers.update(CLIENT_HEADER)
    weak = anon.post("/api/v1/auth/setup", json={"email": "a@b.co", "password": "short"})
    assert weak.status_code == 422
    authenticate(anon)
    me = anon.get("/api/v1/auth/me").json()
    assert me["email"] == ADMIN_EMAIL and me["role"] == "admin"
    again = anon.post("/api/v1/auth/setup", json={"email": "x@y.co", "password": "another-long-pw"})
    assert again.status_code == 409
    assert anon.get("/api/v1/auth/status").json()["setup_required"] is False


def test_session_cookie_is_hardened(anon: TestClient) -> None:
    anon.headers.update(CLIENT_HEADER)
    r = anon.post("/api/v1/auth/setup", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert ADMIN_PASSWORD not in r.text


def test_login_logout_and_throttling(admin: TestClient) -> None:
    c = TestClient(admin.app)
    c.headers.update(CLIENT_HEADER)
    assert (
        c.post(
            "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-nope"}
        ).status_code
        == 401
    )
    # Unknown users get the same answer (no account enumeration).
    unknown = c.post("/api/v1/auth/login", json={"email": "who@example.com", "password": "x" * 12})
    assert (
        unknown.status_code == 401
        and unknown.json()
        == c.post(
            "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "nope-nope-nope"}
        ).json()
    )
    ok = c.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL.upper(), "password": ADMIN_PASSWORD}
    )
    assert ok.status_code == 200
    assert c.get("/api/v1/jobs").status_code == 200
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert c.get("/api/v1/jobs").status_code == 401

    # login_max_attempts=3 failures per (ip, email) -> throttled, even for the right password.
    for _ in range(3):
        c.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-password"})
    limited = c.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert limited.status_code == 429 and "Retry-After" in limited.headers


def test_cookie_writes_need_the_csrf_header(admin: TestClient) -> None:
    bare = TestClient(admin.app, cookies=admin.cookies)
    r = bare.post("/api/v1/jobs", json={"name": "j", "spec": {"runtime": "shell", "script": "x"}})
    assert r.status_code == 403 and "CSRF" in r.json()["detail"]
    assert bare.get("/api/v1/jobs").status_code == 200  # reads are fine
    no_header_login = bare.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert no_header_login.status_code == 403


def test_password_change_signs_out_other_sessions(admin: TestClient) -> None:
    other = TestClient(admin.app)
    other.headers.update(CLIENT_HEADER)
    other.post("/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert other.get("/api/v1/auth/me").status_code == 200
    bad = admin.post(
        "/api/v1/auth/password",
        json={"current_password": "wrong", "new_password": "a-new-password"},
    )
    assert bad.status_code == 422
    r = admin.post(
        "/api/v1/auth/password",
        json={"current_password": ADMIN_PASSWORD, "new_password": "a-new-password"},
    )
    assert r.status_code == 204
    assert admin.get("/api/v1/auth/me").status_code == 200  # this session stays
    assert other.get("/api/v1/auth/me").status_code == 401


# ---------------------------------------------------------------- roles


# ---------------------------------------------------------------- API tokens


def test_api_tokens(admin: TestClient) -> None:
    created = admin.post("/api/v1/auth/tokens", json={"name": "ci"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert token.startswith("tqt_")
    listed = admin.get("/api/v1/auth/tokens").json()
    assert [t["name"] for t in listed] == ["ci"] and token not in str(listed)

    bot = TestClient(admin.app)  # no cookie, no CSRF header: bearer tokens don't need one
    auth = {"Authorization": f"Bearer {token}"}
    assert bot.get("/api/v1/auth/me", headers=auth).json()["via"] == "token"
    assert (
        bot.post(
            "/api/v1/jobs",
            headers=auth,
            json={"name": "via-token", "spec": {"runtime": "shell", "script": "x"}},
        ).status_code
        == 201
    )
    assert (
        bot.get("/api/v1/jobs", headers={"Authorization": "Bearer tqt_forged"}).status_code == 401
    )

    assert admin.delete(f"/api/v1/auth/tokens/{created.json()['id']}").status_code == 204
    assert bot.get("/api/v1/jobs", headers=auth).status_code == 401


# ---------------------------------------------------------------- audit


# ---------------------------------------------------------------- secrets


# ---------------------------------------------------------------- bootstrap, metrics, settings


def test_bootstrap_admin_from_environment(database_url: str) -> None:
    migrate.upgrade(database_url)
    settings = _settings(
        database_url,
        bootstrap_admin_email="Ops@Example.com",
        bootstrap_admin_password=SecretStr("bootstrap-password"),
    )
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/v1/auth/status").json()["setup_required"] is False
        c.headers.update(CLIENT_HEADER)
        r = c.post(
            "/api/v1/auth/login",
            json={"email": "ops@example.com", "password": "bootstrap-password"},
        )
        assert r.status_code == 200 and r.json()["role"] == "admin"


def test_metrics(admin: TestClient, database_url: str) -> None:
    job = _job(admin)
    admin.post(f"/api/v1/jobs/{job['id']}/runs")
    body = admin.get("/metrics").text
    assert 'torqrun_runs{status="QUEUED"} 1.0' in body
    assert 'torqrun_queue_depth{queue="default"} 1.0' in body
    assert "torqrun_http_requests_total" in body
    with TestClient(create_app(_settings(database_url, metrics_token=SecretStr("m-token")))) as c:
        assert c.get("/metrics").status_code == 401
        assert c.get("/metrics", headers={"Authorization": "Bearer m-token"}).status_code == 200


def test_production_settings_are_guarded(database_url: str) -> None:
    with pytest.raises(ValueError, match="https"):
        Settings(
            database_url=SecretStr(database_url),
            environment="production",
            public_url="http://torqrun.example.com",
        )
    with pytest.raises(ValueError, match="only allowed in development"):
        Settings(
            database_url=SecretStr(database_url),
            environment="production",
            secret_key=SecretStr(SECRET_KEY),
            dev_enrollment_token=SecretStr(TOKEN),
        )
    prod = Settings(
        database_url=SecretStr(database_url),
        environment="production",
        secret_key=SecretStr(SECRET_KEY),
    )
    assert prod.secure_cookies is True


# ---------------------------------------------------------------- Community Edition limits


def test_community_edition_has_no_paid_features(admin: TestClient) -> None:
    info = admin.get("/api/v1/system/info").json()
    assert info["edition"] == "community" and info["features"] == [] and info["license"] is None
    # Paid APIs don't exist here …
    for path in (
        "/api/v1/users",
        "/api/v1/secrets",
        "/api/v1/audit",
        "/api/v1/notification-channels",
    ):
        assert admin.get(path).status_code == 404, path
    # … and a job that needs the secrets manager explains how to get it.
    r = admin.post(
        "/api/v1/jobs",
        json={
            "name": "needs-secret",
            "spec": {"runtime": "shell", "script": "x", "secrets": {"A": "a"}},
        },
    )
    assert r.status_code == 402
    assert (
        "Secrets manager" in r.json()["detail"] and "anshulshrm12@gmail.com" in r.json()["detail"]
    )
