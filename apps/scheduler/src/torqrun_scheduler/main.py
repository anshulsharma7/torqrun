"""Background maintenance loop.

Every tick runs a few short, independent transactions. Each one selects its work with
``FOR UPDATE SKIP LOCKED``, so any number of scheduler replicas can run side by side without
coordination and without doing the same work twice.

* requeue dispatched-but-unacknowledged attempts (safe: agents ack before spawning)
* reap expired leases: RUNNING/STARTING -> LOST, CANCEL_REQUESTED -> CANCELLED. Paused while
  no API replica has been up for a full lease period: during a control-plane outage agents
  cannot renew, and their runs must not be declared lost for it.
* promote due retries: RETRY_WAIT -> QUEUED with a new attempt
* fire due schedules (cron / interval), honouring misfire and overlap policies
* advance running workflows: start tasks whose dependencies finished, skip, finalise
* extension steps (Torqrun Enterprise: notifications)
* purge old idempotency keys and expired sign-in sessions (every few minutes)
"""

import asyncio
import contextlib
import importlib.util
import logging
import signal
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from torqrun_core.logs import configure_logging
from torqrun_db import create_engine, schedules, workflows
from torqrun_db import runs as ops
from torqrun_db.models import UserSession

logger = logging.getLogger("torqrun_scheduler")


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TORQRUN_", extra="ignore")

    database_url: SecretStr
    scheduler_interval_seconds: float = Field(default=1.0, gt=0)
    dispatch_ack_timeout_seconds: float = Field(default=30.0, ge=1)
    # Must match the API's lease TTL: the reaper waits this long after the control plane came
    # back before declaring silent agents lost.
    lease_ttl_seconds: float = Field(default=60.0, ge=1)
    idempotency_ttl_hours: float = Field(default=24.0, gt=0)
    scheduler_heartbeat_file: Path = Path("/tmp/torqrun-scheduler.alive")  # noqa: S108 - liveness marker only
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"


Step = Callable[[AsyncSession], Awaitable[int]]

PURGE_EVERY_SECONDS = 300.0


class Scheduler:
    def __init__(
        self, settings: SchedulerSettings, maker: async_sessionmaker[AsyncSession]
    ) -> None:
        self.settings = settings
        self.maker = maker
        self._stop = asyncio.Event()
        self._last_purge = 0.0
        self._reaping_paused = False
        ack = timedelta(seconds=settings.dispatch_ack_timeout_seconds)
        self.steps: dict[str, Step] = {
            "requeue_unacked": lambda s: _count(ops.requeue_unacked(s, older_than=ack)),
            "reap_expired_leases": self._reap,
            "promote_due_retries": lambda s: _count(ops.promote_due_retries(s)),
            "fire_schedules": self._fire_schedules,
            "advance_workflows": workflows.advance_running,
        }
        # Extension points (used by Torqrun Enterprise): steps that run every few minutes,
        # work done after each tick outside any transaction, and cleanup on stop.
        self.periodic_steps: dict[str, Step] = {}
        self.after_tick: dict[str, Callable[[], Awaitable[int]]] = {}
        self.on_stop: list[Callable[[], Awaitable[None]]] = []

    async def _reap(self, session: AsyncSession) -> int:
        """Reap expired leases, but never blame agents for a control-plane outage."""
        up_since = await ops.control_plane_up_since(session)
        grace = timedelta(seconds=self.settings.lease_ttl_seconds)
        if up_since is None or ops.utcnow() - up_since < grace:
            if not self._reaping_paused:
                logger.warning(
                    "lease reaping paused: no API has been up for a full lease period yet",
                    extra={"api_up_since": str(up_since) if up_since else None},
                )
            self._reaping_paused = True
            return 0
        if self._reaping_paused:
            logger.info("lease reaping resumed")
        self._reaping_paused = False
        return len(await ops.reap_expired_leases(session))

    async def _fire_schedules(self, session: AsyncSession) -> int:
        fired = await schedules.fire_due(session)
        for f in fired:
            if f.runs or f.skipped:
                logger.info(
                    "schedule fired",
                    extra={"schedule": f.schedule.name, "runs": len(f.runs), "skipped": f.skipped},
                )
        return sum(len(f.runs) for f in fired)

    def stop(self) -> None:
        self._stop.set()

    async def tick(self) -> dict[str, int]:
        """Run every step once. A failing step is logged and does not block the others."""
        done: dict[str, int] = {}
        steps = dict(self.steps)
        if time.monotonic() - self._last_purge >= PURGE_EVERY_SECONDS:
            ttl = timedelta(hours=self.settings.idempotency_ttl_hours)
            steps["purge_idempotency_keys"] = lambda s: ops.purge_idempotency_keys(
                s, older_than=ttl
            )
            steps["purge_expired_sessions"] = _purge_sessions
            steps.update(self.periodic_steps)
            self._last_purge = time.monotonic()
        for name, step in steps.items():
            try:
                async with self.maker() as session, session.begin():
                    n = await step(session)
            except Exception:
                logger.exception("scheduler step failed", extra={"step": name})
                continue
            done[name] = n
            if n:
                logger.info("scheduler step", extra={"step": name, "count": n})
        for name, work in self.after_tick.items():
            try:
                done[name] = await work()
            except Exception:
                logger.exception("scheduler step failed", extra={"step": name})
        return done

    async def run(self) -> None:
        logger.info(
            "scheduler started", extra={"interval_s": self.settings.scheduler_interval_seconds}
        )
        while not self._stop.is_set():
            await self.tick()
            with contextlib.suppress(OSError):
                self.settings.scheduler_heartbeat_file.touch()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.settings.scheduler_interval_seconds
                )
        for cleanup in self.on_stop:
            with contextlib.suppress(Exception):
                await cleanup()
        logger.info("scheduler stopped")


def load_extensions(scheduler: Scheduler) -> str:
    """Install Torqrun Enterprise's scheduler steps if the package is present (it checks its
    own license). Returns the edition name."""
    if importlib.util.find_spec("torqrun_ee") is None:
        return "community"
    from torqrun_ee.scheduler import install  # type: ignore[import-not-found,unused-ignore]

    edition: str = install(scheduler)
    return edition


async def _purge_sessions(session: AsyncSession) -> int:
    result = await session.execute(delete(UserSession).where(UserSession.expires_at < ops.utcnow()))
    return int(result.rowcount)  # type: ignore[attr-defined]


async def _count(work: Awaitable[Sequence[object]]) -> int:
    return len(await work)


async def _main(settings: SchedulerSettings) -> None:
    engine = create_engine(settings.database_url.get_secret_value(), pool_size=2, max_overflow=2)
    scheduler = Scheduler(settings, async_sessionmaker(engine, expire_on_commit=False))
    load_extensions(scheduler)
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, scheduler.stop)
    try:
        await scheduler.run()
    finally:
        await engine.dispose()


def main() -> None:
    settings = SchedulerSettings()  # values come from the environment
    configure_logging(settings.log_level, settings.log_format)
    asyncio.run(_main(settings))


if __name__ == "__main__":
    main()
