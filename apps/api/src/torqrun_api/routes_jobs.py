"""Jobs: definitions with immutable versions, and triggering runs.

These endpoints are unauthenticated until M6; settings refuse to start outside development.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from torqrun_api.deps import SessionDep
from torqrun_api.edition import require_feature
from torqrun_api.idempotency import idempotent, validate_key
from torqrun_api.runs_view import run_out
from torqrun_api.schemas import (
    JobCreate,
    JobOut,
    JobUpdate,
    JobVersionOut,
    Page,
    RunBrief,
    RunOut,
)
from torqrun_api.security import ActorDep
from torqrun_db import runs as ops
from torqrun_db.models import Job, JobVersion, Run

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


async def _get_job(session: SessionDep, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


async def _current_version(session: SessionDep, job: Job) -> JobVersion:
    return (
        await session.execute(
            select(JobVersion).where(
                JobVersion.job_id == job.id, JobVersion.version == job.current_version
            )
        )
    ).scalar_one()


async def _job_out(session: SessionDep, job: Job) -> JobOut:
    version = await _current_version(session, job)
    recent = (
        (
            await session.execute(
                select(Run).where(Run.job_id == job.id).order_by(Run.created_at.desc()).limit(12)
            )
        )
        .scalars()
        .all()
    )
    last = recent[0] if recent else None
    return JobOut(
        id=job.id,
        name=job.name,
        description=job.description,
        current_version=job.current_version,
        spec=version.spec,  # dict, validated into JobSpec
        created_at=job.created_at,
        updated_at=job.updated_at,
        last_run=RunBrief.model_validate(last) if last else None,
        recent_runs=[RunBrief.model_validate(r) for r in recent],
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, request: Request, session: SessionDep) -> JobOut:
    if body.spec.secrets:
        require_feature(request, "secrets")
    try:
        async with session.begin():
            job, _ = await ops.create_job(
                session, name=body.name, description=body.description, spec=body.spec.model_dump()
            )
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"a job named {body.name!r} already exists"
        ) from None
    await session.refresh(job)
    return await _job_out(session, job)


@router.get("")
async def list_jobs(
    session: SessionDep,
    q: str | None = Query(default=None, max_length=63, description="Substring of the job name"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[JobOut]:
    stmt = select(Job)
    if q:
        stmt = stmt.where(Job.name.contains(q, autoescape=True))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    jobs = (
        (await session.execute(stmt.order_by(Job.name).limit(limit).offset(offset))).scalars().all()
    )
    items = [await _job_out(session, j) for j in jobs]
    return Page(items=items, total=total, limit=limit, offset=offset)


@router.get("/{job_id}")
async def get_job(job_id: uuid.UUID, session: SessionDep) -> JobOut:
    return await _job_out(session, await _get_job(session, job_id))


@router.put("/{job_id}")
async def update_job(
    job_id: uuid.UUID, body: JobUpdate, request: Request, session: SessionDep
) -> JobOut:
    """Edit a job. A changed spec creates a new immutable version; past runs keep theirs."""
    if body.spec.secrets:
        require_feature(request, "secrets")
    async with session.begin():
        job = await _get_job(session, job_id)
        current = await _current_version(session, job)
        new_spec = body.spec.model_dump()
        if new_spec != current.spec:
            await ops.add_job_version(session, job, description=body.description, spec=new_spec)
        else:
            job.description = body.description
    await session.refresh(job)
    return await _job_out(session, job)


@router.get("/{job_id}/versions")
async def list_versions(job_id: uuid.UUID, session: SessionDep) -> list[JobVersionOut]:
    await _get_job(session, job_id)
    rows = (
        (
            await session.execute(
                select(JobVersion)
                .where(JobVersion.job_id == job_id)
                .order_by(JobVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return [JobVersionOut.model_validate(r) for r in rows]


@router.post("/{job_id}/runs", status_code=status.HTTP_201_CREATED, response_model=RunOut)
async def trigger_run(
    actor: ActorDep,
    job_id: uuid.UUID,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Start a run of the job's current version. Send an ``Idempotency-Key`` header to make
    retries of this request safe: the same key returns the original run instead of a new one."""

    async def work() -> RunOut:
        job = await _get_job(session, job_id)
        version = await _current_version(session, job)
        run = await ops.create_run(session, job, version, trigger="manual", actor=actor)
        return run_out(run, job.name)

    return await idempotent(
        session, key=validate_key(idempotency_key), scope=f"trigger:{job_id}", payload={}, work=work
    )
