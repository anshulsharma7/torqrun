"""Agent inventory for the dashboard."""

import uuid
from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_api.deps import SessionDep, SettingsDep
from torqrun_api.schemas import AgentActiveRun, AgentOut
from torqrun_api.settings import Settings
from torqrun_core.states import LEASED
from torqrun_db.models import Agent, Job, Run, RunAttempt
from torqrun_db.runs import utcnow
from torqrun_protocol.agent import SystemStats

router = APIRouter(prefix="/api/v1/agents", tags=["agents"])


async def _active_runs(
    session: AsyncSession, agent_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[AgentActiveRun]]:
    rows = (
        await session.execute(
            select(
                RunAttempt.agent_id,
                Run.id,
                Run.job_id,
                Job.name,
                RunAttempt.status,
                RunAttempt.started_at,
            )
            .join(Run, Run.id == RunAttempt.run_id)
            .join(Job, Job.id == Run.job_id)
            .where(
                RunAttempt.agent_id.in_(agent_ids), RunAttempt.status.in_([s.value for s in LEASED])
            )
            .order_by(RunAttempt.dispatched_at)
        )
    ).all()
    out: dict[uuid.UUID, list[AgentActiveRun]] = defaultdict(list)
    for agent_id, run_id, job_id, job_name, st, started_at in rows:
        if agent_id is None:  # excluded by the WHERE clause; narrows the type
            continue
        out[agent_id].append(
            AgentActiveRun(
                run_id=run_id, job_id=job_id, job_name=job_name, status=st, started_at=started_at
            )
        )
    return out


def _agent_out(agent: Agent, active: list[AgentActiveRun], settings: Settings) -> AgentOut:
    cutoff = utcnow() - timedelta(seconds=settings.agent_offline_after_seconds)
    return AgentOut(
        id=agent.id,
        name=agent.name,
        hostname=agent.hostname,
        os=agent.os,
        arch=agent.arch,
        agent_version=agent.agent_version,
        status=agent.status,
        connected=agent.last_seen_at is not None and agent.last_seen_at >= cutoff,
        max_slots=agent.max_slots,
        running=len(active),
        queues=agent.queues,
        tags=agent.tags,
        capabilities=list(agent.capabilities or ["process"]),
        last_seen_at=agent.last_seen_at,
        created_at=agent.created_at,
        system=SystemStats.model_validate(agent.system_stats) if agent.system_stats else None,
        active_runs=active,
    )


@router.get("")
async def list_agents(session: SessionDep, settings: SettingsDep) -> list[AgentOut]:
    agents = (await session.execute(select(Agent).order_by(Agent.name))).scalars().all()
    active = await _active_runs(session, [a.id for a in agents])
    return [_agent_out(a, active.get(a.id, []), settings) for a in agents]


@router.get("/{agent_id}")
async def get_agent(agent_id: uuid.UUID, session: SessionDep, settings: SettingsDep) -> AgentOut:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="agent not found")
    active = await _active_runs(session, [agent.id])
    return _agent_out(agent, active.get(agent.id, []), settings)
