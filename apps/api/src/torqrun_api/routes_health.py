"""Health and system endpoints."""

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import func, select

from torqrun_api import __version__
from torqrun_api.deps import EngineDep, SettingsDep
from torqrun_api.health import DatabaseCheck, check_database
from torqrun_db.models import Agent
from torqrun_db.runs import utcnow

router = APIRouter()


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    database: DatabaseCheck


class SystemInfo(BaseModel):
    name: Literal["torqrun"] = "torqrun"
    version: str
    edition: str = "community"
    features: list[str] = []  # paid features enabled by the license
    license: dict[str, object] | None = None  # customer, plan, seats, expires_at, state
    environment: str
    database: DatabaseCheck
    agents_online: int | None = None  # None when the database is not usable


@router.get("/healthz", tags=["health"])
async def healthz() -> Liveness:
    """Liveness: the process is up and serving. Never touches dependencies."""
    return Liveness()


@router.get(
    "/readyz",
    tags=["health"],
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": Readiness}},
)
async def readyz(response: Response, engine: EngineDep, settings: SettingsDep) -> Readiness:
    """Readiness: database reachable and schema at this build's migration head."""
    db = await check_database(engine, settings.readiness_timeout_seconds)
    ready = db.status == "ok"
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ready" if ready else "not_ready", database=db)


@router.get("/api/v1/system/info", tags=["system"])
async def system_info(request: Request, engine: EngineDep, settings: SettingsDep) -> SystemInfo:
    """Public: version, database state and how many agents are connected (no names)."""
    db = await check_database(engine, settings.readiness_timeout_seconds)
    online = None
    if db.status == "ok":
        since = utcnow() - timedelta(seconds=settings.agent_offline_after_seconds)
        async with engine.connect() as conn:
            online = (
                await conn.execute(
                    select(func.count())
                    .select_from(Agent)
                    .where(Agent.status != "REVOKED", Agent.last_seen_at >= since)
                )
            ).scalar_one()
    edition = request.app.state.edition
    return SystemInfo(
        version=__version__,
        edition=edition.name,
        features=edition.features(),
        license=edition.license_summary(),
        environment=settings.environment,
        database=db,
        agents_online=online,
    )
