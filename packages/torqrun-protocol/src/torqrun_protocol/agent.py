"""Agent <-> control plane messages (``/api/v1/agent/*``).

Flow: enroll once -> heartbeat every few seconds -> long-poll ``claim`` -> per assignment:
``ack`` (only then spawn) -> ``started`` -> ``logs``* -> ``complete``. Every per-attempt call
carries the attempt's ``lease_token``; a stale token gets HTTP 409 and the agent must stop
that work. See docs/architecture/ARCHITECTURE.md §5.
"""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

from torqrun_protocol.jobs import JobSpec

PROTOCOL_VERSION = 1
PROTOCOL_HEADER = "X-Torqrun-Protocol"

MAX_LOG_CHUNK_BYTES = 64 * 1024
MAX_LOG_BATCH_CHUNKS = 1000

Label = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,62}$")]


class _Msg(BaseModel):
    # Unknown fields are ignored in both directions so agents and control plane can be upgraded
    # independently (one release apart). Every field added later must have a default.
    model_config = ConfigDict(extra="ignore")


class AssignedSpec(JobSpec):
    """A job spec as an agent reads it: tolerant of fields added in newer releases."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class EnrollRequest(_Msg):
    enrollment_token: str = Field(min_length=8, max_length=256)
    name: Label
    hostname: str = Field(max_length=255)
    os: str = Field(max_length=64)
    arch: str = Field(max_length=64)
    agent_version: str = Field(max_length=64)
    max_slots: int = Field(ge=1, le=256)
    queues: list[Label] = Field(default_factory=lambda: ["default"], min_length=1, max_length=32)
    tags: list[Label] = Field(default_factory=list, max_length=64)
    capabilities: list[Label] = Field(default_factory=lambda: ["process"], max_length=16)


class EnrollResponse(_Msg):
    agent_id: UUID
    credential: str = Field(description="Shown once. Send as 'Authorization: Bearer <credential>'.")
    # Effective settings: an enrollment token may preset queues/tags for the agent.
    queues: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class RotateResponse(_Msg):
    credential: str = Field(description="New credential. The old one stops working in 5 minutes.")


class LeaseRef(_Msg):
    attempt_id: UUID
    lease_token: str


class SystemStats(_Msg):
    """Host facts and utilisation, sampled by the agent on every heartbeat. All optional:
    platforms that can't provide a value simply omit it."""

    os_pretty: str | None = Field(default=None, max_length=128)
    kernel: str | None = Field(default=None, max_length=128)
    python_version: str | None = Field(default=None, max_length=32)
    cpu_count: int | None = Field(default=None, ge=1)
    load_1m: float | None = Field(default=None, ge=0)
    load_5m: float | None = Field(default=None, ge=0)
    load_15m: float | None = Field(default=None, ge=0)
    mem_total_bytes: int | None = Field(default=None, ge=0)
    mem_available_bytes: int | None = Field(default=None, ge=0)
    disk_total_bytes: int | None = Field(default=None, ge=0)
    disk_free_bytes: int | None = Field(default=None, ge=0)
    uptime_seconds: float | None = Field(default=None, ge=0)


class HeartbeatRequest(_Msg):
    running: list[LeaseRef] = Field(default_factory=list, max_length=256)
    free_slots: int = Field(ge=0, le=256)
    system: SystemStats | None = None
    capabilities: list[Label] | None = Field(default=None, max_length=16)


class HeartbeatResponse(_Msg):
    server_time: datetime
    agent_status: str = "ONLINE"
    lease_ttl_seconds: int
    # Attempts the agent reported but no longer holds (lease expired or reassigned): stop them.
    revoked_leases: list[UUID] = Field(default_factory=list)
    # Attempts a user asked to cancel: kill the process, then report outcome "cancelled".
    cancel: list[UUID] = Field(default_factory=list)


class ClaimRequest(_Msg):
    free_slots: int = Field(ge=1, le=256)
    wait_seconds: float = Field(default=20.0, ge=0, le=25)


class Assignment(_Msg):
    run_id: UUID
    attempt_id: UUID
    attempt_no: int
    lease_token: str
    lease_expires_at: datetime
    job_id: UUID
    job_name: str
    job_version: int
    spec: AssignedSpec
    # Set for task runs of a workflow.
    workflow_run_id: UUID | None = None
    task_key: str | None = None
    # Resolved values of spec.secrets (env name -> value). Never logged; the agent redacts them
    # from output. Names listed in missing_secrets could not be resolved: the run must fail.
    secret_env: dict[str, str] = Field(default_factory=dict, repr=False)
    missing_secrets: list[str] = Field(default_factory=list)

    @field_serializer("spec")
    def _spec_without_defaults(self, spec: JobSpec) -> dict[str, Any]:
        # Fields at their default values are left out, so a feature added in a newer release
        # is invisible to older agents unless a job actually uses it (and such jobs are
        # capability-gated, e.g. the docker executor).
        return spec.model_dump(mode="json", exclude_defaults=True)


class ClaimResponse(_Msg):
    assignments: list[Assignment]


class LeaseRequest(_Msg):
    lease_token: str


class StartedRequest(_Msg):
    lease_token: str
    pid: int = Field(ge=1)
    started_at: datetime


class LogChunk(_Msg):
    seq: int = Field(ge=0, description="Monotonic per attempt, shared by all streams.")
    stream: Literal["stdout", "stderr", "system"]
    ts: datetime
    data: str = Field(max_length=MAX_LOG_CHUNK_BYTES)


class LogBatch(_Msg):
    lease_token: str
    chunks: list[LogChunk] = Field(min_length=1, max_length=MAX_LOG_BATCH_CHUNKS)


class LogBatchResponse(_Msg):
    accepted: int
    # Piggybacked on every log upload so chatty jobs are cancelled within ~1s.
    cancel_requested: bool = False


class CompleteRequest(_Msg):
    lease_token: str
    # "lost": the agent itself found the attempt interrupted (e.g. it restarted mid-run).
    outcome: Literal["succeeded", "failed", "timed_out", "cancelled", "lost"]
    exit_code: int | None = Field(description="None when the process could not be started.")
    finished_at: datetime
    error_summary: str | None = Field(default=None, max_length=4000)
    last_log_seq: int | None = Field(default=None, ge=0)
