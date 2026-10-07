"""Schedule persistence and firing.

Firing is exactly-once per slot: the schedule row is locked (``FOR UPDATE SKIP LOCKED``, so
scheduler replicas split the work) and ``runs(schedule_id, scheduled_for)`` is unique, so even a
replayed or concurrent evaluation cannot create a second run for the same slot.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_core.schedule import ScheduleSpec, due_fires
from torqrun_core.states import TERMINAL
from torqrun_db import workflows
from torqrun_db.models import Job, JobVersion, Run, Schedule, Workflow, WorkflowRun, WorkflowVersion
from torqrun_db.runs import create_run, utcnow


def spec_of(s: Schedule) -> ScheduleSpec:
    return ScheduleSpec(
        kind=s.kind,  # type: ignore[arg-type]
        cron=s.cron,
        interval_seconds=s.interval_seconds,
        timezone=s.timezone,
        anchor=s.created_at,
    )


def reschedule_from_now(s: Schedule, now: datetime | None = None) -> None:
    """Next slot strictly in the future (used on create, edit and resume: no burst of old slots)."""
    s.next_fire_at = spec_of(s).next_after(now or utcnow())


@dataclass(frozen=True)
class Fired:
    schedule: Schedule
    runs: list[Run | WorkflowRun]
    skipped: int


async def fire_due(
    session: AsyncSession, *, limit: int = 50, now: datetime | None = None
) -> list[Fired]:
    now = now or utcnow()
    due = (
        (
            await session.execute(
                select(Schedule)
                .where(Schedule.enabled.is_(True), Schedule.next_fire_at <= now)
                .order_by(Schedule.next_fire_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    out: list[Fired] = []
    for sch in due:
        assert sch.next_fire_at is not None  # noqa: S101 - filtered above
        plan = due_fires(
            spec_of(sch),
            sch.next_fire_at,
            now,
            policy=sch.misfire_policy,  # type: ignore[arg-type]
            grace=timedelta(seconds=sch.misfire_grace_seconds),
            max_catchup=sch.max_catchup,
        )
        created: list[Run | WorkflowRun] = []
        skipped = plan.skipped
        reasons: list[str] = []
        if plan.skipped:
            reasons.append(
                f"{plan.skipped} missed slot(s) dropped by misfire policy {sch.misfire_policy}"
            )
        for slot in plan.fire:
            if sch.overlap_policy == "skip" and await _has_active_run(session, sch):
                skipped += 1
                reasons.append("previous run still active (overlap policy: skip)")
                continue
            try:
                async with session.begin_nested():
                    made = await _start_target(session, sch, slot)
            except IntegrityError:
                continue  # this slot already has its run (replayed evaluation)
            if made is None:
                skipped += 1
                reasons.append("target job or workflow no longer exists")
                continue
            created.append(made)
        sch.next_fire_at = plan.next_fire_at
        if created:
            sch.last_fired_at = created[-1].scheduled_for
            if isinstance(created[-1], Run):
                sch.last_run_id = created[-1].id
        if skipped:
            sch.skipped_count += skipped
            sch.last_skipped_at = now
            sch.last_skip_reason = "; ".join(dict.fromkeys(reasons))
        out.append(Fired(schedule=sch, runs=created, skipped=skipped))
    return out


async def _has_active_run(session: AsyncSession, sch: Schedule) -> bool:
    if sch.workflow_id is not None:
        stmt = select(WorkflowRun.id).where(
            WorkflowRun.schedule_id == sch.id, WorkflowRun.status == "RUNNING"
        )
    else:
        stmt = select(Run.id).where(
            Run.schedule_id == sch.id, Run.status.not_in([s.value for s in TERMINAL])
        )
    return (await session.execute(stmt.limit(1))).first() is not None


async def _start_target(
    session: AsyncSession, sch: Schedule, slot: datetime
) -> Run | WorkflowRun | None:
    reason = f"schedule {sch.name!r} slot {slot.isoformat()}"
    if sch.workflow_id is not None:
        wf = await session.get(Workflow, sch.workflow_id)
        if wf is None:
            return None
        version = (
            await session.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == wf.id,
                    WorkflowVersion.version == wf.current_version,
                )
            )
        ).scalar_one()
        wr = await workflows.start(
            session,
            wf,
            version,
            trigger="schedule",
            actor=f"schedule:{sch.name}",
            schedule_id=sch.id,
            scheduled_for=slot,
        )
        await session.flush()
        return wr
    job = await session.get(Job, sch.job_id)
    if job is None:
        return None
    version_row = (
        await session.execute(
            select(JobVersion).where(
                JobVersion.job_id == job.id, JobVersion.version == job.current_version
            )
        )
    ).scalar_one()
    return await create_run(
        session,
        job,
        version_row,
        trigger="schedule",
        actor=f"schedule:{sch.name}",
        schedule_id=sch.id,
        scheduled_for=slot,
        reason=reason,
    )
