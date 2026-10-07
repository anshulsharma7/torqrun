"""Agent request authentication (``Authorization: Bearer tqa_…``)."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from torqrun_api.deps import SessionDep
from torqrun_db import fleet
from torqrun_db.models import Agent

CREDENTIAL_PREFIX = fleet.CREDENTIAL_PREFIX


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


async def current_agent(request: Request, session: SessionDep) -> Agent:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith(CREDENTIAL_PREFIX):
        raise _unauthorized("agent credential required")
    found = await fleet.authenticate(session, token)
    # End the read-only transaction so handlers can open their own with session.begin();
    # the session is configured with expire_on_commit=False, so the agent stays usable.
    await session.commit()
    if found is None:
        raise _unauthorized("invalid agent credential")
    agent, credential_hash = found
    if agent.status == "REVOKED":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="agent revoked")
    request.state.credential_hash = credential_hash
    return agent


AgentDep = Annotated[Agent, Depends(current_agent)]
