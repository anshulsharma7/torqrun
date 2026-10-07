"""Async engine construction."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

SUPPORTED_DRIVER = "postgresql+psycopg"


def normalize_url(url: str) -> str:
    """Return ``url`` using the psycopg 3 driver, which serves both sync and async engines.

    Accepts the common ``postgres://`` and ``postgresql://`` spellings so operators can paste
    a URL from their hosting provider unchanged. Any other backend is rejected: PostgreSQL is
    the only supported metadata store (SKIP LOCKED and LISTEN/NOTIFY are required).
    """
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if backend not in ("postgresql", "postgres"):
        raise ValueError(f"unsupported database backend {backend!r}; Torqrun requires PostgreSQL")
    return parsed.set(drivername=SUPPORTED_DRIVER).render_as_string(hide_password=False)


def create_engine(url: str, *, pool_size: int = 10, max_overflow: int = 10) -> AsyncEngine:
    return create_async_engine(
        normalize_url(url),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        # Fail fast instead of hanging a request when the database is unreachable.
        connect_args={"connect_timeout": 5},
    )
