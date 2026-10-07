from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import migrate


def _client(url: str) -> TestClient:
    return TestClient(create_app(Settings(database_url=SecretStr(url), environment="test")))


def test_readyz_reports_pending_migrations_on_empty_database(database_url: str) -> None:
    with _client(database_url) as client:
        r = client.get("/readyz")
    assert r.status_code == 503
    db = r.json()["database"]
    assert db["status"] == "migrations_pending"
    assert db["schema_revision"] is None


def test_readyz_is_ready_after_upgrade(database_url: str) -> None:
    migrate.upgrade(database_url)
    with _client(database_url) as client:
        r = client.get("/readyz")
        info = client.get("/api/v1/system/info").json()
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert r.json()["database"]["schema_revision"] == migrate.head_revision()
    assert info["database"]["status"] == "ok"


def test_migrations_round_trip(database_url: str) -> None:
    migrate.upgrade(database_url)
    migrate.downgrade(database_url, "base")
    migrate.upgrade(database_url)
    engine = create_engine(database_url)
    try:
        with engine.connect() as conn:
            assert migrate.current_revision(conn) == migrate.head_revision()
            assert "alembic_version" in inspect(conn).get_table_names()
    finally:
        engine.dispose()


def test_upgrade_is_idempotent(database_url: str) -> None:
    migrate.upgrade(database_url)
    migrate.upgrade(database_url)
