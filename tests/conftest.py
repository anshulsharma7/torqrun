"""Fixtures for integration and e2e tests: each test gets a fresh, empty PostgreSQL database.

Set TORQRUN_TEST_DATABASE_URL to a server URL whose user may CREATE/DROP DATABASE, e.g.
``postgresql://torqrun:torqrun_dev@127.0.0.1:55432/postgres``. Tests are skipped otherwise.
"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from torqrun_db.engine import normalize_url

ADMIN_URL = os.environ.get("TORQRUN_TEST_DATABASE_URL")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "tests/integration" in str(item.path) or "tests/e2e" in str(item.path):
            item.add_marker(pytest.mark.integration)
            if not ADMIN_URL:
                item.add_marker(pytest.mark.skip(reason="TORQRUN_TEST_DATABASE_URL not set"))


@pytest.fixture
def database_url() -> Iterator[str]:
    assert ADMIN_URL is not None
    admin = create_engine(normalize_url(ADMIN_URL), isolation_level="AUTOCOMMIT")
    name = f"torqrun_test_{uuid.uuid4().hex[:12]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield (
            make_url(normalize_url(ADMIN_URL))
            .set(database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()
