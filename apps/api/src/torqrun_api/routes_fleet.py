"""Fleet administration: enrollment tokens and agent drain / resume / revoke."""

import uuid
from datetime import datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import select

from torqrun_api.deps import SessionDep
from torqrun_api.public_url import public_url
from torqrun_api.security import ActorDep
from torqrun_db import fleet
from torqrun_db.models import EnrollmentToken

router = APIRouter(prefix="/api/v1", tags=["fleet"])

Label = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,62}$")]


class TokenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=200)
    ttl_minutes: int = Field(default=60, ge=1, le=60 * 24 * 30, description="Valid for this long")
    max_uses: int | None = Field(
        default=1, ge=1, le=1000, description="None = unlimited (until expiry)"
    )
    queues: list[Label] = Field(
        default_factory=list, max_length=32, description="Preset queues; empty = agent decides"
    )
    tags: list[Label] = Field(default_factory=list, max_length=64)


class TokenOut(BaseModel):
    id: uuid.UUID
    prefix: str
    description: str
    state: Literal["active", "expired", "used", "revoked"]
    uses: int
    max_uses: int | None
    queues: list[str]
    tags: list[str]
    expires_at: datetime | None
    created_at: datetime
    created_by: str


class TokenCreated(TokenOut):
    token: str = Field(description="Shown once. Store it now; only a hash is kept.")
    install_command: str = Field(description="One-line installer for Linux servers (run as root)")


def _out(row: EnrollmentToken) -> TokenOut:
    return TokenOut(
        id=row.id,
        prefix=row.prefix,
        description=row.description,
        state=fleet.token_state(row),
        uses=row.uses,
        max_uses=row.max_uses,
        queues=row.queues,
        tags=row.tags,
        expires_at=row.expires_at,
        created_at=row.created_at,
        created_by=row.created_by,
    )


@router.post("/enrollment-tokens", status_code=status.HTTP_201_CREATED)
async def create_token(
    actor: ActorDep, body: TokenCreate, request: Request, session: SessionDep
) -> TokenCreated:
    async with session.begin():
        new = await fleet.create_enrollment_token(
            session,
            description=body.description,
            ttl=timedelta(minutes=body.ttl_minutes),
            max_uses=body.max_uses,
            queues=body.queues,
            tags=body.tags,
            created_by=actor,
        )
    base = public_url(request)
    return TokenCreated(
        **_out(new.row).model_dump(),
        token=new.token,
        install_command=f"curl -fsSL {base}/agent/install.sh | sudo bash -s -- --token {new.token}",
    )


@router.get("/enrollment-tokens")
async def list_tokens(session: SessionDep, include_inactive: bool = False) -> list[TokenOut]:
    rows = (
        await session.execute(
            select(EnrollmentToken).order_by(EnrollmentToken.created_at.desc()).limit(200)
        )
    ).scalars()
    out = [_out(r) for r in rows]
    return out if include_inactive else [t for t in out if t.state == "active"]


@router.delete("/enrollment-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(token_id: uuid.UUID, session: SessionDep) -> Response:
    async with session.begin():
        if await fleet.revoke_enrollment_token(session, token_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="token not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _agent_action(
    session: SessionDep, agent_id: uuid.UUID, action: str, actor: str
) -> dict[str, str]:
    async with session.begin():
        try:
            if action == "revoke":
                agent = await fleet.revoke_agent(session, agent_id, actor=actor)
            else:
                agent = await fleet.set_draining(session, agent_id, action == "drain")
        except LookupError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="agent not found") from None
        except ValueError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
    return {"id": str(agent.id), "status": agent.status}


@router.post("/agents/{agent_id}/drain")
async def drain_agent(
    actor: ActorDep, agent_id: uuid.UUID, request: Request, session: SessionDep
) -> dict[str, str]:
    """Stop sending new work to the agent; jobs already running finish normally."""
    return await _agent_action(session, agent_id, "drain", actor)


@router.post("/agents/{agent_id}/resume")
async def resume_agent(
    actor: ActorDep, agent_id: uuid.UUID, request: Request, session: SessionDep
) -> dict[str, str]:
    return await _agent_action(session, agent_id, "resume", actor)


@router.post("/agents/{agent_id}/revoke")
async def revoke_agent(
    actor: ActorDep, agent_id: uuid.UUID, request: Request, session: SessionDep
) -> dict[str, str]:
    """Permanently disable the agent's credentials. Its running jobs are marked lost (retried if
    the job allows); it must re-enroll with a new token to come back."""
    return await _agent_action(session, agent_id, "revoke", actor)
