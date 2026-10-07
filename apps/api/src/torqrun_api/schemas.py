"""User-facing API request/response models (``/api/v1``)."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from torqrun_core.states import RunStatus
from torqrun_protocol.agent import SystemStats
from torqrun_protocol.jobs import JobSpec

JobName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


# Jobs ----------------------------------------------------------------------------------


class JobCreate(_In):
    name: JobName = Field(description="Lowercase letters, digits, '-', '_', '.'; unique.")
    description: str = Field(default="", max_length=2000)
    spec: JobSpec


class JobUpdate(_In):
    description: str = Field(default="", max_length=2000)
    spec: JobSpec


class RunBrief(_Out):
    id: UUID
    status: RunStatus
    created_at: datetime
    finished_at: datetime | None


class JobOut(_Out):
    id: UUID
    name: str
    description: str
    current_version: int
    spec: JobSpec
    created_at: datetime
    updated_at: datetime
    last_run: RunBrief | None = None
    recent_runs: list[RunBrief] = Field(default_factory=list, description="Newest first, up to 12")


class JobVersionOut(_Out):
    version: int
    spec: JobSpec
    created_at: datetime


# Runs ----------------------------------------------------------------------------------


class RunOut(_Out):
    id: UUID
    job_id: UUID
    job_name: str
    job_version: int
    trigger: str
    status: RunStatus
    queue: str
    current_attempt: int
    max_attempts: int
    next_attempt_at: datetime | None = Field(description="Set while waiting to retry")
    rerun_of: UUID | None
    schedule_id: UUID | None = None
    scheduled_for: datetime | None = None
    exit_code: int | None
    error_summary: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None = Field(description="finished_at - started_at, when both exist")


class AttemptOut(_Out):
    attempt_no: int
    status: RunStatus
    agent_id: UUID | None
    agent_name: str | None
    dispatched_at: datetime | None
    acked_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    exit_code: int | None
    pid: int | None
    error_summary: str | None
    log_bytes: int
    last_log_seq: int | None


class RunEventOut(_Out):
    attempt_no: int | None
    from_status: RunStatus | None
    to_status: RunStatus
    reason: str
    actor: str
    at: datetime


class RunDetail(RunOut):
    spec: JobSpec
    attempts: list[AttemptOut]
    events: list[RunEventOut]


class LogChunkOut(_Out):
    seq: int
    stream: Literal["stdout", "stderr", "system"]
    ts: datetime
    data: str


class LogPage(BaseModel):
    attempt_no: int
    chunks: list[LogChunkOut]
    next_after_seq: int = Field(description="Pass as after_seq to continue.")
    complete: bool = Field(description="True when the attempt has finished and no more logs follow")


# Agents --------------------------------------------------------------------------------


class AgentActiveRun(BaseModel):
    run_id: UUID
    job_id: UUID
    job_name: str
    status: RunStatus
    started_at: datetime | None


class AgentOut(_Out):
    id: UUID
    name: str
    hostname: str
    os: str
    arch: str
    agent_version: str
    status: str
    connected: bool = Field(description="Heartbeat seen within the offline threshold")
    max_slots: int
    running: int
    queues: list[str]
    tags: list[str]
    capabilities: list[str] = Field(default_factory=lambda: ["process"])
    last_seen_at: datetime | None
    created_at: datetime
    system: SystemStats | None = Field(description="Latest host stats from the heartbeat")
    active_runs: list[AgentActiveRun]


# Queues --------------------------------------------------------------------------------


class QueueOut(BaseModel):
    name: str
    max_concurrency: int | None
    paused: bool
    queued: int
    running: int
    agents_online: int = Field(description="Connected agents serving this queue")


class QueueUpdate(_In):
    max_concurrency: int | None = Field(default=None, ge=1, le=10000)
    paused: bool = False
