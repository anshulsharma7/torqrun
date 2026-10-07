"""HTTP client for the control plane's agent protocol."""

import asyncio
import logging
import os
import random
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel

from torqrun_agent import __version__
from torqrun_protocol.agent import (
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
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

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {502, 503, 504}


class _Empty(BaseModel):
    pass


class ControlPlaneError(Exception):
    """Non-retryable protocol error (bad request, unexpected status)."""


class AuthError(ControlPlaneError):
    """Credential rejected or agent revoked. The agent cannot continue."""


class LeaseLostError(ControlPlaneError):
    """The control plane no longer considers this agent the holder of the attempt."""


class ControlPlane:
    def __init__(
        self,
        base_url: str,
        credential: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retries: int = 6,
        ca_file: str | None = None,
    ) -> None:
        headers = {
            PROTOCOL_HEADER: str(PROTOCOL_VERSION),
            "User-Agent": f"torqrun-agent/{__version__}",
        }
        if credential:
            headers["Authorization"] = f"Bearer {credential}"
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers=headers,
            timeout=httpx.Timeout(30.0),
            transport=transport,
            verify=ca_file if ca_file else True,
        )
        self._retries = retries

    def set_credential(self, credential: str) -> None:
        self._http.headers["Authorization"] = f"Bearer {credential}"

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _post(
        self,
        path: str,
        body: BaseModel,
        *,
        retries: int | None = None,
        timeout_seconds: float | None = None,
    ) -> httpx.Response:
        attempts = (self._retries if retries is None else retries) + 1
        payload = body.model_dump(mode="json")
        for attempt in range(1, attempts + 1):
            try:
                kwargs: dict[str, Any] = {"json": payload}
                if timeout_seconds is not None:
                    kwargs["timeout"] = timeout_seconds
                response = await self._http.post(path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise
                reason = f"{type(exc).__name__}"
            else:
                if response.status_code in RETRYABLE_STATUS and attempt < attempts:
                    reason = f"HTTP {response.status_code}"
                else:
                    return self._check(response)
            delay = min(30.0, 0.5 * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0)  # noqa: S311
            logger.warning("request to %s failed (%s); retrying in %.1fs", path, reason, delay)
            await asyncio.sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _check(response: httpx.Response) -> httpx.Response:
        if response.is_success:
            return response
        detail: object
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:200]
        if response.status_code in (401, 403):
            raise AuthError(f"HTTP {response.status_code}: {detail}")
        if response.status_code == 409 and detail == "lease_revoked":
            raise LeaseLostError(str(detail))
        raise ControlPlaneError(f"HTTP {response.status_code}: {detail}")

    async def enroll(self, body: EnrollRequest) -> EnrollResponse:
        r = await self._post("/api/v1/agent/enroll", body, retries=2)
        return EnrollResponse.model_validate(r.json())

    async def rotate_credential(self) -> RotateResponse:
        r = await self._post("/api/v1/agent/credentials/rotate", _Empty(), retries=2)
        return RotateResponse.model_validate(r.json())

    async def drain(self) -> None:
        await self._post("/api/v1/agent/drain", _Empty(), retries=2)

    async def heartbeat(self, body: HeartbeatRequest) -> HeartbeatResponse:
        r = await self._post("/api/v1/agent/heartbeat", body, retries=0)
        return HeartbeatResponse.model_validate(r.json())

    async def claim(self, body: ClaimRequest) -> ClaimResponse:
        r = await self._post(
            "/api/v1/agent/claim", body, retries=0, timeout_seconds=body.wait_seconds + 15
        )
        return ClaimResponse.model_validate(r.json())

    async def ack(self, attempt_id: UUID, lease_token: str) -> None:
        await self._post(
            f"/api/v1/agent/attempts/{attempt_id}/ack", LeaseRequest(lease_token=lease_token)
        )

    async def started(self, attempt_id: UUID, body: StartedRequest) -> None:
        await self._post(f"/api/v1/agent/attempts/{attempt_id}/started", body)

    async def logs(self, attempt_id: UUID, body: LogBatch) -> LogBatchResponse:
        r = await self._post(f"/api/v1/agent/attempts/{attempt_id}/logs", body)
        return LogBatchResponse.model_validate(r.json())

    async def upload_artifact(
        self, attempt_id: UUID, lease_token: str, name: str, path: Path
    ) -> None:
        """Stream one regular file to the control plane."""

        # O_NOFOLLOW: a job that swaps the file for a symlink can't make us upload other files.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)

        async def body() -> AsyncIterator[bytes]:
            with os.fdopen(fd, "rb") as fh:
                while chunk := await asyncio.to_thread(fh.read, 1024 * 1024):
                    yield chunk

        try:
            response = await self._http.put(
                f"/api/v1/agent/attempts/{attempt_id}/artifacts/{name}",
                content=body(),
                headers={
                    "X-Torqrun-Lease": lease_token,
                    "Content-Type": "application/octet-stream",
                },
                timeout=httpx.Timeout(300.0, connect=10.0),
            )
        except httpx.TransportError as exc:
            raise ControlPlaneError(f"{type(exc).__name__}") from None
        self._check(response)

    async def complete(self, attempt_id: UUID, body: CompleteRequest) -> None:
        # Kept short: an undelivered result is spooled to disk and re-sent later.
        await self._post(f"/api/v1/agent/attempts/{attempt_id}/complete", body, retries=3)
