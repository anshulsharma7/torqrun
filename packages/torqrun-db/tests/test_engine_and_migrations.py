import pytest

from torqrun_db.engine import normalize_url
from torqrun_db.migrate import alembic_config, head_revision


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db:5432/fh",
        "postgres://u:p@db:5432/fh",
        "postgresql+asyncpg://u:p@db:5432/fh",
        "postgresql+psycopg://u:p@db:5432/fh",
    ],
)
def test_normalize_url_forces_psycopg_driver(url: str) -> None:
    assert normalize_url(url) == "postgresql+psycopg://u:p@db:5432/fh"


def test_normalize_url_keeps_password_and_query() -> None:
    assert (
        normalize_url("postgresql://u:s%40cret@db/fh?sslmode=require")
        == "postgresql+psycopg://u:s%40cret@db/fh?sslmode=require"
    )


@pytest.mark.parametrize("url", ["sqlite:///x.db", "mysql://u:p@h/db"])
def test_normalize_url_rejects_other_backends(url: str) -> None:
    with pytest.raises(ValueError, match="requires PostgreSQL"):
        normalize_url(url)


def test_head_revision_is_latest_migration() -> None:
    assert head_revision() == "0009_notifications_artifacts"


def test_alembic_config_escapes_percent_in_url() -> None:
    cfg = alembic_config("postgresql://u:s%40cret@db/fh")
    # Round-trips through ConfigParser interpolation without raising or mangling.
    assert cfg.get_main_option("sqlalchemy.url") == "postgresql+psycopg://u:s%40cret@db/fh"
