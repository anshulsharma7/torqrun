"""Dependency checks shared by ``/readyz`` and the system-info endpoint."""

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from torqrun_db.migrate import current_revision, head_revision

logger = logging.getLogger(__name__)


class DatabaseCheck(BaseModel):
    status: Literal["ok", "unreachable", "migrations_pending"]
    schema_revision: str | None
    expected_revision: str | None


async def check_database(engine: AsyncEngine, timeout_seconds: float) -> DatabaseCheck:
    """Report whether the database is reachable and migrated to this build's head revision.

    A schema behind *or* ahead of this build counts as not ready: serving traffic against a
    schema the code doesn't match is how silent data corruption happens during upgrades.
    """
    expected = head_revision()
    try:
        async with asyncio.timeout(timeout_seconds), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            current = await conn.run_sync(current_revision)
    except Exception as exc:
        # Details go to the log only; the HTTP response must not leak hosts or credentials.
        logger.warning(
            "database check failed", extra={"error": type(exc).__name__, "detail": str(exc)}
        )
        return DatabaseCheck(status="unreachable", schema_revision=None, expected_revision=expected)
    status: Literal["ok", "migrations_pending"] = (
        "ok" if current == expected else "migrations_pending"
    )
    return DatabaseCheck(status=status, schema_revision=current, expected_revision=expected)
