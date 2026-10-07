"""ORM models. Schema rationale: docs/architecture/ARCHITECTURE.md §6."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from torqrun_core.ids import uuid7
from torqrun_core.states import RunStatus
from torqrun_db.base import Base

TZ = DateTime(timezone=True)

_RUN_STATUSES = ", ".join(f"'{s.value}'" for s in RunStatus)
_ATTEMPT_STATUSES = ", ".join(f"'{s.value}'" for s in RunStatus if s is not RunStatus.RETRY_WAIT)
AGENT_STATUSES = ("PENDING", "ONLINE", "DRAINING", "REVOKED")


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid7)


def _now() -> datetime:
    return datetime.now(UTC)


# Timestamps get Python-side defaults (so values are known after flush without a reload, which
# matters with async sessions) plus server defaults for rows written by raw SQL.
def _created() -> Mapped[datetime]:
    return mapped_column(TZ, default=_now, server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(TZ, default=_now, onupdate=_now, server_default=func.now(), nullable=False)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(63), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    versions: Mapped[list["JobVersion"]] = relationship(
        back_populates="job", order_by="JobVersion.version", lazy="raise"
    )


class JobVersion(Base):
    """Immutable. Editing a job inserts a new row and bumps ``jobs.current_version``."""

    __tablename__ = "job_versions"
    __table_args__ = (UniqueConstraint("job_id", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()

    job: Mapped[Job] = relationship(back_populates="versions", lazy="raise")


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in AGENT_STATUSES) + ")", name="status"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(63), unique=True)
    hostname: Mapped[str] = mapped_column(String(255))
    os: Mapped[str] = mapped_column(String(64))
    arch: Mapped[str] = mapped_column(String(64))
    agent_version: Mapped[str] = mapped_column(String(64))
    max_slots: Mapped[int] = mapped_column(Integer)
    queues: Mapped[list[str]] = mapped_column(ARRAY(Text))
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Execution backends the agent offers (e.g. "process", "docker"), reported on heartbeat.
    capabilities: Mapped[list[str]] = mapped_column(ARRAY(Text), default=lambda: ["process"])
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    last_seen_at: Mapped[datetime | None] = mapped_column(TZ)
    # Latest host stats from the heartbeat (protocol SystemStats); not a time series.
    system_stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    enrolled_via: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("enrollment_tokens.id"))
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    # When an "agent offline" notification was last queued (re-armed by the next heartbeat).
    offline_notified_at: Mapped[datetime | None] = mapped_column(TZ)


class AgentCredential(Base):
    __tablename__ = "agent_credentials"

    id: Mapped[uuid.UUID] = _pk()
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"))
    # SHA-256 of a 256-bit random secret; high entropy makes a slow KDF unnecessary.
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    # Set when rotated: the old credential keeps working briefly so in-flight requests succeed.
    expires_at: Mapped[datetime | None] = mapped_column(TZ)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[uuid.UUID] = _pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Shown in lists so admins can recognise a token without ever seeing the secret again.
    prefix: Mapped[str] = mapped_column(String(16), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    # Presets applied to agents enrolled with this token (empty = agent decides).
    queues: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    max_uses: Mapped[int | None] = mapped_column(Integer)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(f"status IN ({_RUN_STATUSES})", name="status"),
        CheckConstraint(
            "trigger IN ('manual', 'api', 'schedule', 'retry', 'rerun')", name="trigger"
        ),
        Index("ix_runs_job_created", "job_id", "created_at"),
        Index("ix_runs_status", "status"),
        Index("ix_runs_retry_due", "next_attempt_at", postgresql_where="status = 'RETRY_WAIT'"),
        UniqueConstraint("schedule_id", "scheduled_for"),
        Index(
            "ix_runs_workflow_run",
            "workflow_run_id",
            postgresql_where="workflow_run_id IS NOT NULL",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    job_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("job_versions.id"))
    job_version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20))
    queue: Mapped[str] = mapped_column(String(63))
    priority: Mapped[int] = mapped_column(Integer)
    spec_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    current_attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=1)
    # When the run is in RETRY_WAIT: the moment its next attempt becomes due.
    next_attempt_at: Mapped[datetime | None] = mapped_column(TZ)
    # A rerun is a new run; this points at the run it repeats.
    rerun_of: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    # Scheduled runs: which schedule and which slot. Unique together, so a slot can never
    # produce two runs, whatever the number of scheduler replicas or restarts.
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL")
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(TZ)
    # Task runs of a workflow run.
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL", use_alter=True)
    )
    task_key: Mapped[str | None] = mapped_column(String(63))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    error_summary: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = _created()
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()

    attempts: Mapped[list["RunAttempt"]] = relationship(
        back_populates="run", order_by="RunAttempt.attempt_no", lazy="raise"
    )


class RunAttempt(Base):
    __tablename__ = "run_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_no"),
        CheckConstraint(f"status IN ({_ATTEMPT_STATUSES})", name="status"),
        # The dispatcher scans this: queued work in priority order.
        Index(
            "ix_run_attempts_queued",
            "queue",
            "priority",
            "created_at",
            postgresql_where="status = 'QUEUED'",
        ),
        # The lease reaper (M2) scans this.
        Index(
            "ix_run_attempts_leased",
            "lease_expires_at",
            postgresql_where="status IN ('DISPATCHED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED')",
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    # Denormalised from the run so the claim query needs no join.
    queue: Mapped[str] = mapped_column(String(63))
    priority: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    lease_token: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(TZ)
    dispatched_at: Mapped[datetime | None] = mapped_column(TZ)
    acked_at: Mapped[datetime | None] = mapped_column(TZ)
    started_at: Mapped[datetime | None] = mapped_column(TZ)
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    pid: Mapped[int | None] = mapped_column(Integer)
    error_summary: Mapped[str | None] = mapped_column(Text)
    last_log_seq: Mapped[int | None] = mapped_column(BigInteger)
    log_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = _created()

    run: Mapped[Run] = relationship(back_populates="attempts", lazy="raise")


class RunEvent(Base):
    """Append-only history of every status change."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run", "run_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"))
    attempt_no: Mapped[int | None] = mapped_column(Integer)
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text, default="")
    actor: Mapped[str] = mapped_column(String(80))
    at: Mapped[datetime] = _created()


class LogChunk(Base):
    __tablename__ = "log_chunks"
    __table_args__ = (CheckConstraint("stream IN ('stdout', 'stderr', 'system')", name="stream"),)

    attempt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("run_attempts.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    stream: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(TZ)
    data: Mapped[str] = mapped_column(Text)


class QueueSettings(Base):
    """Optional per-queue settings. Queues exist implicitly by name; a row only adds limits."""

    __tablename__ = "queues"

    name: Mapped[str] = mapped_column(String(63), primary_key=True)
    max_concurrency: Mapped[int | None] = mapped_column(Integer)
    paused: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = _updated()


class IdempotencyKey(Base):
    """Stored response for a client-supplied Idempotency-Key, so retried requests are no-ops."""

    __tablename__ = "idempotency_keys"

    scope: Mapped[str] = mapped_column(String(120), primary_key=True)
    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int | None] = mapped_column(Integer)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class ControlPlaneHeartbeat(Base):
    """One row per running API replica, refreshed every few seconds.

    Lets the lease reaper tell "an agent went silent" from "the control plane was down and no
    agent *could* renew": leases are only reaped once an API has been continuously up for a
    full lease period.
    """

    __tablename__ = "control_plane_heartbeats"

    replica_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    up_since: Mapped[datetime] = mapped_column(TZ)
    last_alive_at: Mapped[datetime] = mapped_column(TZ)


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        CheckConstraint("kind IN ('cron', 'interval')", name="kind"),
        CheckConstraint("misfire_policy IN ('skip', 'run_once', 'run_all')", name="misfire"),
        CheckConstraint("overlap_policy IN ('allow', 'skip')", name="overlap"),
        CheckConstraint(
            "(kind = 'cron' AND cron IS NOT NULL) OR (kind = 'interval' AND interval_seconds > 0)",
            name="definition",
        ),
        Index("ix_schedules_due", "next_fire_at", postgresql_where="enabled"),
        CheckConstraint("(job_id IS NULL) <> (workflow_id IS NULL)", name="one_target"),
    )

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120))
    # Exactly one target: a job or a workflow.
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE", use_alter=True)
    )
    kind: Mapped[str] = mapped_column(String(10))
    cron: Mapped[str | None] = mapped_column(String(120))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    misfire_policy: Mapped[str] = mapped_column(String(10), default="run_once")
    misfire_grace_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_catchup: Mapped[int] = mapped_column(Integer, default=5)
    # "skip": don't start a slot while a previous run of this schedule is still active.
    overlap_policy: Mapped[str] = mapped_column(String(10), default="allow")
    enabled: Mapped[bool] = mapped_column(default=True)
    next_fire_at: Mapped[datetime | None] = mapped_column(TZ)
    last_fired_at: Mapped[datetime | None] = mapped_column(TZ)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL", use_alter=True)
    )
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    last_skipped_at: Mapped[datetime | None] = mapped_column(TZ)
    last_skip_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(63), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    current_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class WorkflowVersion(Base):
    """Immutable. Editing a workflow inserts a new version."""

    __tablename__ = "workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version"),)

    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(Text, default="")  # YAML as written (for editing)
    created_at: Mapped[datetime] = _created()


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        CheckConstraint("status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')", name="status"),
        UniqueConstraint("schedule_id", "scheduled_for"),
        Index("ix_workflow_runs_running", "updated_at", postgresql_where="status = 'RUNNING'"),
        Index("ix_workflow_runs_workflow_created", "workflow_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    workflow_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"))
    workflow_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_versions.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(12))
    trigger: Mapped[str] = mapped_column(String(16))
    rerun_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL")
    )
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL")
    )
    scheduled_for: Mapped[datetime | None] = mapped_column(TZ)
    # Per task: {"state": TaskState, "run_id", "reused_from", "note"} (ids as strings or null)
    tasks: Mapped[dict[str, Any]] = mapped_column(JSONB)
    started_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


ROLES = ("viewer", "operator", "admin")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="role"),)

    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(254), unique=True)  # stored lowercase
    name: Mapped[str] = mapped_column(String(120), default="")
    password_hash: Mapped[str] = mapped_column(String(255))  # Argon2id
    role: Mapped[str] = mapped_column(String(10))
    disabled: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(TZ)


class UserSession(Base):
    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(TZ)
    last_seen_at: Mapped[datetime] = _created()
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")


class ApiToken(Base):
    __tablename__ = "api_tokens"

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(TZ)
    expires_at: Mapped[datetime | None] = mapped_column(TZ)
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)


class AuditEvent(Base):
    """Append-only record of who changed what (every mutating API request, plus logins)."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_at", "at"),
        Index("ix_audit_events_actor", "actor", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = _created()
    actor: Mapped[str] = mapped_column(String(254))  # user email, "agent:<name>", "anonymous"
    action: Mapped[str] = mapped_column(String(200))  # e.g. "POST /api/v1/jobs/{job_id}/runs"
    target_type: Mapped[str] = mapped_column(String(40), default="")
    target_id: Mapped[str] = mapped_column(String(200), default="")
    status_code: Mapped[int] = mapped_column(Integer)
    ip: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class Secret(Base):
    """A named secret, AES-256-GCM encrypted with the server's TORQRUN_SECRET_KEY.

    The plaintext never leaves the control plane except inside a job assignment sent to the
    agent that runs a job referencing it (over TLS), and is never returned by the API.
    """

    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(63), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(16))  # which master key encrypted it
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


NOTIFICATION_KINDS = ("webhook", "slack", "teams", "email")
NOTIFICATION_EVENTS = (
    "run.failed",  # FAILED, TIMED_OUT or LOST after all retries
    "run.succeeded",
    "workflow.failed",
    "workflow.succeeded",
    "agent.offline",
)


class NotificationChannel(Base):
    """Where to send notifications. The destination (URL, signing key, recipients) is
    encrypted like a secret: webhook URLs are credentials too."""

    __tablename__ = "notification_channels"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{k}'" for k in NOTIFICATION_KINDS) + ")", name="kind"
        ),
    )

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    events: Mapped[list[str]] = mapped_column(ARRAY(Text))
    # Only notify about runs in these queues (empty = all queues).
    queues: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    enabled: Mapped[bool] = mapped_column(default=True)
    target_hint: Mapped[str] = mapped_column(String(200), default="")  # masked, for display
    config_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    config_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(16))
    created_by: Mapped[str] = mapped_column(String(254), default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class NotificationDelivery(Base):
    """Transactional outbox: written in the same transaction as the event it reports, then
    delivered (with retries) by the scheduler. ``dedupe_key`` makes enqueueing idempotent."""

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        UniqueConstraint("channel_id", "dedupe_key"),
        CheckConstraint("status IN ('pending', 'sending', 'sent', 'failed')", name="status"),
        Index("ix_notification_deliveries_due", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = _pk()
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notification_channels.id", ondelete="CASCADE"), index=True
    )
    event: Mapped[str] = mapped_column(String(40))
    dedupe_key: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(10), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = _created()
    locked_until: Mapped[datetime | None] = mapped_column(TZ)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    sent_at: Mapped[datetime | None] = mapped_column(TZ)


class Artifact(Base):
    """A file a run produced (written to $TORQRUN_ARTIFACTS_DIR), stored by the API."""

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("attempt_id", "name"),)

    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("run_attempts.id", ondelete="CASCADE"))
    attempt_no: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(500))  # relative to the artifact store root
    created_at: Mapped[datetime] = _created()
