"""Queues: implicit by name (any job can name one); settings add limits and pausing."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Path
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from torqrun_api.deps import SessionDep, SettingsDep
from torqrun_api.schemas import QueueOut, QueueUpdate
from torqrun_core.states import LEASED, RunStatus
from torqrun_db.models import Agent, Job, JobVersion, QueueSettings, RunAttempt
from torqrun_db.runs import utcnow

router = APIRouter(prefix="/api/v1/queues", tags=["queues"])

QueueName = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")]


@router.get("")
async def list_queues(session: SessionDep, settings: SettingsDep) -> list[QueueOut]:
    configured = {q.name: q for q in (await session.execute(select(QueueSettings))).scalars()}
    counts: dict[tuple[str, str], int] = {
        (q, st): n
        for q, st, n in (
            await session.execute(
                select(RunAttempt.queue, RunAttempt.status, func.count())
                .where(RunAttempt.status.in_([RunStatus.QUEUED.value, *[s.value for s in LEASED]]))
                .group_by(RunAttempt.queue, RunAttempt.status)
            )
        ).all()
    }
    job_queues = set(
        (
            await session.execute(
                select(JobVersion.spec["queue"].astext).join(
                    Job, (Job.id == JobVersion.job_id) & (Job.current_version == JobVersion.version)
                )
            )
        ).scalars()
    )
    cutoff = utcnow() - timedelta(seconds=settings.agent_offline_after_seconds)
    agents = (
        (await session.execute(select(Agent).where(Agent.last_seen_at >= cutoff))).scalars().all()
    )
    names = sorted(
        set(configured) | {q for q, _ in counts} | {q for q in job_queues if q} | {"default"}
    )
    out: list[QueueOut] = []
    for name in names:
        cfg = configured.get(name)
        out.append(
            QueueOut(
                name=name,
                max_concurrency=cfg.max_concurrency if cfg else None,
                paused=cfg.paused if cfg else False,
                queued=counts.get((name, RunStatus.QUEUED.value), 0),
                running=sum(counts.get((name, s.value), 0) for s in LEASED),
                agents_online=sum(
                    1
                    for a in agents
                    if name in a.queues and a.status not in ("DRAINING", "REVOKED")
                ),
            )
        )
    return out


@router.put("/{name}")
async def update_queue(
    name: QueueName, body: QueueUpdate, session: SessionDep, settings: SettingsDep
) -> QueueOut:
    """Set a queue's concurrency limit (null = unlimited) and paused flag. Paused queues keep
    accepting runs but nothing is dispatched from them; running work is not interrupted."""
    async with session.begin():
        await session.execute(
            insert(QueueSettings)
            .values(name=name, max_concurrency=body.max_concurrency, paused=body.paused)
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "max_concurrency": body.max_concurrency,
                    "paused": body.paused,
                    "updated_at": utcnow(),
                },
            )
        )
    return next(q for q in await list_queues(session, settings) if q.name == name)
