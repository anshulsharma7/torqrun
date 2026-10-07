"""Idempotency-Key support for endpoints that create runs.

The key is reserved and the response stored in the *same* transaction as the work, so a retried
or concurrent duplicate request returns the original response instead of creating a second run.
Keys are scoped per endpoint and target, and kept for 24 h (purged by the scheduler).
"""

from collections.abc import Awaitable, Callable

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_db import runs as ops

REPLAY_HEADER = "Idempotent-Replayed"


def validate_key(key: str | None) -> str | None:
    if key is not None and not (1 <= len(key) <= 255 and key.isprintable()):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Idempotency-Key must be 1-255 printable characters"
        )
    return key


async def idempotent(
    session: AsyncSession,
    *,
    key: str | None,
    scope: str,
    payload: object,
    work: Callable[[], Awaitable[BaseModel]],
    status_code: int = status.HTTP_201_CREATED,
) -> JSONResponse:
    async with session.begin():
        if key is not None:
            try:
                stored = await ops.idempotency_begin(session, scope, key, ops.request_hash(payload))
            except ops.IdempotencyConflictError:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail="Idempotency-Key was already used with a different request",
                ) from None
            if stored is not None and stored.response is not None:
                return JSONResponse(
                    stored.response,
                    status_code=stored.status_code or status_code,
                    headers={REPLAY_HEADER: "true"},
                )
        result = (await work()).model_dump(mode="json")
        if key is not None:
            await ops.idempotency_store(session, scope, key, status_code, result)
    return JSONResponse(result, status_code=status_code)
