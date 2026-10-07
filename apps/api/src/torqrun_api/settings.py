"""Runtime configuration, read from ``TORQRUN_*`` environment variables."""

from typing import Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TORQRUN_", extra="ignore")

    # Kept secret so the password never shows up in reprs, logs or error pages.
    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"

    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0)

    # Agent protocol
    lease_ttl_seconds: int = Field(default=60, ge=10)
    claim_poll_interval_seconds: float = Field(default=0.5, gt=0)
    agent_offline_after_seconds: int = Field(default=30, ge=5)
    # Dispatched attempts not acknowledged within this window go back to the queue.
    dispatch_ack_timeout_seconds: float = Field(default=30.0, ge=1)
    # Development-only shared enrollment token so `make up` can enroll its bundled agent.
    # Real deployments use single-use tokens (M3). Rejected outside development.
    dev_enrollment_token: SecretStr | None = None

    # Externally reachable URL (e.g. https://torqrun.example.com) for install commands. When
    # unset, derived from each request (works behind the bundled nginx / Caddy).
    public_url: str | None = None
    # Directory with install-agent.sh and dist/*.whl (built into the API image).
    agent_assets_dir: str = "/opt/torqrun/agent"

    # SSE polling cadence for live logs/status.
    stream_poll_interval_seconds: float = Field(default=0.5, gt=0)

    # Artifacts (M7): files jobs leave in $TORQRUN_ARTIFACTS_DIR, stored here.
    artifacts_dir: str = "/var/lib/torqrun/artifacts"
    max_artifact_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    max_attempt_artifact_bytes: int = Field(default=500 * 1024 * 1024, ge=1024)
    max_artifacts_per_attempt: int = Field(default=100, ge=1)
    artifact_retention_days: float = Field(default=30.0, gt=0)

    # Users & security (M6)
    # Master key for encrypted data (Enterprise: secrets, notification channels). >= 32 chars.
    secret_key: SecretStr | None = None
    # Created on startup when no user exists yet (otherwise the first visitor sets one up).
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: SecretStr | None = None
    session_ttl_hours: float = Field(default=12.0, gt=0, le=24 * 30)
    # Secure cookies need HTTPS; defaults to on in production.
    cookie_secure: bool | None = None
    login_max_attempts: int = Field(default=10, ge=1)
    login_window_seconds: float = Field(default=300.0, gt=0)
    # Optional bearer token for GET /metrics; when unset the endpoint is open (keep it internal).
    metrics_token: SecretStr | None = None

    @field_validator(
        "secret_key",
        "bootstrap_admin_email",
        "bootstrap_admin_password",
        "metrics_token",
        "public_url",
        "dev_enrollment_token",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, v: object) -> object:
        # docker compose passes `${VAR:-}` through as an empty string.
        return None if v == "" else v

    @property
    def secure_cookies(self) -> bool:
        return (
            self.cookie_secure
            if self.cookie_secure is not None
            else self.environment == "production"
        )

    @model_validator(mode="after")
    def _guard_insecure_modes(self) -> Self:
        if self.dev_enrollment_token is not None:
            if self.environment != "development":
                raise ValueError("TORQRUN_DEV_ENROLLMENT_TOKEN is only allowed in development")
            if len(self.dev_enrollment_token.get_secret_value()) < 16:
                raise ValueError("TORQRUN_DEV_ENROLLMENT_TOKEN must be at least 16 characters")
        if self.secret_key is not None and len(self.secret_key.get_secret_value()) < 32:
            raise ValueError("TORQRUN_SECRET_KEY must be at least 32 characters")
        https = (self.public_url or "https://").startswith("https://")
        if self.environment == "production" and not https:
            raise ValueError("TORQRUN_PUBLIC_URL must use https:// in production")
        if (self.bootstrap_admin_email is None) != (self.bootstrap_admin_password is None):
            raise ValueError("set both TORQRUN_BOOTSTRAP_ADMIN_EMAIL and _PASSWORD, or neither")
        return self
