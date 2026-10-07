"""Agent configuration from ``TORQRUN_AGENT_*`` environment variables."""

import os
import re
import socket
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _default_state_dir() -> Path:
    if os.geteuid() == 0:
        return Path("/var/lib/torqrun-agent")
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "torqrun-agent"


def _default_name() -> str:
    # Agent names must match the protocol's label pattern.
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]", "-", socket.gethostname()).strip("-.")
    return (cleaned or "agent")[:63]


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TORQRUN_AGENT_", extra="ignore")

    server_url: str = Field(description="Control-plane base URL, e.g. https://torqrun.example.com")
    enrollment_token: SecretStr | None = None
    name: str = Field(default_factory=_default_name)
    state_dir: Path = Field(default_factory=_default_state_dir)

    max_slots: int = Field(default=2, ge=1, le=256)
    queues: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["default"])
    tags: Annotated[list[str], NoDecode] = Field(default_factory=list)

    heartbeat_interval_seconds: float = Field(default=5.0, gt=0)
    claim_wait_seconds: float = Field(default=20.0, ge=0, le=25)
    log_flush_interval_seconds: float = Field(default=0.5, gt=0)
    max_log_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    kill_grace_seconds: float = Field(default=10.0, ge=0)
    shutdown_grace_seconds: float = Field(default=20.0, ge=0)
    keep_workspaces: bool = False

    # PATH given to jobs. Deliberately minimal and independent of the agent's own environment
    # (which, in a container, points at the agent's private virtualenv).
    job_path: str = "/usr/local/bin:/usr/bin:/bin"

    ca_file: Path | None = Field(
        default=None,
        description="CA bundle to trust for the control plane (private PKI / self-signed).",
    )
    credential_rotate_days: float = Field(
        default=7.0, gt=0, description="Rotate the agent credential this often."
    )

    docker: Literal["auto", "on", "off"] = Field(
        default="auto",
        description="Offer the container executor: 'auto' if a Docker daemon answers.",
    )

    allow_insecure_http: bool = Field(
        default=False, description="Allow plain http:// to a non-local server (development only)."
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("queues", "tags", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> object:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @field_validator("server_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @model_validator(mode="after")
    def _require_tls(self) -> Self:
        parts = urlsplit(self.server_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"invalid server URL {self.server_url!r}")
        if (
            parts.scheme == "http"
            and parts.hostname not in LOCAL_HOSTS
            and not self.allow_insecure_http
        ):
            raise ValueError(
                "refusing plain http:// to a remote server: agent credentials would travel "
                "unencrypted. Use https://, or set TORQRUN_AGENT_ALLOW_INSECURE_HTTP=true "
                "on a trusted private network."
            )
        return self
