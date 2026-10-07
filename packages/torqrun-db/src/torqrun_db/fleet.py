"""Agent fleet operations: enrollment tokens, credentials, drain and revoke.

Secrets (enrollment tokens, agent credentials) are random 256-bit values stored only as
SHA-256 hashes; the plaintext is returned exactly once, at creation.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_core.states import LEASED, RunStatus
from torqrun_db.models import Agent, AgentCredential, EnrollmentToken, RunAttempt
from torqrun_db.runs import finish, transition, utcnow

ENROLLMENT_PREFIX = "tqe_"
CREDENTIAL_PREFIX = "tqa_"
ROTATION_OVERLAP = timedelta(minutes=5)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


class EnrollmentError(Exception):
    """The enrollment token is unknown, expired, revoked or used up."""


@dataclass(frozen=True)
class NewToken:
    row: EnrollmentToken
    token: str  # plaintext, returned once


async def create_enrollment_token(
    session: AsyncSession,
    *,
    description: str,
    ttl: timedelta,
    max_uses: int | None,
    queues: list[str],
    tags: list[str],
    created_by: str,
) -> NewToken:
    token = new_secret(ENROLLMENT_PREFIX)
    row = EnrollmentToken(
        token_hash=hash_secret(token),
        prefix=token[:12],
        description=description,
        max_uses=max_uses,
        uses=0,
        expires_at=utcnow() + ttl,
        queues=queues,
        tags=tags,
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return NewToken(row=row, token=token)


async def consume_enrollment_token(session: AsyncSession, token: str) -> EnrollmentToken:
    """Validate and use up one use of ``token`` (row-locked, so concurrent enrollments with a
    single-use token cannot both succeed)."""
    row = (
        await session.execute(
            select(EnrollmentToken)
            .where(EnrollmentToken.token_hash == hash_secret(token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = utcnow()
    if row is None or row.revoked_at is not None:
        raise EnrollmentError("invalid enrollment token")
    if row.expires_at is not None and row.expires_at <= now:
        raise EnrollmentError("enrollment token expired")
    if row.max_uses is not None and row.uses >= row.max_uses:
        raise EnrollmentError("enrollment token already used")
    row.uses += 1
    return row


async def issue_credential(session: AsyncSession, agent: Agent) -> str:
    credential = new_secret(CREDENTIAL_PREFIX)
    session.add(AgentCredential(agent_id=agent.id, secret_hash=hash_secret(credential)))
    await session.flush()
    return credential


async def rotate_credential(session: AsyncSession, agent: Agent, current_hash: str) -> str:
    """Issue a new credential; the presented one keeps working for ROTATION_OVERLAP so requests
    already in flight (or an agent that crashes before saving the new one) don't fail hard."""
    await session.execute(
        update(AgentCredential)
        .where(AgentCredential.secret_hash == current_hash, AgentCredential.agent_id == agent.id)
        .values(expires_at=utcnow() + ROTATION_OVERLAP)
    )
    return await issue_credential(session, agent)


async def authenticate(session: AsyncSession, credential: str) -> tuple[Agent, str] | None:
    """The agent owning ``credential`` and the credential hash, if it is currently valid."""
    digest = hash_secret(credential)
    now = utcnow()
    row = (
        await session.execute(
            select(Agent)
            .join(AgentCredential, AgentCredential.agent_id == Agent.id)
            .where(
                AgentCredential.secret_hash == digest,
                AgentCredential.revoked_at.is_(None),
                (AgentCredential.expires_at.is_(None)) | (AgentCredential.expires_at > now),
            )
        )
    ).scalar_one_or_none()
    return (row, digest) if row is not None else None


async def set_draining(session: AsyncSession, agent_id: uuid.UUID, draining: bool) -> Agent:
    agent = await _lock_agent(session, agent_id)
    if agent.status == "REVOKED":
        raise ValueError("agent is revoked")
    agent.status = "DRAINING" if draining else "ONLINE"
    return agent


async def revoke_agent(session: AsyncSession, agent_id: uuid.UUID, *, actor: str) -> Agent:
    """Revoke an agent: credentials die immediately and its in-flight work is marked LOST
    (retried if the job allows), because a revoked agent's reports are no longer accepted."""
    agent = await _lock_agent(session, agent_id)
    now = utcnow()
    agent.status = "REVOKED"
    agent.revoked_at = agent.revoked_at or now
    await session.execute(
        update(AgentCredential)
        .where(AgentCredential.agent_id == agent.id, AgentCredential.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    leased = (
        (
            await session.execute(
                select(RunAttempt)
                .where(
                    RunAttempt.agent_id == agent.id,
                    RunAttempt.status.in_([s.value for s in LEASED]),
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for attempt in leased:
        if attempt.status == RunStatus.DISPATCHED:
            # Never acknowledged, so it cannot have started: hand it to another agent.
            attempt.agent_id = None
            attempt.lease_token = None
            attempt.lease_expires_at = None
            attempt.dispatched_at = None
            await transition(
                session, attempt, RunStatus.QUEUED, actor=actor, reason="agent revoked before start"
            )
            continue
        target = (
            RunStatus.CANCELLED if attempt.status == RunStatus.CANCEL_REQUESTED else RunStatus.LOST
        )
        attempt.error_summary = "agent was revoked while the job was running"
        await finish(session, attempt, target, actor=actor, reason="agent revoked")
    return agent


async def revoke_enrollment_token(
    session: AsyncSession, token_id: uuid.UUID
) -> EnrollmentToken | None:
    row = await session.get(EnrollmentToken, token_id, with_for_update=True)
    if row is not None and row.revoked_at is None:
        row.revoked_at = utcnow()
    return row


def token_state(row: EnrollmentToken, now: datetime | None = None) -> str:
    now = now or utcnow()
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= now:
        return "expired"
    if row.max_uses is not None and row.uses >= row.max_uses:
        return "used"
    return "active"


async def _lock_agent(session: AsyncSession, agent_id: uuid.UUID) -> Agent:
    agent = await session.get(Agent, agent_id, with_for_update=True)
    if agent is None:
        raise LookupError(str(agent_id))
    return agent
