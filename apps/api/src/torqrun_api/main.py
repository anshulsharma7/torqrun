"""Application factory."""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from torqrun_api import (
    __version__,
    metrics,
    routes_agent,
    routes_agents,
    routes_artifacts,
    routes_auth,
    routes_fleet,
    routes_health,
    routes_install,
    routes_jobs,
    routes_queues,
    routes_runs,
    routes_schedules,
    routes_workflows,
)
from torqrun_api.edition import load_extensions
from torqrun_api.lifecycle import shutting_down
from torqrun_api.logging import configure_logging
from torqrun_api.metrics import MetricsMiddleware
from torqrun_api.middleware import RequestContextMiddleware
from torqrun_api.routes_auth import SETUP_LOCK
from torqrun_api.security import LoginLimiter, access, hash_password
from torqrun_api.settings import Settings
from torqrun_db import create_engine
from torqrun_db import runs as ops
from torqrun_db.models import User

logger = logging.getLogger("torqrun_api")

REPLICA_ID = uuid.uuid4().hex
BEAT_INTERVAL_SECONDS = 2.0


async def _control_plane_heartbeat(app: FastAPI, maker: async_sessionmaker[AsyncSession]) -> None:
    """Tell the scheduler this API replica is serving agents (see ops.control_plane_up_since).

    Stops (and withdraws the replica) as soon as shutdown begins: a replica that no longer
    accepts connections must not count as "up", or agents would be blamed for not renewing.
    """
    up_since = ops.utcnow()
    while not shutting_down(app):
        try:
            async with maker() as session, session.begin():
                await ops.control_plane_beat(session, REPLICA_ID, up_since)
        except Exception as exc:  # database briefly unavailable: keep trying
            logger.warning("control-plane heartbeat failed", extra={"error": type(exc).__name__})
        for _ in range(int(BEAT_INTERVAL_SECONDS / 0.25)):
            if shutting_down(app):
                break
            await asyncio.sleep(0.25)
    with contextlib.suppress(Exception):
        async with maker() as session, session.begin():
            await ops.control_plane_remove(session, REPLICA_ID)
    logger.info("api shutting down: stopped serving agents")


async def _housekeeping(maker: async_sessionmaker[AsyncSession], settings: Settings) -> None:
    """Hourly: delete artifacts past their retention (each replica; deletes are idempotent)."""
    while True:
        try:
            removed = await routes_artifacts.purge_expired(maker, settings)
            if removed:
                logger.info("expired artifacts deleted", extra={"count": removed})
        except Exception as exc:
            logger.warning("artifact cleanup failed", extra={"error": type(exc).__name__})
        await asyncio.sleep(3600)


async def _bootstrap_admin(settings: Settings, maker: async_sessionmaker[AsyncSession]) -> None:
    """Create the configured admin if there are no users yet (unattended installs)."""
    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return
    email = settings.bootstrap_admin_email.strip().lower()
    async with maker() as session, session.begin():
        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": SETUP_LOCK})
        if (await session.execute(select(func.count()).select_from(User))).scalar_one():
            return
        session.add(
            User(
                email=email,
                name=email.split("@")[0],
                role="admin",
                password_hash=hash_password(settings.bootstrap_admin_password.get_secret_value()),
                disabled=False,
                created_at=ops.utcnow(),
            )
        )
    logger.info("bootstrap admin created", extra={"email": email})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # values come from the environment
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
        )
        # expire_on_commit=False: objects stay readable after commit (no implicit async reloads).
        app.state.sessionmaker = async_sessionmaker(app.state.engine, expire_on_commit=False)
        app.state.login_limiter = LoginLimiter(
            settings.login_max_attempts, settings.login_window_seconds
        )
        await _bootstrap_admin(settings, app.state.sessionmaker)
        housekeeping = asyncio.create_task(_housekeeping(app.state.sessionmaker, settings))
        beat = asyncio.create_task(_control_plane_heartbeat(app, app.state.sessionmaker))
        if settings.dev_enrollment_token is not None:
            logger.warning("development enrollment token is enabled; never use this in production")
        logger.info(
            "api starting",
            extra={
                "version": __version__,
                "environment": settings.environment,
                "edition": app.state.edition.name,
            },
        )
        try:
            yield
        finally:
            for task in (beat, housekeeping):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            with contextlib.suppress(Exception):
                async with app.state.sessionmaker() as session, session.begin():
                    await ops.control_plane_remove(session, REPLICA_ID)
            await app.state.engine.dispose()
            logger.info("api stopped")

    app = FastAPI(
        title="Torqrun API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
    )
    # Unauthenticated: probes, the agent protocol (agent credentials), installer, sign-in.
    for module in (
        routes_health,
        routes_agent,
        routes_install,
        routes_auth,
    ):
        app.include_router(module.router)
    app.include_router(routes_artifacts.agent_router)  # agent credentials
    operator = [Depends(access(write="operator"))]
    admin = [Depends(access(read="admin", write="admin"))]
    for module in (routes_jobs, routes_runs, routes_queues, routes_schedules, routes_workflows):
        app.include_router(module.router, dependencies=operator)
    app.include_router(routes_artifacts.router, dependencies=operator)
    app.include_router(routes_agents.router, dependencies=[Depends(access(write="admin"))])
    app.include_router(routes_fleet.router, dependencies=admin)
    app.include_router(metrics.router)

    @app.exception_handler(OperationalError)
    async def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        # Connection refused, "too many clients", failover…: transient. 503 + Retry-After makes
        # agents and well-behaved clients retry instead of treating it as a hard failure.
        logger.error(
            "database unavailable",
            extra={"path": request.url.path, "error": str(exc.orig).splitlines()[0][:200]},
        )
        return JSONResponse(
            {"detail": "database temporarily unavailable"},
            status_code=503,
            headers={"Retry-After": "2"},
        )

    # Paid features (Torqrun Enterprise), when installed and licensed. Added before the
    # outer middleware so anything it installs runs inside request-ID and metrics handling.
    app.state.edition = load_extensions(app, settings)
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)

    return app
