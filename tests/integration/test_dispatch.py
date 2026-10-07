"""Dispatch and lifecycle invariants against a real PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from torqrun_core.states import InvalidTransitionError, RunStatus
from torqrun_db import create_engine, migrate
from torqrun_db import runs as ops
from torqrun_db.models import Agent, Run, RunAttempt, RunEvent

S = RunStatus
TTL = timedelta(seconds=60)


@pytest.fixture
async def maker(database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    await asyncio.to_thread(migrate.upgrade, database_url)
    engine = create_engine(database_url, pool_size=10)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _agent(maker: async_sessionmaker[AsyncSession], name: str, queues: list[str]) -> Agent:
    async with maker() as s, s.begin():
        agent = Agent(
            name=name,
            hostname="h",
            os="linux",
            arch="x86_64",
            agent_version="t",
            max_slots=50,
            queues=queues,
            tags=[],
            status="ONLINE",
        )
        s.add(agent)
    return agent


async def _runs(maker: async_sessionmaker[AsyncSession], n: int, queue: str = "default") -> None:
    async with maker() as s, s.begin():
        job, version = await ops.create_job(
            s,
            name=f"job-{queue}",
            description="",
            spec={"runtime": "shell", "script": "true", "queue": queue},
        )
        for _ in range(n):
            await ops.create_run(s, job, version, trigger="api", actor="test")


async def _claim(
    maker: async_sessionmaker[AsyncSession], agent: Agent, limit: int
) -> list[ops.Claimed]:
    async with maker() as s, s.begin():
        return await ops.claim(s, agent, limit=limit, lease_ttl=TTL)


async def test_concurrent_claims_never_assign_an_attempt_twice(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await _runs(maker, 40)
    agents = [await _agent(maker, f"a{i}", ["default"]) for i in range(6)]
    results = await asyncio.gather(*(_claim(maker, a, 10) for a in agents))
    claimed = [c.attempt.id for r in results for c in r]
    assert len(claimed) == len(set(claimed)) == 40
    async with maker() as s:
        statuses = (await s.execute(select(RunAttempt.status))).scalars().all()
    assert set(statuses) == {S.DISPATCHED}


async def test_claim_respects_queues_and_priority(maker: async_sessionmaker[AsyncSession]) -> None:
    await _runs(maker, 2, queue="gpu")
    await _runs(maker, 2, queue="default")
    async with maker() as s, s.begin():
        urgent = (
            await s.execute(select(RunAttempt).where(RunAttempt.queue == "default").limit(1))
        ).scalar_one()
        urgent.priority = 50
    agent = await _agent(maker, "cpu-only", ["default"])
    claimed = await _claim(maker, agent, 10)
    assert {c.run.queue for c in claimed} == {"default"}
    assert claimed[0].attempt.id == urgent.id


async def test_lifecycle_records_every_transition(maker: async_sessionmaker[AsyncSession]) -> None:
    await _runs(maker, 1)
    agent = await _agent(maker, "a", ["default"])
    [c] = await _claim(maker, agent, 1)
    token = c.attempt.lease_token or ""
    for target in (S.STARTING, S.RUNNING, S.SUCCEEDED):
        async with maker() as s, s.begin():
            attempt = await ops.lock_leased_attempt(s, c.attempt.id, agent.id, token)
            await ops.transition(s, attempt, target, actor="test")
    async with maker() as s:
        run = (await s.execute(select(Run))).scalar_one()
        events = (await s.execute(select(RunEvent).order_by(RunEvent.id))).scalars().all()
        final = await s.get(RunAttempt, c.attempt.id)
    assert run.status == S.SUCCEEDED
    assert run.finished_at is not None
    assert [(e.from_status, e.to_status) for e in events] == [
        (None, "QUEUED"),
        ("QUEUED", "DISPATCHED"),
        ("DISPATCHED", "STARTING"),
        ("STARTING", "RUNNING"),
        ("RUNNING", "SUCCEEDED"),
    ]
    assert final is not None
    assert final.lease_token is None  # lease released on finish


async def test_invalid_transition_is_rejected_and_rolled_back(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    await _runs(maker, 1)
    agent = await _agent(maker, "a", ["default"])
    [c] = await _claim(maker, agent, 1)
    with pytest.raises(InvalidTransitionError):
        async with maker() as s, s.begin():
            attempt = await ops.lock_leased_attempt(
                s, c.attempt.id, agent.id, c.attempt.lease_token or ""
            )
            await ops.transition(s, attempt, S.SUCCEEDED, actor="test")  # skips ack + start
    async with maker() as s:
        assert (await s.get(RunAttempt, c.attempt.id)).status == S.DISPATCHED  # type: ignore[union-attr]


async def test_stale_or_foreign_lease_is_refused(maker: async_sessionmaker[AsyncSession]) -> None:
    await _runs(maker, 1)
    owner = await _agent(maker, "owner", ["default"])
    other = await _agent(maker, "other", ["default"])
    [c] = await _claim(maker, owner, 1)
    async with maker() as s, s.begin():
        with pytest.raises(ops.LeaseMismatchError):
            await ops.lock_leased_attempt(s, c.attempt.id, owner.id, "wrong-token")
        with pytest.raises(ops.LeaseMismatchError):
            await ops.lock_leased_attempt(s, c.attempt.id, other.id, c.attempt.lease_token or "")


async def test_log_append_is_idempotent(maker: async_sessionmaker[AsyncSession]) -> None:
    await _runs(maker, 1)
    agent = await _agent(maker, "a", ["default"])
    [c] = await _claim(maker, agent, 1)
    chunks = [
        {"seq": i, "stream": "stdout", "ts": ops.utcnow(), "data": f"line {i}\n"} for i in range(3)
    ]
    for _ in range(2):  # the same batch delivered twice (lost response, agent retried)
        async with maker() as s, s.begin():
            attempt = await ops.lock_leased_attempt(
                s, c.attempt.id, agent.id, c.attempt.lease_token or ""
            )
            await ops.append_logs(s, attempt, chunks)
    async with maker() as s:
        stored = await s.get(RunAttempt, c.attempt.id)
    assert stored is not None
    assert stored.last_log_seq == 2
    assert stored.log_bytes == sum(len(f"line {i}\n") for i in range(3))


async def test_unacknowledged_dispatch_is_requeued(maker: async_sessionmaker[AsyncSession]) -> None:
    await _runs(maker, 2)
    agent = await _agent(maker, "a", ["default"])
    first, second = await _claim(maker, agent, 2)
    async with maker() as s, s.begin():  # second one was acked: it may already be running
        attempt = await ops.lock_leased_attempt(
            s, second.attempt.id, agent.id, second.attempt.lease_token or ""
        )
        await ops.transition(s, attempt, S.STARTING, actor="test")
    async with maker() as s, s.begin():
        assert await ops.requeue_unacked(s, older_than=timedelta(seconds=60)) == []  # too recent
        requeued = await ops.requeue_unacked(s, older_than=timedelta(seconds=0))
    assert [a.id for a in requeued] == [first.attempt.id]
    async with maker() as s:
        a1 = await s.get(RunAttempt, first.attempt.id)
        a2 = await s.get(RunAttempt, second.attempt.id)
        run = await s.get(Run, first.run.id)
    assert a1 is not None and a2 is not None and run is not None
    assert (a1.status, a1.agent_id, a1.lease_token) == (S.QUEUED, None, None)
    assert run.status == S.QUEUED
    assert a2.status == S.STARTING  # acked work is never silently requeued
    # The old lease is dead; the requeued attempt can be claimed again.
    [again] = await _claim(maker, agent, 1)
    assert again.attempt.id == first.attempt.id
