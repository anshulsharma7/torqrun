"""Agent protocol endpoints (``/api/v1/agent/*``). Contract: ``torqrun_protocol.agent``."""

import asyncio
import logging
import secrets
import time
import uuid
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_api.agent_auth import AgentDep
from torqrun_api.deps import SessionDep, SessionMakerDep, SettingsDep
from torqrun_api.edition import SecretResolver
from torqrun_api.lifecycle import shutting_down
from torqrun_core.states import InvalidTransitionError, RunStatus, is_terminal
from torqrun_db import fleet
from torqrun_db import runs as ops
from torqrun_db.models import Agent, RunAttempt
from torqrun_protocol.agent import (
    AssignedSpec,
    Assignment,
    ClaimRequest,
    ClaimResponse,
    CompleteRequest,
    EnrollRequest,
    EnrollResponse,
    HeartbeatRequest,
    HeartbeatResponse,
    LeaseRequest,
    LogBatch,
    LogBatchResponse,
    RotateResponse,
    StartedRequest,
)
from torqrun_protocol.jobs import JobSpec

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/agent", tags=["agent protocol"])

S = RunStatus
OUTCOME_STATUS = {
    "succeeded": S.SUCCEEDED,
    "failed": S.FAILED,
    "timed_out": S.TIMED_OUT,
    "cancelled": S.CANCELLED,
    "lost": S.LOST,
}


def _lease_conflict() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail="lease_revoked")


def _invalid(exc: InvalidTransitionError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail=f"invalid_transition: {exc}")


@router.post("/enroll", status_code=status.HTTP_201_CREATED)
async def enroll(body: EnrollRequest, session: SessionDep, settings: SettingsDep) -> EnrollResponse:
    """Exchange an enrollment token for a permanent, revocable agent identity.

    Accepts single-use tokens created by an admin (``tqe_…``) and, in development only, the
    shared ``TORQRUN_DEV_ENROLLMENT_TOKEN`` used by the bundled Compose agent.
    """
    dev_token = settings.dev_enrollment_token
    is_dev = dev_token is not None and secrets.compare_digest(
        fleet.hash_secret(body.enrollment_token), fleet.hash_secret(dev_token.get_secret_value())
    )
    try:
        async with session.begin():
            token_row = None
            if not is_dev:
                try:
                    token_row = await fleet.consume_enrollment_token(session, body.enrollment_token)
                except fleet.EnrollmentError as exc:
                    logger.warning(
                        "enrollment rejected", extra={"agent_name": body.name, "reason": str(exc)}
                    )
                    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from None
            agent = Agent(
                name=body.name,
                hostname=body.hostname,
                os=body.os,
                arch=body.arch,
                agent_version=body.agent_version,
                max_slots=body.max_slots,
                # A token's presets win: the admin decided what this machine is for.
                queues=(token_row.queues if token_row and token_row.queues else body.queues),
                tags=sorted(set(body.tags) | set(token_row.tags if token_row else [])),
                capabilities=body.capabilities,
                status="PENDING",
                enrolled_via=token_row.id if token_row else None,
            )
            session.add(agent)
            await session.flush()
            credential = await fleet.issue_credential(session, agent)
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"an agent named {body.name!r} is already enrolled; choose another name",
        ) from None
    logger.info(
        "agent enrolled",
        extra={"agent_id": str(agent.id), "agent_name": agent.name, "dev_token": is_dev},
    )
    return EnrollResponse(
        agent_id=agent.id, credential=credential, queues=agent.queues, tags=agent.tags
    )


@router.post("/credentials/rotate")
async def rotate_credential(
    request: Request, agent: AgentDep, session: SessionDep
) -> RotateResponse:
    """Replace the calling agent's credential. The old one keeps working for 5 minutes."""
    async with session.begin():
        credential = await fleet.rotate_credential(session, agent, request.state.credential_hash)
    logger.info("agent credential rotated", extra={"agent_name": agent.name})
    return RotateResponse(credential=credential)


@router.post("/drain", status_code=status.HTTP_204_NO_CONTENT)
async def self_drain(agent: AgentDep, session: SessionDep) -> Response:
    """The agent asks to stop receiving new work (e.g. before maintenance). Running jobs finish."""
    async with session.begin():
        await fleet.set_draining(session, agent.id, True)
    logger.info("agent draining (self-requested)", extra={"agent_name": agent.name})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/heartbeat")
async def heartbeat(
    body: HeartbeatRequest, agent: AgentDep, session: SessionDep, settings: SettingsDep
) -> HeartbeatResponse:
    ttl = timedelta(seconds=settings.lease_ttl_seconds)
    async with session.begin():
        agent.last_seen_at = ops.utcnow()
        if body.system is not None:
            agent.system_stats = body.system.model_dump(exclude_none=True)
        if body.capabilities is not None:
            agent.capabilities = body.capabilities
        if agent.status == "PENDING":
            agent.status = "ONLINE"
            logger.info("agent online", extra={"agent_name": agent.name})
        revoked = await ops.extend_leases(
            session, agent.id, [(r.attempt_id, r.lease_token) for r in body.running], ttl
        )
        cancel = (
            (
                await session.execute(
                    select(RunAttempt.id).where(
                        RunAttempt.agent_id == agent.id, RunAttempt.status == S.CANCEL_REQUESTED
                    )
                )
            )
            .scalars()
            .all()
        )
    return HeartbeatResponse(
        server_time=ops.utcnow(),
        lease_ttl_seconds=settings.lease_ttl_seconds,
        revoked_leases=revoked,
        cancel=list(cancel),
        agent_status=agent.status,
    )


@router.post("/claim")
async def claim(
    body: ClaimRequest,
    request: Request,
    agent: AgentDep,
    maker: SessionMakerDep,
    settings: SettingsDep,
) -> ClaimResponse:
    """Long-poll for work: returns as soon as something is assigned, or empty after the wait.

    Polls the database at a short interval rather than holding a transaction open, so a
    waiting agent costs one cheap indexed query per interval.
    """
    if agent.status in ("DRAINING", "REVOKED"):
        return ClaimResponse(assignments=[])
    ttl = timedelta(seconds=settings.lease_ttl_seconds)
    limit = min(body.free_slots, agent.max_slots)
    deadline = time.monotonic() + body.wait_seconds
    while True:
        if await request.is_disconnected() or shutting_down(request.app):
            return ClaimResponse(assignments=[])
        try:
            async with maker() as session, session.begin():
                fresh = await session.get(Agent, agent.id)
                if fresh is None or fresh.status in ("DRAINING", "REVOKED"):
                    return ClaimResponse(assignments=[])
                claimed = await ops.claim(session, fresh, limit=limit, lease_ttl=ttl)
                resolve = request.app.state.secret_resolver
                assignments = [await _assignment(session, resolve, c) for c in claimed]
                if assignments and await request.is_disconnected():
                    # The caller left while we were claiming: roll the claim back instead of
                    # handing work to a connection that can no longer receive it.
                    raise _CallerGoneError
        except _CallerGoneError:
            return ClaimResponse(assignments=[])
        if assignments:
            for a in assignments:
                logger.info(
                    "attempt dispatched",
                    extra={
                        "run_id": str(a.run_id),
                        "attempt": a.attempt_no,
                        "agent_name": agent.name,
                    },
                )
            return ClaimResponse(assignments=assignments)
        # Stop waiting once the agent is gone (restart, crash) so nothing is claimed for a
        # caller that can't receive it, and server shutdown isn't held up by idle long-polls.
        if (
            time.monotonic() >= deadline
            or await request.is_disconnected()
            or shutting_down(request.app)
        ):
            return ClaimResponse(assignments=[])
        await asyncio.sleep(settings.claim_poll_interval_seconds)


class _CallerGoneError(Exception):
    pass


async def _assignment(session: AsyncSession, resolve: SecretResolver, c: ops.Claimed) -> Assignment:
    spec = JobSpec.model_validate(c.run.spec_snapshot)
    secret_env, missing = await resolve(session, spec.secrets) if spec.secrets else ({}, [])
    return Assignment(
        run_id=c.run.id,
        attempt_id=c.attempt.id,
        attempt_no=c.attempt.attempt_no,
        lease_token=c.attempt.lease_token or "",
        lease_expires_at=c.attempt.lease_expires_at or ops.utcnow(),
        job_id=c.job.id,
        job_name=c.job.name,
        job_version=c.run.job_version,
        spec=AssignedSpec.model_validate(c.run.spec_snapshot),
        workflow_run_id=c.run.workflow_run_id,
        task_key=c.run.task_key,
        secret_env=secret_env,
        missing_secrets=missing,
    )


@router.post("/attempts/{attempt_id}/ack", status_code=status.HTTP_204_NO_CONTENT)
async def ack(
    attempt_id: uuid.UUID, body: LeaseRequest, agent: AgentDep, session: SessionDep
) -> Response:
    async with session.begin():
        try:
            attempt = await ops.lock_leased_attempt(session, attempt_id, agent.id, body.lease_token)
        except ops.LeaseMismatchError:
            raise _lease_conflict() from None
        if attempt.status == S.DISPATCHED:
            attempt.acked_at = ops.utcnow()
            await ops.transition(
                session, attempt, S.STARTING, actor=f"agent:{agent.name}", reason="acknowledged"
            )
        elif attempt.status != S.STARTING:  # STARTING = duplicate ack after a lost response
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"cannot ack attempt in {attempt.status}"
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/attempts/{attempt_id}/started", status_code=status.HTTP_204_NO_CONTENT)
async def started(
    attempt_id: uuid.UUID, body: StartedRequest, agent: AgentDep, session: SessionDep
) -> Response:
    async with session.begin():
        try:
            attempt = await ops.lock_leased_attempt(session, attempt_id, agent.id, body.lease_token)
        except ops.LeaseMismatchError:
            raise _lease_conflict() from None
        if attempt.status == S.STARTING:
            attempt.pid = body.pid
            attempt.started_at = body.started_at
            await ops.transition(
                session,
                attempt,
                S.RUNNING,
                actor=f"agent:{agent.name}",
                reason=f"process started (pid {body.pid})",
            )
        elif attempt.status == S.CANCEL_REQUESTED:
            # Cancelled between ack and spawn: record the process; the agent kills it next.
            attempt.pid = body.pid
            attempt.started_at = body.started_at
        elif attempt.status != S.RUNNING:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"cannot start attempt in {attempt.status}"
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/attempts/{attempt_id}/logs")
async def logs(
    attempt_id: uuid.UUID, body: LogBatch, agent: AgentDep, session: SessionDep
) -> LogBatchResponse:
    async with session.begin():
        try:
            attempt = await ops.lock_leased_attempt(session, attempt_id, agent.id, body.lease_token)
        except ops.LeaseMismatchError:
            raise _lease_conflict() from None
        added = await ops.append_logs(session, attempt, [c.model_dump() for c in body.chunks])
        cancel = attempt.status == S.CANCEL_REQUESTED
    return LogBatchResponse(accepted=added, cancel_requested=cancel)


@router.post("/attempts/{attempt_id}/complete", status_code=status.HTTP_204_NO_CONTENT)
async def complete(
    attempt_id: uuid.UUID, body: CompleteRequest, agent: AgentDep, session: SessionDep
) -> Response:
    target = OUTCOME_STATUS[body.outcome]
    async with session.begin():
        existing = (
            await session.execute(
                select(RunAttempt).where(RunAttempt.id == attempt_id).with_for_update()
            )
        ).scalar_one_or_none()
        # Idempotent retry: the first report succeeded but its response was lost.
        if (
            existing is not None
            and existing.agent_id == agent.id
            and is_terminal(S(existing.status))
            and existing.status in (target, S.CANCELLED)
        ):
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        try:
            attempt = await ops.lock_leased_attempt(session, attempt_id, agent.id, body.lease_token)
        except ops.LeaseMismatchError:
            raise _lease_conflict() from None
        if attempt.status == S.CANCEL_REQUESTED and target is S.LOST:
            target = S.CANCELLED  # the user wanted it stopped, and it did stop
        attempt.exit_code = body.exit_code
        attempt.error_summary = body.error_summary
        attempt.finished_at = body.finished_at
        try:
            run = await ops.finish(
                session,
                attempt,
                target,
                actor=f"agent:{agent.name}",
                reason=f"exit code {body.exit_code}"
                if body.exit_code is not None
                else body.outcome,
            )
        except InvalidTransitionError as exc:
            raise _invalid(exc) from None
    logger.info(
        "attempt finished",
        extra={"run_id": str(run.id), "status": target.value, "exit_code": body.exit_code},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
