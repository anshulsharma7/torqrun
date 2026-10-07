"""Schedules: run a job on a cron expression or a fixed interval."""

import uuid
from datetime import datetime
from typing import Literal, Self
from zoneinfo import available_timezones

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from torqrun_api.deps import SessionDep
from torqrun_core.schedule import ScheduleError, ScheduleSpec
from torqrun_core.states import RunStatus
from torqrun_db.models import Job, Run, Schedule, Workflow
from torqrun_db.runs import utcnow
from torqrun_db.schedules import reschedule_from_now, spec_of

router = APIRouter(prefix="/api/v1/schedules", tags=["schedules"])


class ScheduleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["cron", "interval"] = "cron"
    cron: str | None = Field(
        default=None, max_length=120, examples=["0 2 * * *", "*/15 * * * *", "@daily"]
    )
    interval_seconds: int | None = Field(default=None, ge=10, le=366 * 86400)
    timezone: str = Field(default="UTC", max_length=64, examples=["Europe/Berlin", "Asia/Kolkata"])

    @model_validator(mode="after")
    def _valid(self) -> Self:
        try:
            self.spec().validate()
        except ScheduleError as exc:
            raise ValueError(str(exc)) from None
        return self

    def spec(self) -> ScheduleSpec:
        return ScheduleSpec(
            kind=self.kind,
            cron=self.cron,
            interval_seconds=self.interval_seconds,
            timezone=self.timezone,
        )


class ScheduleIn(ScheduleDefinition):
    name: str = Field(min_length=1, max_length=120)
    job_id: uuid.UUID | None = Field(default=None, description="Run this job (or set workflow_id)")
    workflow_id: uuid.UUID | None = Field(
        default=None, description="Run this workflow (or set job_id)"
    )
    misfire_policy: Literal["skip", "run_once", "run_all"] = Field(
        default="run_once", description="What to do with slots missed while the scheduler was down"
    )
    misfire_grace_seconds: int = Field(default=60, ge=1, le=86400)
    max_catchup: int = Field(default=5, ge=1, le=100)
    overlap_policy: Literal["allow", "skip"] = Field(
        default="allow",
        description="'skip': don't start while the previous scheduled run is still active",
    )
    enabled: bool = True

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.job_id is None) == (self.workflow_id is None):
            raise ValueError("set exactly one of job_id or workflow_id")
        return self


class ScheduleOut(BaseModel):
    id: uuid.UUID
    name: str
    job_id: uuid.UUID | None
    workflow_id: uuid.UUID | None
    target: Literal["job", "workflow"]
    job_name: str = Field(description="Name of the target job or workflow")
    kind: str
    cron: str | None
    interval_seconds: int | None
    timezone: str
    misfire_policy: str
    misfire_grace_seconds: int
    max_catchup: int
    overlap_policy: str
    enabled: bool
    next_fire_at: datetime | None
    upcoming: list[datetime]
    last_fired_at: datetime | None
    last_run_id: uuid.UUID | None
    last_run_status: RunStatus | None
    skipped_count: int
    last_skipped_at: datetime | None
    last_skip_reason: str | None
    created_at: datetime


class PreviewIn(ScheduleDefinition):
    count: int = Field(default=5, ge=1, le=20)


class PreviewOut(BaseModel):
    next: list[datetime]


async def _out(session: SessionDep, s: Schedule) -> ScheduleOut:
    target = (
        await session.get(Job, s.job_id) if s.job_id else await session.get(Workflow, s.workflow_id)
    )
    last_status = None
    if s.last_run_id:
        last_status = (
            await session.execute(select(Run.status).where(Run.id == s.last_run_id))
        ).scalar_one_or_none()
    upcoming = spec_of(s).upcoming(s.next_fire_at, 2) if s.enabled and s.next_fire_at else []
    return ScheduleOut(
        id=s.id,
        name=s.name,
        job_id=s.job_id,
        workflow_id=s.workflow_id,
        target="workflow" if s.workflow_id else "job",
        job_name=target.name if target else "?",
        kind=s.kind,
        cron=s.cron,
        interval_seconds=s.interval_seconds,
        timezone=s.timezone,
        misfire_policy=s.misfire_policy,
        misfire_grace_seconds=s.misfire_grace_seconds,
        max_catchup=s.max_catchup,
        overlap_policy=s.overlap_policy,
        enabled=s.enabled,
        next_fire_at=s.next_fire_at if s.enabled else None,
        upcoming=([s.next_fire_at, *upcoming] if s.enabled and s.next_fire_at else []),
        last_fired_at=s.last_fired_at,
        last_run_id=s.last_run_id,
        last_run_status=RunStatus(last_status) if last_status else None,
        skipped_count=s.skipped_count,
        last_skipped_at=s.last_skipped_at,
        last_skip_reason=s.last_skip_reason,
        created_at=s.created_at,
    )


async def _get(session: SessionDep, schedule_id: uuid.UUID, *, lock: bool = False) -> Schedule:
    s = await session.get(Schedule, schedule_id, with_for_update=lock)
    if s is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="schedule not found")
    return s


def _apply(s: Schedule, body: ScheduleIn) -> None:
    for field in (
        "name",
        "kind",
        "cron",
        "interval_seconds",
        "timezone",
        "misfire_policy",
        "misfire_grace_seconds",
        "max_catchup",
        "overlap_policy",
        "enabled",
    ):
        setattr(s, field, getattr(body, field))
    if body.kind == "cron":
        s.interval_seconds = None
    else:
        s.cron = None


@router.get("/timezones")
async def list_timezones() -> list[str]:
    """IANA time zones this server accepts (the same database it validates against)."""
    regional = (
        z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/"))
    )
    return ["UTC", *sorted(regional)]


@router.post("/preview")
async def preview(body: PreviewIn) -> PreviewOut:
    """Next fire times for a definition (validates it, too). Nothing is saved."""
    return PreviewOut(next=body.spec().upcoming(utcnow(), body.count))


@router.get("")
async def list_schedules(
    session: SessionDep, job_id: uuid.UUID | None = None, workflow_id: uuid.UUID | None = None
) -> list[ScheduleOut]:
    stmt = select(Schedule).order_by(Schedule.enabled.desc(), Schedule.next_fire_at)
    if job_id:
        stmt = stmt.where(Schedule.job_id == job_id)
    if workflow_id:
        stmt = stmt.where(Schedule.workflow_id == workflow_id)
    return [await _out(session, s) for s in (await session.execute(stmt)).scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleIn, session: SessionDep) -> ScheduleOut:
    async with session.begin():
        if body.job_id and await session.get(Job, body.job_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job not found")
        if body.workflow_id and await session.get(Workflow, body.workflow_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workflow not found")
        s = Schedule(
            job_id=body.job_id, workflow_id=body.workflow_id, created_at=utcnow(), skipped_count=0
        )
        _apply(s, body)
        reschedule_from_now(s)
        session.add(s)
    return await _out(session, s)


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: uuid.UUID, session: SessionDep) -> ScheduleOut:
    return await _out(session, await _get(session, schedule_id))


@router.put("/{schedule_id}")
async def update_schedule(
    schedule_id: uuid.UUID, body: ScheduleIn, session: SessionDep
) -> ScheduleOut:
    async with session.begin():
        s = await _get(session, schedule_id, lock=True)
        if body.job_id != s.job_id:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="a schedule cannot move to another job",
            )
        _apply(s, body)
        reschedule_from_now(s)  # changed timing applies from now on; no burst of old slots
    return await _out(session, s)


@router.post("/{schedule_id}/pause")
async def pause_schedule(schedule_id: uuid.UUID, session: SessionDep) -> ScheduleOut:
    async with session.begin():
        s = await _get(session, schedule_id, lock=True)
        s.enabled = False
    return await _out(session, s)


@router.post("/{schedule_id}/resume")
async def resume_schedule(schedule_id: uuid.UUID, session: SessionDep) -> ScheduleOut:
    """Resume from now: slots that passed while paused are not run."""
    async with session.begin():
        s = await _get(session, schedule_id, lock=True)
        s.enabled = True
        reschedule_from_now(s)
    return await _out(session, s)


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: uuid.UUID, session: SessionDep) -> Response:
    async with session.begin():
        await session.delete(await _get(session, schedule_id, lock=True))
    return Response(status_code=status.HTTP_204_NO_CONTENT)
