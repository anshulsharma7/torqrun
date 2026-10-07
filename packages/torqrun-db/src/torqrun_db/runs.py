"""Run lifecycle operations. Every status change goes through :func:`transition` (attempt and
run together) or :func:`transition_run` (run-only states: RETRY_WAIT and its exits).

Concurrency model: an attempt's row lock (``SELECT ... FOR UPDATE``) serialises all changes to
it; the run row is always locked *after* its attempt, so lock order is consistent and cannot
deadlock. Run-only operations lock just the run. Dispatch takes a per-queue advisory lock (so
concurrency limits can't be overshot by racing claimers) and ``FOR UPDATE SKIP LOCKED`` on the
attempts. Callers own the transaction (``async with session.begin()``).
"""

import hashlib
import json
import random
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_core.retry import RetryPolicy, backoff_delay, should_retry
from torqrun_core.states import LEASED, RunStatus, check_transition, is_terminal
from torqrun_db import events
from torqrun_db.models import (
    Agent,
    ControlPlaneHeartbeat,
    IdempotencyKey,
    Job,
    JobVersion,
    LogChunk,
    QueueSettings,
    Run,
    RunAttempt,
    RunEvent,
)

S = RunStatus


def utcnow() -> datetime:
    return datetime.now(UTC)


class LeaseMismatchError(Exception):
    """The caller's lease token is not the attempt's current one (stale or foreign agent)."""


class RunFinishedError(Exception):
    """The requested change needs a run that is still active (or, for retry, one that failed)."""


class IdempotencyConflictError(Exception):
    """An Idempotency-Key was reused with a different request body."""


def retry_policy(run: Run) -> RetryPolicy:
    spec = run.spec_snapshot
    r = spec.get("retry") or {}
    return RetryPolicy(
        max_attempts=run.max_attempts,
        backoff_seconds=r.get("backoff_seconds", 10.0),
        backoff_factor=r.get("backoff_factor", 2.0),
        max_backoff_seconds=r.get("max_backoff_seconds", 600.0),
        retry_on_timeout=r.get("retry_on_timeout", False),
        interrupt_policy=spec.get("interrupt_policy", "fail"),
    )


@dataclass(frozen=True)
class Claimed:
    attempt: RunAttempt
    run: Run
    job: Job


async def create_job(
    session: AsyncSession, *, name: str, description: str, spec: dict[str, Any]
) -> tuple[Job, JobVersion]:
    job = Job(name=name, description=description, current_version=1)
    session.add(job)
    await session.flush()
    version = JobVersion(job_id=job.id, version=1, spec=spec)
    session.add(version)
    await session.flush()
    return job, version


async def add_job_version(
    session: AsyncSession, job: Job, *, description: str, spec: dict[str, Any]
) -> JobVersion:
    """Create the next immutable version. Locks the job row so concurrent edits serialise."""
    await session.refresh(job, with_for_update=True)
    job.current_version += 1
    job.description = description
    version = JobVersion(job_id=job.id, version=job.current_version, spec=spec)
    session.add(version)
    await session.flush()
    return version


async def create_run(
    session: AsyncSession,
    job: Job,
    version: JobVersion,
    *,
    trigger: str,
    actor: str,
    rerun_of: uuid.UUID | None = None,
    schedule_id: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
    reason: str | None = None,
) -> Run:
    spec = version.spec
    run = Run(
        rerun_of=rerun_of,
        schedule_id=schedule_id,
        scheduled_for=scheduled_for,
        job_id=job.id,
        job_version_id=version.id,
        job_version=version.version,
        trigger=trigger,
        status=S.QUEUED,
        queue=spec.get("queue", "default"),
        priority=spec.get("priority", 0),
        spec_snapshot=spec,
        current_attempt=1,
        max_attempts=(spec.get("retry") or {}).get("max_attempts", 1),
    )
    session.add(run)
    await session.flush()
    session.add(
        RunAttempt(
            run_id=run.id,
            attempt_no=1,
            status=S.QUEUED,
            queue=run.queue,
            priority=run.priority,
        )
    )
    session.add(
        RunEvent(
            run_id=run.id,
            attempt_no=1,
            from_status=None,
            to_status=S.QUEUED,
            reason=reason
            or (f"{trigger} trigger" + (f" (rerun of {str(rerun_of)[-8:]})" if rerun_of else "")),
            actor=actor,
        )
    )
    await session.flush()
    return run


async def _lock_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> RunAttempt | None:
    result = await session.execute(
        select(RunAttempt).where(RunAttempt.id == attempt_id).with_for_update()
    )
    return result.scalar_one_or_none()


async def transition(
    session: AsyncSession,
    attempt: RunAttempt,
    to: RunStatus,
    *,
    actor: str,
    reason: str = "",
    now: datetime | None = None,
) -> Run:
    """Move a (locked) attempt and its run to ``to``, recording a run event.

    Raises :class:`torqrun_core.states.InvalidTransitionError` for edges not in the table.
    """
    now = now or utcnow()
    current = RunStatus(attempt.status)
    check_transition(current, to)
    run = (
        await session.execute(select(Run).where(Run.id == attempt.run_id).with_for_update())
    ).scalar_one()

    attempt.status = to
    run.status = to
    if to is S.RUNNING:
        run.started_at = attempt.started_at or now
    if is_terminal(to):
        attempt.finished_at = attempt.finished_at or now
        run.finished_at = attempt.finished_at
        run.exit_code = attempt.exit_code
        run.error_summary = attempt.error_summary
        attempt.lease_token = None
        attempt.lease_expires_at = None
    session.add(
        RunEvent(
            run_id=run.id,
            attempt_no=attempt.attempt_no,
            from_status=current,
            to_status=to,
            reason=reason,
            actor=actor,
            at=now,
        )
    )
    await session.flush()
    return run


async def _lock_run(session: AsyncSession, run_id: uuid.UUID) -> Run | None:
    return (
        await session.execute(select(Run).where(Run.id == run_id).with_for_update())
    ).scalar_one_or_none()


async def transition_run(
    session: AsyncSession,
    run: Run,
    to: RunStatus,
    *,
    actor: str,
    reason: str = "",
    now: datetime | None = None,
) -> None:
    """Change a (locked) run's status without touching its attempts (RETRY_WAIT and exits)."""
    now = now or utcnow()
    current = RunStatus(run.status)
    check_transition(current, to)
    run.status = to
    if to is S.CANCELLED:
        run.finished_at = now
        run.next_attempt_at = None
    session.add(
        RunEvent(
            run_id=run.id,
            attempt_no=run.current_attempt,
            from_status=current,
            to_status=to,
            reason=reason,
            actor=actor,
            at=now,
        )
    )
    await session.flush()


async def finish(
    session: AsyncSession,
    attempt: RunAttempt,
    to: RunStatus,
    *,
    actor: str,
    reason: str = "",
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> Run:
    """End a (locked) attempt in terminal state ``to`` and schedule a retry if policy allows."""
    now = now or utcnow()
    run = await transition(session, attempt, to, actor=actor, reason=reason, now=now)
    policy = retry_policy(run)
    if should_retry(to, attempt.attempt_no, policy):
        delay = backoff_delay(attempt.attempt_no, policy, rng)
        run.next_attempt_at = now + timedelta(seconds=delay)
        await transition_run(
            session,
            run,
            S.RETRY_WAIT,
            actor="system",
            now=now,
            reason=f"attempt {attempt.attempt_no} {to.value.lower().replace('_', ' ')}; "
            f"retry {attempt.attempt_no + 1}/{run.max_attempts} in {delay:.0f}s",
        )
    else:
        await events.emit_run_finished(session, run)
    return run


async def _start_next_attempt(
    session: AsyncSession, run: Run, *, actor: str, reason: str
) -> RunAttempt:
    """RETRY_WAIT -> QUEUED with a fresh attempt. ``run`` must be locked."""
    run.current_attempt += 1
    run.next_attempt_at = None
    run.started_at = run.finished_at = None
    run.exit_code = None
    run.error_summary = None
    run.queued_at = utcnow()
    attempt = RunAttempt(
        run_id=run.id,
        attempt_no=run.current_attempt,
        status=S.QUEUED,
        queue=run.queue,
        priority=run.priority,
    )
    session.add(attempt)
    await transition_run(session, run, S.QUEUED, actor=actor, reason=reason)
    return attempt


async def promote_due_retries(session: AsyncSession, *, limit: int = 100) -> list[Run]:
    """Queue the next attempt of every run whose retry delay has elapsed."""
    due = (
        (
            await session.execute(
                select(Run)
                .where(Run.status == S.RETRY_WAIT, Run.next_attempt_at <= utcnow())
                .order_by(Run.next_attempt_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for run in due:
        await _start_next_attempt(
            session,
            run,
            actor="system",
            reason=f"attempt {run.current_attempt + 1} of {run.max_attempts} queued",
        )
    return list(due)


async def reap_expired_leases(session: AsyncSession, *, limit: int = 100) -> list[RunAttempt]:
    """Fail attempts whose agent stopped renewing its lease (crashed, partitioned, powered off).

    RUNNING/STARTING become LOST (retried only if the job opted in); an attempt that was already
    being cancelled becomes CANCELLED.
    """
    expired = (
        (
            await session.execute(
                select(RunAttempt)
                .where(
                    RunAttempt.status.in_([S.STARTING, S.RUNNING, S.CANCEL_REQUESTED]),
                    RunAttempt.lease_expires_at < utcnow(),
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for attempt in expired:
        if attempt.status == S.CANCEL_REQUESTED:
            attempt.error_summary = "cancelled; agent stopped responding before confirming the kill"
            await finish(
                session,
                attempt,
                S.CANCELLED,
                actor="system",
                reason="lease expired during cancellation",
            )
        else:
            attempt.error_summary = (
                "agent stopped responding (lease expired); the process may have partially run"
            )
            await finish(session, attempt, S.LOST, actor="system", reason="lease expired")
    return list(expired)


async def cancel_run(session: AsyncSession, run_id: uuid.UUID, *, actor: str) -> Run:
    """Cancel a run in whatever state it is. Raises RunFinishedError if it already ended."""
    probe = await session.get(Run, run_id)
    if probe is None:
        raise LookupError(str(run_id))
    attempt = (
        await session.execute(
            select(RunAttempt)
            .where(RunAttempt.run_id == run_id, RunAttempt.attempt_no == probe.current_attempt)
            .with_for_update()
        )
    ).scalar_one()
    run = await _lock_run(session, run_id)
    assert run is not None  # noqa: S101 - existed above and runs are never deleted mid-request
    await session.refresh(run)
    status = RunStatus(run.status)
    if status is S.RETRY_WAIT:
        await transition_run(
            session, run, S.CANCELLED, actor=actor, reason="cancelled while waiting to retry"
        )
    elif status in (S.QUEUED, S.DISPATCHED):
        # Not started yet: end it now. A DISPATCHED attempt's lease is cleared, so the agent's
        # ack fails and it never spawns the process.
        attempt.error_summary = "cancelled before it started"
        await finish(session, attempt, S.CANCELLED, actor=actor, reason="cancelled before start")
    elif status in (S.STARTING, S.RUNNING):
        await transition(
            session, attempt, S.CANCEL_REQUESTED, actor=actor, reason="cancel requested"
        )
    elif status is not S.CANCEL_REQUESTED:
        raise RunFinishedError(f"run already {status.value.lower()}")
    return run


async def retry_run(session: AsyncSession, run_id: uuid.UUID, *, actor: str) -> Run:
    """Manually start another attempt of a failed/timed-out/lost run (ignores max_attempts)."""
    run = await _lock_run(session, run_id)
    if run is None:
        raise LookupError(str(run_id))
    status = RunStatus(run.status)
    if status not in (S.FAILED, S.TIMED_OUT, S.LOST, S.RETRY_WAIT):
        raise RunFinishedError(
            f"only failed, timed-out or lost runs can be retried (run is {status.value.lower()})"
        )
    if status is not S.RETRY_WAIT:
        await transition_run(session, run, S.RETRY_WAIT, actor=actor, reason="manual retry")
    run.max_attempts = max(run.max_attempts, run.current_attempt + 1)
    await _start_next_attempt(
        session, run, actor=actor, reason=f"attempt {run.current_attempt + 1} queued (manual retry)"
    )
    return run


def cancel_requested_for(attempts: Sequence[RunAttempt]) -> list[uuid.UUID]:
    return [a.id for a in attempts if a.status == S.CANCEL_REQUESTED]


MAX_CLAIM_PASSES = 5


async def claim(
    session: AsyncSession, agent: Agent, *, limit: int, lease_ttl: timedelta
) -> list[Claimed]:
    """Atomically assign up to ``limit`` queued attempts to ``agent``.

    ``FOR UPDATE SKIP LOCKED`` alone guarantees no attempt is claimed twice, and lets any number
    of claimers work in parallel. Concurrency limits need more: counting running attempts and
    claiming must be atomic. So a transaction-scoped advisory lock is taken only where a limit
    exists: per queue with ``max_concurrency`` (before selecting rows) and per job with
    ``max_concurrent`` (non-blocking: a job another claimer is counting is skipped this round,
    so claimers never wait on each other there). Unlimited queues never serialize.
    """
    now = utcnow()
    queues = sorted(set(agent.queues))
    settings = {
        qs.name: qs
        for qs in (
            await session.execute(select(QueueSettings).where(QueueSettings.name.in_(queues)))
        ).scalars()
    }
    serving = [q for q in queues if not (q in settings and settings[q].paused)]
    if not serving:
        return []
    capped = [q for q in serving if q in settings and settings[q].max_concurrency is not None]
    for q in capped:
        await session.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(f"torqrun.queue:{q}")))
        )
    leased = [st.value for st in LEASED]
    queue_active: dict[str, int] = {}
    if capped:
        queue_active = dict(
            (
                await session.execute(
                    select(RunAttempt.queue, func.count())
                    .where(RunAttempt.status.in_(leased), RunAttempt.queue.in_(capped))
                    .group_by(RunAttempt.queue)
                )
            ).all()
        )
    base = (
        select(RunAttempt, Run)
        .join(Run, Run.id == RunAttempt.run_id)
        .where(RunAttempt.status == S.QUEUED, RunAttempt.queue.in_(serving))
    )
    # Executors are capabilities: a run needing one only goes to agents that offer it. Filtered
    # in SQL so a backlog of, say, container jobs never hides process jobs queued behind it.
    executor = Run.spec_snapshot["executor"].astext
    if "docker" not in (agent.capabilities or []):
        base = base.where(or_(executor.is_(None), executor != "docker"))

    picked: list[tuple[RunAttempt, Run]] = []
    seen: list[uuid.UUID] = []
    job_active: dict[uuid.UUID, int] = {}  # jobs with a limit that we hold the lock for
    busy_jobs: set[uuid.UUID] = set()  # limited jobs another claimer is deciding about
    # Lock only as many rows as still needed (locking extra would hide them from parallel
    # claimers); look further, a few times, only if limits rejected some.
    for _ in range(MAX_CLAIM_PASSES):
        need = limit - len(picked)
        if need <= 0:
            break
        stmt = base.where(RunAttempt.id.not_in(seen)) if seen else base
        rows = (
            await session.execute(
                stmt.order_by(RunAttempt.priority.desc(), RunAttempt.created_at)
                .limit(need)
                .with_for_update(skip_locked=True, of=RunAttempt)
            )
        ).all()
        if not rows:
            break
        for attempt, run in rows:
            seen.append(attempt.id)
            q_cap = settings[attempt.queue].max_concurrency if attempt.queue in settings else None
            if q_cap is not None and queue_active.get(attempt.queue, 0) >= q_cap:
                continue
            j_cap = run.spec_snapshot.get("max_concurrent")
            if j_cap:
                if run.job_id in busy_jobs:
                    continue
                if run.job_id not in job_active:
                    # Non-blocking: if another claimer is counting this job right now, leave its
                    # runs for the next poll. Never waits, so claimers can't deadlock.
                    got = (
                        await session.execute(
                            select(
                                func.pg_try_advisory_xact_lock(
                                    func.hashtext(f"torqrun.job:{run.job_id}")
                                )
                            )
                        )
                    ).scalar_one()
                    if not got:
                        busy_jobs.add(run.job_id)
                        continue
                    job_active[run.job_id] = (
                        await session.execute(
                            select(func.count())
                            .select_from(RunAttempt)
                            .join(Run, Run.id == RunAttempt.run_id)
                            .where(Run.job_id == run.job_id, RunAttempt.status.in_(leased))
                        )
                    ).scalar_one()
                if job_active[run.job_id] >= j_cap:
                    continue
                job_active[run.job_id] += 1
            queue_active[attempt.queue] = queue_active.get(attempt.queue, 0) + 1
            attempt.agent_id = agent.id
            attempt.lease_token = secrets.token_urlsafe(32)
            attempt.lease_expires_at = now + lease_ttl
            attempt.dispatched_at = now
            run = await transition(
                session,
                attempt,
                S.DISPATCHED,
                actor=f"agent:{agent.name}",
                reason="claimed",
                now=now,
            )
            picked.append((attempt, run))
            if len(picked) >= limit:
                break
    if not picked:
        return []
    jobs = {
        j.id: j
        for j in (
            await session.execute(select(Job).where(Job.id.in_({r.job_id for _, r in picked})))
        ).scalars()
    }
    return [Claimed(attempt=a, run=r, job=jobs[r.job_id]) for a, r in picked]


async def requeue_unacked(
    session: AsyncSession, *, older_than: timedelta, limit: int = 100
) -> list[RunAttempt]:
    """Return dispatched-but-never-acknowledged attempts to the queue.

    Safe by construction: agents never spawn a process before their ack succeeds, so an
    attempt still in DISPATCHED cannot have started. Covers claim responses lost in transit
    and agents that died between claim and ack.
    """
    cutoff = utcnow() - older_than
    stale = (
        (
            await session.execute(
                select(RunAttempt)
                .where(RunAttempt.status == S.DISPATCHED, RunAttempt.dispatched_at < cutoff)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for attempt in stale:
        attempt.agent_id = None
        attempt.lease_token = None
        attempt.lease_expires_at = None
        attempt.dispatched_at = None
        await transition(
            session,
            attempt,
            S.QUEUED,
            actor="system",
            reason=f"not acknowledged within {int(older_than.total_seconds())}s; requeued",
        )
    return list(stale)


async def lock_leased_attempt(
    session: AsyncSession, attempt_id: uuid.UUID, agent_id: uuid.UUID, lease_token: str
) -> RunAttempt:
    """Lock an attempt the calling agent must currently hold, or raise LeaseMismatchError."""
    attempt = await _lock_attempt(session, attempt_id)
    if (
        attempt is None
        or attempt.agent_id != agent_id
        or attempt.lease_token is None
        or not secrets.compare_digest(attempt.lease_token, lease_token)
    ):
        raise LeaseMismatchError(str(attempt_id))
    return attempt


async def extend_leases(
    session: AsyncSession,
    agent_id: uuid.UUID,
    refs: Sequence[tuple[uuid.UUID, str]],
    lease_ttl: timedelta,
) -> list[uuid.UUID]:
    """Extend leases the agent still holds; return the attempt IDs it no longer holds."""
    revoked: list[uuid.UUID] = []
    expires = utcnow() + lease_ttl
    for attempt_id, token in refs:
        result = await session.execute(
            update(RunAttempt)
            .where(
                RunAttempt.id == attempt_id,
                RunAttempt.agent_id == agent_id,
                RunAttempt.lease_token == token,
            )
            .values(lease_expires_at=expires)
        )
        if result.rowcount == 0:  # type: ignore[attr-defined]
            revoked.append(attempt_id)
    return revoked


async def append_logs(
    session: AsyncSession, attempt: RunAttempt, chunks: Sequence[dict[str, Any]]
) -> int:
    """Insert chunks idempotently (a retried batch re-sends the same seqs). Returns rows added."""
    stmt = (
        insert(LogChunk)
        .values([{"attempt_id": attempt.id, **c} for c in chunks])
        .on_conflict_do_nothing(index_elements=["attempt_id", "seq"])
        .returning(LogChunk.seq, LogChunk.data)
    )
    inserted = (await session.execute(stmt)).all()
    if inserted:
        attempt.log_bytes += sum(len(data.encode()) for _, data in inserted)
        top = max(seq for seq, _ in inserted)
        attempt.last_log_seq = max(attempt.last_log_seq or -1, top)
    return len(inserted)


def request_hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


async def idempotency_begin(
    session: AsyncSession, scope: str, key: str, req_hash: str
) -> IdempotencyKey | None:
    """Reserve ``key`` in this transaction. Returns the stored record if the key was used before.

    A concurrent duplicate blocks on the primary key until the first transaction commits, then
    sees its stored response, so exactly one request performs the work.
    """
    inserted = (
        await session.execute(
            insert(IdempotencyKey)
            .values(scope=scope, key=key, request_hash=req_hash)
            .on_conflict_do_nothing(index_elements=["scope", "key"])
            .returning(IdempotencyKey.key)
        )
    ).first()
    if inserted is not None:
        return None
    existing = (
        await session.execute(
            select(IdempotencyKey).where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        )
    ).scalar_one()
    if existing.request_hash != req_hash:
        raise IdempotencyConflictError(key)
    return existing


async def idempotency_store(
    session: AsyncSession, scope: str, key: str, status_code: int, response: dict[str, Any]
) -> None:
    await session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.scope == scope, IdempotencyKey.key == key)
        .values(status_code=status_code, response=response)
    )


async def purge_idempotency_keys(session: AsyncSession, *, older_than: timedelta) -> int:
    result = await session.execute(
        delete(IdempotencyKey).where(IdempotencyKey.created_at < utcnow() - older_than)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


ALIVE_WINDOW = timedelta(seconds=10)


async def control_plane_beat(session: AsyncSession, replica_id: str, up_since: datetime) -> None:
    now = utcnow()
    await session.execute(
        insert(ControlPlaneHeartbeat)
        .values(replica_id=replica_id, up_since=up_since, last_alive_at=now)
        .on_conflict_do_update(index_elements=["replica_id"], set_={"last_alive_at": now})
    )
    # Forget replicas that have been gone for a long time.
    await session.execute(
        delete(ControlPlaneHeartbeat).where(
            ControlPlaneHeartbeat.last_alive_at < now - timedelta(hours=1)
        )
    )


async def control_plane_remove(session: AsyncSession, replica_id: str) -> None:
    await session.execute(
        delete(ControlPlaneHeartbeat).where(ControlPlaneHeartbeat.replica_id == replica_id)
    )


async def control_plane_up_since(session: AsyncSession) -> datetime | None:
    """Since when agents have continuously had an API to talk to (None: no API is alive).

    With several replicas behind a load balancer, any live one lets agents renew, so this is
    the earliest ``up_since`` among the replicas that are alive now.
    """
    return (
        await session.execute(
            select(func.min(ControlPlaneHeartbeat.up_since)).where(
                ControlPlaneHeartbeat.last_alive_at >= utcnow() - ALIVE_WINDOW
            )
        )
    ).scalar_one_or_none()
