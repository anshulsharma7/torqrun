"""Job specification: the immutable description of *what* to run.

A spec is stored with each job version and copied onto every run, so a run always shows
exactly what was executed even after the job is edited.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

MAX_SCRIPT_BYTES = 256 * 1024
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED_ENV_PREFIX = "TORQRUN_"
SECRET_NAME_PATTERN = r"^[a-z0-9][a-z0-9_.-]{0,62}$"  # noqa: S105 - a name pattern

QueueName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")]
Arg = Annotated[str, StringConstraints(max_length=4096)]


class RetrySpec(BaseModel):
    """How a failed run is retried. Attempts of one run share its ID and history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_attempts: int = Field(default=1, ge=1, le=20, description="1 = no retries.")
    backoff_seconds: float = Field(default=10.0, ge=1, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1, le=10)
    max_backoff_seconds: float = Field(default=600.0, ge=1, le=86400)
    retry_on_timeout: bool = False


# A container image reference (registry/name:tag@digest). Must not start with "-" so it can
# never be read as a docker CLI option.
IMAGE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._/:@+-]{0,254}$"


class ContainerSpec(BaseModel):
    """How the ``docker`` executor runs the script."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    image: str = Field(pattern=IMAGE_PATTERN, examples=["python:3.12-slim", "bash:5"])
    network: Literal["bridge", "none"] = Field(
        default="bridge", description="'none' cuts the job off from the network entirely."
    )
    memory_mb: int | None = Field(default=None, ge=16, le=1024 * 1024)
    cpus: float | None = Field(default=None, gt=0, le=512)
    pull: Literal["missing", "always", "never"] = "missing"


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: Literal["python", "shell"]
    script: str = Field(min_length=1, description="Script source, run as a file.")
    args: list[Arg] = Field(default_factory=list, max_length=100)
    env: dict[str, Annotated[str, StringConstraints(max_length=32 * 1024)]] = Field(
        default_factory=dict, max_length=200
    )
    timeout_seconds: int = Field(default=3600, ge=1, le=7 * 24 * 3600)
    queue: QueueName = "default"
    priority: int = Field(default=0, ge=-100, le=100, description="Higher runs first.")
    retry: RetrySpec = Field(default_factory=RetrySpec)
    interrupt_policy: Literal["fail", "retry"] = Field(
        default="fail",
        description=(
            "When the agent disappears mid-run (LOST), the script may have partly run. "
            "'retry' declares it safe to run again; 'fail' (default) does not retry."
        ),
    )
    max_concurrent: int | None = Field(
        default=None,
        ge=1,
        le=1000,
        description="Max simultaneous runs of this job; None = no limit.",
    )
    executor: Literal["process", "docker"] = Field(
        default="process",
        description="'process': a child process on the agent host (no isolation). 'docker': a "
        "throwaway container; only agents with Docker pick these runs up.",
    )
    container: ContainerSpec | None = None
    secrets: dict[str, Annotated[str, StringConstraints(pattern=SECRET_NAME_PATTERN)]] = Field(
        default_factory=dict,
        max_length=50,
        description="Environment variable -> secret name. Values are injected at run time and "
        "redacted from logs; they are never stored in the job.",
    )

    @field_validator("script")
    @classmethod
    def _script_size(cls, v: str) -> str:
        if len(v.encode()) > MAX_SCRIPT_BYTES:
            raise ValueError(f"script exceeds {MAX_SCRIPT_BYTES // 1024} KiB")
        return v

    @model_validator(mode="after")
    def _executor(self) -> Self:
        if self.executor == "docker" and self.container is None:
            raise ValueError("the docker executor needs container.image")
        if self.executor == "process" and self.container is not None:
            raise ValueError("container settings need executor 'docker'")
        return self

    @model_validator(mode="after")
    def _secret_env(self) -> Self:
        for name in self.secrets:
            if not ENV_NAME.match(name) or name.upper().startswith(RESERVED_ENV_PREFIX):
                raise ValueError(f"secrets: invalid environment variable name {name!r}")
            if name in self.env:
                raise ValueError(f"{name!r} is set both in env and secrets")
        return self

    @field_validator("env")
    @classmethod
    def _env_names(cls, v: dict[str, str]) -> dict[str, str]:
        for name in v:
            if not ENV_NAME.match(name):
                raise ValueError(f"invalid environment variable name {name!r}")
            if name.upper().startswith(RESERVED_ENV_PREFIX):
                raise ValueError(f"{name!r}: the {RESERVED_ENV_PREFIX} prefix is reserved")
        return v
