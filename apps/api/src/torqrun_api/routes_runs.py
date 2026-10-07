"""Runs: history, details, logs, and a live Server-Sent Events stream."""

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_api.deps import SessionDep, SessionMakerDep, SettingsDep
from torqrun_api.idempotency import idempotent, validate_key
from torqrun_api.lifecycle import shutting_down
from torqrun_api.runs_view import run_out
from torqrun_api.schemas import (
    AttemptOut,
    LogChunkOut,
    LogPage,
    Page,
    RunDetail,
    RunEventOut,
    RunOut,
)
from torqrun_api.security import ActorDep
from torqrun_core.states import RunStatus, is_terminal
from torqrun_db import runs as ops
from torqrun_db.models import Agent, Job, JobVersion, LogChunk, Run, RunAttempt, RunEvent

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

STREAM_BATCH = 500
KEEPALIVE_SECONDS = 15.0


async def _run_with_job(session: AsyncSession, run_id: uuid.UUID) -> tuple[Run, str]:
    row = (
        await session.execute(
            select(Run, Job.name).join(Job, Job.id == Run.job_id).where(Run.id == run_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    return row[0], row[1]


async def _attempt(session: AsyncSession, run: Run, attempt_no: int | None) -> RunAttempt:
    no = attempt_no or run.current_attempt
    attempt = (
        await session.execute(
            select(RunAttempt).where(RunAttempt.run_id == run.id, RunAttempt.attempt_no == no)
        )
    ).scalar_one_or_none()
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"attempt {no} not found")
    return attempt


async def _chunks(
    session: AsyncSession, attempt_id: uuid.UUID, after: int, limit: int
) -> list[LogChunk]:
    return list(
        (
            await session.execute(
                select(LogChunk)
                .where(LogChunk.attempt_id == attempt_id, LogChunk.seq > after)
                .order_by(LogChunk.seq)
                .limit(limit)
            )
        ).scalars()
    )


@router.get("")
async def list_runs(
    session: SessionDep,
    job_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    status_: Annotated[list[RunStatus] | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[RunOut]:
    stmt = select(Run, Job.name).join(Job, Job.id == Run.job_id)
    if job_id is not None:
        stmt = stmt.where(Run.job_id == job_id)
    if agent_id is not None:
        stmt = stmt.where(
            Run.id.in_(select(RunAttempt.run_id).where(RunAttempt.agent_id == agent_id))
        )
    if status_:
        stmt = stmt.where(Run.status.in_([s.value for s in status_]))
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await session.execute(stmt.order_by(Run.created_at.desc()).limit(limit).offset(offset))
    ).all()
    return Page(
        items=[run_out(r, name) for r, name in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, session: SessionDep) -> RunDetail:
    run, job_name = await _run_with_job(session, run_id)
    attempts = (
        await session.execute(
            select(RunAttempt, Agent.name)
            .outerjoin(Agent, Agent.id == RunAttempt.agent_id)
            .where(RunAttempt.run_id == run.id)
            .order_by(RunAttempt.attempt_no)
        )
    ).all()
    events = (
        (
            await session.execute(
                select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id)
            )
        )
        .scalars()
        .all()
    )
    return RunDetail(
        **run_out(run, job_name).model_dump(),
        spec=run.spec_snapshot,  # dict, validated into JobSpec
        attempts=[
            AttemptOut.model_validate({**_attrs(a), "agent_name": agent_name})
            for a, agent_name in attempts
        ],
        events=[RunEventOut.model_validate(e) for e in events],
    )


def _attrs(attempt: RunAttempt) -> dict[str, object]:
    return {c.key: getattr(attempt, c.key) for c in RunAttempt.__table__.columns}


@router.post("/{run_id}/cancel")
async def cancel_run(actor: ActorDep, run_id: uuid.UUID, session: SessionDep) -> RunOut:
    """Cancel a run. Queued runs end immediately; running ones are signalled and end as
    *Cancelled* once the agent has killed the process (seconds). Repeating is harmless."""
    async with session.begin():
        try:
            run = await ops.cancel_run(session, run_id, actor=actor)
        except LookupError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found") from None
        except ops.RunFinishedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
        _, name = await _run_with_job(session, run_id)
        return run_out(run, name)


@router.post("/{run_id}/retry", status_code=status.HTTP_201_CREATED, response_model=RunOut)
async def retry_run(
    actor: ActorDep,
    run_id: uuid.UUID,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """New attempt of the *same* run (same version and parameters), now. For failed, timed-out
    and lost runs, or to skip a pending retry delay."""

    async def work() -> RunOut:
        try:
            run = await ops.retry_run(session, run_id, actor=actor)
        except LookupError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found") from None
        except ops.RunFinishedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
        _, name = await _run_with_job(session, run_id)
        return run_out(run, name)

    return await idempotent(
        session, key=validate_key(idempotency_key), scope=f"retry:{run_id}", payload={}, work=work
    )


@router.post("/{run_id}/rerun", status_code=status.HTTP_201_CREATED, response_model=RunOut)
async def rerun(
    actor: ActorDep,
    run_id: uuid.UUID,
    session: SessionDep,
    version: Literal["current", "original"] = Query(
        default="current",
        description="Job version to run: the job's current one, or the one this run used",
    ),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Start a *new* run of the same job, linked to this one via ``rerun_of``."""

    async def work() -> RunOut:
        original, _ = await _run_with_job(session, run_id)
        job = await session.get(Job, original.job_id)
        assert job is not None  # noqa: S101 - FK guarantees it
        wanted = job.current_version if version == "current" else original.job_version
        jv = (
            await session.execute(
                select(JobVersion).where(JobVersion.job_id == job.id, JobVersion.version == wanted)
            )
        ).scalar_one()
        run = await ops.create_run(
            session, job, jv, trigger="rerun", actor=actor, rerun_of=original.id
        )
        return run_out(run, job.name)

    return await idempotent(
        session,
        key=validate_key(idempotency_key),
        scope=f"rerun:{run_id}:{version}",
        payload={},
        work=work,
    )


@router.get("/{run_id}/logs")
async def get_logs(
    run_id: uuid.UUID,
    session: SessionDep,
    attempt: int | None = Query(default=None, ge=1),
    after_seq: int = Query(default=-1, ge=-1),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> LogPage:
    run, _ = await _run_with_job(session, run_id)
    att = await _attempt(session, run, attempt)
    chunks = await _chunks(session, att.id, after_seq, limit)
    next_seq = chunks[-1].seq if chunks else after_seq
    finished = is_terminal(RunStatus(att.status))
    return LogPage(
        attempt_no=att.attempt_no,
        chunks=[LogChunkOut.model_validate(c) for c in chunks],
        next_after_seq=next_seq,
        complete=finished and len(chunks) < limit,
    )


@router.get("/{run_id}/logs/download")
async def download_logs(
    run_id: uuid.UUID, maker: SessionMakerDep, attempt: int | None = Query(default=None, ge=1)
) -> StreamingResponse:
    async with maker() as session:
        run, job_name = await _run_with_job(session, run_id)
        att = await _attempt(session, run, attempt)

    async def body() -> AsyncIterator[str]:
        after = -1
        while True:
            async with maker() as session:
                chunks = await _chunks(session, att.id, after, 5000)
            if not chunks:
                return
            for c in chunks:
                prefix = "" if c.stream == "stdout" else f"[{c.stream}] "
                yield f"{c.ts.isoformat()} {prefix}{c.data}"
            after = chunks[-1].seq

    filename = f"{job_name}-{run.id}-attempt{att.attempt_no}.log"
    return StreamingResponse(
        body(),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _sse(event: str, data: object, event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


@router.get("/{run_id}/stream")
async def stream_run(
    run_id: uuid.UUID,
    request: Request,
    maker: SessionMakerDep,
    settings: SettingsDep,
    attempt: int | None = Query(default=None, ge=1),
    after_seq: int = Query(default=-1, ge=-1),
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Live run status and logs as Server-Sent Events.

    Events: ``run`` (RunOut, on every status change), ``logs`` (list of chunks; the SSE id is
    the last seq, so browsers resume from where they left off after a reconnect) and ``end``
    (the streamed attempt finished and every log chunk has been sent). Streams one attempt:
    the current one unless ``attempt`` is given.
    """
    async with maker() as session:
        run, _ = await _run_with_job(session, run_id)
        att = await _attempt(session, run, attempt)
    attempt_id = att.id
    cursor = int(last_event_id) if last_event_id and last_event_id.isdigit() else after_seq

    async def events() -> AsyncIterator[str]:
        nonlocal cursor
        last_run_state: tuple[str, object] | None = None
        last_sent = time.monotonic()
        while not await request.is_disconnected() and not shutting_down(request.app):
            async with maker() as session:
                current, name = await _run_with_job(session, run_id)
                chunks = await _chunks(session, attempt_id, cursor, STREAM_BATCH)
                attempt_status = (
                    await session.execute(
                        select(RunAttempt.status).where(RunAttempt.id == attempt_id)
                    )
                ).scalar_one()
            state = (current.status, current.updated_at)
            if state != last_run_state:
                last_run_state = state
                yield _sse("run", run_out(current, name).model_dump(mode="json"))
                last_sent = time.monotonic()
            if chunks:
                cursor = chunks[-1].seq
                payload = [LogChunkOut.model_validate(c).model_dump(mode="json") for c in chunks]
                yield _sse("logs", payload, event_id=cursor)
                last_sent = time.monotonic()
                if len(chunks) == STREAM_BATCH:
                    continue  # more backlog: send it without waiting
            elif is_terminal(RunStatus(attempt_status)):
                # This attempt is over and the agent flushes all logs before reporting the
                # outcome, so nothing follows. (A retry streams under its own attempt number.)
                yield _sse("end", {"status": attempt_status})
                return
            if time.monotonic() - last_sent > KEEPALIVE_SECONDS:
                yield ": keepalive\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(settings.stream_poll_interval_seconds)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
