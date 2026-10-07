"""Buffers job output and ships it to the control plane in ordered, idempotent batches."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

import httpx

from torqrun_agent.client import AuthError, ControlPlaneError, LeaseLostError
from torqrun_protocol.agent import LogChunk

logger = logging.getLogger(__name__)

BATCH_CHUNKS = 500
BATCH_BYTES = 512 * 1024

SendBatch = Callable[[list[LogChunk]], Awaitable[object]]


class LogShipper:
    """Assigns sequence numbers and sends chunks periodically.

    ``emit`` is synchronous and never blocks the output readers; ``run`` sends in the
    background. A batch is removed from the buffer only after the server accepted it, and the
    server ignores duplicate (attempt, seq) pairs, so a retried batch can't duplicate lines.
    Output beyond ``max_bytes`` is dropped (with one marker line) to protect the control plane.
    """

    def __init__(self, send: SendBatch, *, max_bytes: int, flush_interval: float) -> None:
        self._send = send
        self._max_bytes = max_bytes
        self._interval = flush_interval
        self._buffer: list[LogChunk] = []
        self._next_seq = 0
        self._bytes = 0
        self._truncated = False
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()

    def unsent(self) -> list[LogChunk]:
        """Chunks not yet accepted by the control plane (kept when the final flush fails)."""
        return list(self._buffer)

    @property
    def last_seq(self) -> int | None:
        return self._next_seq - 1 if self._next_seq else None

    def emit(self, stream: Literal["stdout", "stderr", "system"], data: str) -> None:
        if self._truncated:
            return
        size = len(data.encode())
        if self._bytes + size > self._max_bytes:
            self._truncated = True
            data = (
                f"[torqrun] output limit of {self._max_bytes} bytes reached; "
                "further output dropped\n"
            )
            stream = "system"
        self._bytes += size
        self._buffer.append(
            LogChunk(seq=self._next_seq, stream=stream, ts=datetime.now(UTC), data=data)
        )
        self._next_seq += 1
        if len(self._buffer) >= BATCH_CHUNKS:
            self._wake.set()

    async def run(self) -> None:
        """Flush periodically until cancelled.

        Transient failures keep the buffer and retry on the next tick (output is bounded by
        ``max_bytes``). Lease loss and auth failures are fatal and propagate to the caller,
        which then stops the job.
        """
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)
            self._wake.clear()
            try:
                await self.flush()
            except (LeaseLostError, AuthError):
                raise
            except (httpx.HTTPError, ControlPlaneError) as exc:
                logger.warning("log upload failed (%s); will retry", exc)

    async def flush(self) -> None:
        async with self._lock:
            while self._buffer:
                batch: list[LogChunk] = []
                size = 0
                for chunk in self._buffer:
                    if batch and (len(batch) >= BATCH_CHUNKS or size >= BATCH_BYTES):
                        break
                    batch.append(chunk)
                    size += len(chunk.data)
                await self._send(batch)
                del self._buffer[: len(batch)]
