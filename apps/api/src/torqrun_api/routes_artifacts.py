"""Artifacts: files a run leaves in ``$TORQRUN_ARTIFACTS_DIR``, uploaded by its agent.

Stored on the API's local disk (``TORQRUN_ARTIFACTS_DIR``; a volume in Compose). With several
API replicas, that directory must be shared storage. Uploads are streamed to disk with size
limits; the lease is checked before and after, so only the agent currently running the attempt
can attach files to it. Downloads are always served as ``application/octet-stream``
attachments, so a stored HTML file can never execute in the UI's origin.
"""

import asyncio
import contextlib
import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from torqrun_api.agent_auth import AgentDep
from torqrun_api.deps import SessionDep, SettingsDep
from torqrun_api.settings import Settings
from torqrun_db import runs as ops
from torqrun_db.models import Artifact, Run

logger = logging.getLogger(__name__)

agent_router = APIRouter(prefix="/api/v1/agent", tags=["agent protocol"])
router = APIRouter(prefix="/api/v1/runs", tags=["runs"])

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
CHUNK = 1024 * 1024


class ArtifactOut(BaseModel):
    id: uuid.UUID
    attempt_no: int
    name: str
    size_bytes: int
    sha256: str
    created_at: datetime


def store_root(settings: Settings) -> Path:
    return Path(settings.artifacts_dir)


def _conflict() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail="lease_revoked")


@agent_router.put("/attempts/{attempt_id}/artifacts/{name}", status_code=status.HTTP_201_CREATED)
async def upload_artifact(
    attempt_id: uuid.UUID,
    name: str,
    request: Request,
    agent: AgentDep,
    session: SessionDep,
    settings: SettingsDep,
    x_torqrun_lease: str = Header(),
) -> ArtifactOut:
    if not NAME.match(name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid artifact name")
    async with session.begin():
        try:
            attempt = await ops.lock_leased_attempt(session, attempt_id, agent.id, x_torqrun_lease)
        except ops.LeaseMismatchError:
            raise _conflict() from None
        used_count, used_bytes = (
            await session.execute(
                select(func.count(), func.coalesce(func.sum(Artifact.size_bytes), 0)).where(
                    Artifact.attempt_id == attempt_id, Artifact.name != name
                )
            )
        ).one()
        run_id, attempt_no = attempt.run_id, attempt.attempt_no
    if used_count >= settings.max_artifacts_per_attempt:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"at most {settings.max_artifacts_per_attempt} artifacts per attempt",
        )
    limit = min(settings.max_artifact_bytes, settings.max_attempt_artifact_bytes - int(used_bytes))

    root = store_root(settings)
    rel = Path(str(run_id)) / str(attempt_no) / name
    tmp_dir = root / ".incoming"
    await asyncio.to_thread(tmp_dir.mkdir, parents=True, exist_ok=True)
    tmp = tmp_dir / f"{uuid.uuid4().hex}.part"
    digest = hashlib.sha256()
    size = 0
    try:
        with tmp.open("wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise HTTPException(
                        status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=f"artifact exceeds the {limit // (1024 * 1024)} MiB limit",
                    )
                digest.update(chunk)
                await asyncio.to_thread(fh.write, chunk)
        async with session.begin():
            try:
                await ops.lock_leased_attempt(session, attempt_id, agent.id, x_torqrun_lease)
            except ops.LeaseMismatchError:
                raise _conflict() from None
            final = root / rel
            await asyncio.to_thread(final.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(os.replace, tmp, final)
            row = (
                await session.execute(
                    insert(Artifact)
                    .values(
                        id=uuid.uuid4(),
                        run_id=run_id,
                        attempt_id=attempt_id,
                        attempt_no=attempt_no,
                        name=name,
                        size_bytes=size,
                        sha256=digest.hexdigest(),
                        path=str(rel),
                        created_at=ops.utcnow(),
                    )
                    .on_conflict_do_update(  # re-upload after a lost response: same file
                        index_elements=["attempt_id", "name"],
                        set_={"size_bytes": size, "sha256": digest.hexdigest()},
                    )
                    .returning(Artifact)
                )
            ).scalar_one()
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
    return _out(row)


def _out(a: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=a.id,
        attempt_no=a.attempt_no,
        name=a.name,
        size_bytes=a.size_bytes,
        sha256=a.sha256,
        created_at=a.created_at,
    )


@router.get("/{run_id}/artifacts")
async def list_artifacts(run_id: uuid.UUID, session: SessionDep) -> list[ArtifactOut]:
    if await session.get(Run, run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="run not found")
    rows = (
        await session.execute(
            select(Artifact)
            .where(Artifact.run_id == run_id)
            .order_by(Artifact.attempt_no.desc(), Artifact.name)
        )
    ).scalars()
    return [_out(a) for a in rows]


@router.get("/{run_id}/artifacts/{artifact_id}/download", response_class=FileResponse)
async def download_artifact(
    run_id: uuid.UUID, artifact_id: uuid.UUID, session: SessionDep, settings: SettingsDep
) -> FileResponse:
    a = await session.get(Artifact, artifact_id)
    if a is None or a.run_id != run_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="artifact not found")
    root = store_root(settings).resolve()
    path = (root / a.path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status.HTTP_410_GONE, detail="artifact file is no longer stored")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=a.name,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


async def purge_expired(
    maker: async_sessionmaker[AsyncSession], settings: Settings, *, now: datetime | None = None
) -> int:
    """Delete artifacts older than the retention period (rows and files)."""
    cutoff = (now or ops.utcnow()) - timedelta(days=settings.artifact_retention_days)
    async with maker() as session, session.begin():
        rows = (
            (
                await session.execute(
                    delete(Artifact).where(Artifact.created_at < cutoff).returning(Artifact.path)
                )
            )
            .scalars()
            .all()
        )
    root = store_root(settings)
    for rel in rows:
        with contextlib.suppress(FileNotFoundError):
            await asyncio.to_thread((root / rel).unlink)
    # Remove now-empty run directories (best effort).
    for rel in {Path(r).parts[0] for r in rows}:
        await asyncio.to_thread(_rmdir_if_empty, root / rel)
    return len(rows)


def _rmdir_if_empty(path: Path) -> None:
    with contextlib.suppress(OSError):
        for sub in path.iterdir():
            sub.rmdir()
        path.rmdir()
