"""M2 reliability invariants against a real PostgreSQL."""

import asyncio
import random
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from torqrun_core.states import LEASED, RunStatus
from torqrun_db import create_engine, migrate
from torqrun_db import runs as ops
from torqrun_db.models import (
    Agent,
    ControlPlaneHeartbeat,
    Job,
    QueueSettings,
    Run,
    RunAttempt,
    RunEvent,
)

S = RunStatus
TTL = timedelta(seconds=60)
Maker = async_sessionmaker[AsyncSession]


@pytest.fixture
async def maker(database_url: str) -> AsyncIterator[Maker]:
    await asyncio.to_thread(migrate.upgrade, database_url)
    engine = create_engine(database_url, pool_size=10)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def agent(maker: Maker, name: str = "a1", queues: tuple[str, ...] = ("default",)) -> Agent:
    async with maker() as s, s.begin():
        a = Agent(
            name=name,
            hostname="h",
            os="linux",
            arch="x86_64",
            agent_version="t",
            max_slots=50,
            queues=list(queues),
            tags=[],
            status="ONLINE",
        )
        s.add(a)
    return a


async def runs(maker: Maker, n: int = 1, name: str = "job", **spec: Any) -> list[Run]:
    async with maker() as s, s.begin():
        job, version = await ops.create_job(
            s, name=name, description="", spec={"runtime": "shell", "script": "true", **spec}
        )
        return [
            await ops.create_run(s, job, version, trigger="api", actor="test") for _ in range(n)
        ]


async def claim(maker: Maker, a: Agent, limit: int = 10) -> list[ops.Claimed]:
    async with maker() as s, s.begin():
        return await ops.claim(s, a, limit=limit, lease_ttl=TTL)


async def advance(maker: Maker, c: ops.Claimed, *targets: RunStatus) -> None:
    for t in targets:
        async with maker() as s, s.begin():
            assert c.attempt.agent_id is not None
            att = await ops.lock_leased_attempt(
                s, c.attempt.id, c.attempt.agent_id, c.attempt.lease_token or ""
            )
            await ops.transition(s, att, t, actor="test")


async def end(maker: Maker, c: ops.Claimed, target: RunStatus) -> Run:
    async with maker() as s, s.begin():
        assert c.attempt.agent_id is not None
        att = await ops.lock_leased_attempt(
            s, c.attempt.id, c.attempt.agent_id, c.attempt.lease_token or ""
        )
        return await ops.finish(s, att, target, actor="test", rng=random.Random(1))


async def get_run(maker: Maker, run_id: Any) -> Run:
    async with maker() as s:
        r = await s.get(Run, run_id)
        assert r is not None
        return r


async def events(maker: Maker, run_id: Any) -> list[tuple[str | None, str]]:
    async with maker() as s:
        rows = (
            await s.execute(select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.id))
        ).scalars()
        return [(e.from_status, e.to_status) for e in rows]


# ---------------------------------------------------------------- retries


async def test_failure_schedules_retry_with_backoff_then_promotes(maker: Maker) -> None:
    [run] = await runs(maker, retry={"max_attempts": 3, "backoff_seconds": 10})
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    r = await end(maker, c, S.FAILED)
    assert r.status == S.RETRY_WAIT
    assert r.next_attempt_at is not None
    delay = (r.next_attempt_at - (await get_run(maker, run.id)).updated_at).total_seconds()
    assert 4 <= delay <= 11  # first retry: base 10s with jitter in [5, 10]

    async with maker() as s, s.begin():
        assert await ops.promote_due_retries(s) == []  # not due yet
        await s.execute(update(Run).where(Run.id == run.id).values(next_attempt_at=ops.utcnow()))
    async with maker() as s, s.begin():
        assert len(await ops.promote_due_retries(s)) == 1
    r = await get_run(maker, run.id)
    assert (r.status, r.current_attempt, r.exit_code, r.next_attempt_at) == (
        S.QUEUED,
        2,
        None,
        None,
    )
    [c2] = await claim(maker, a)
    assert c2.attempt.attempt_no == 2
    assert (S.FAILED, S.RETRY_WAIT) in await events(maker, run.id)


async def test_no_retry_after_last_attempt(maker: Maker) -> None:
    await runs(maker, retry={"max_attempts": 1})
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    assert (await end(maker, c, S.FAILED)).status == S.FAILED


async def test_success_never_retries(maker: Maker) -> None:
    await runs(maker, retry={"max_attempts": 5})
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    assert (await end(maker, c, S.SUCCEEDED)).status == S.SUCCEEDED


# ---------------------------------------------------------------- lease reaper


async def _expire(maker: Maker, attempt_id: Any) -> None:
    async with maker() as s, s.begin():
        await s.execute(
            update(RunAttempt)
            .where(RunAttempt.id == attempt_id)
            .values(lease_expires_at=ops.utcnow() - timedelta(seconds=1))
        )


async def test_expired_lease_marks_run_lost_without_retry_by_default(maker: Maker) -> None:
    [run] = await runs(maker, retry={"max_attempts": 3})
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    await _expire(maker, c.attempt.id)
    async with maker() as s, s.begin():
        assert len(await ops.reap_expired_leases(s)) == 1
    r = await get_run(maker, run.id)
    assert r.status == S.LOST  # interrupt_policy defaults to "fail": partial side effects possible
    assert "lease expired" in (r.error_summary or "")


async def test_expired_lease_retries_when_job_is_safe_to_rerun(maker: Maker) -> None:
    [run] = await runs(maker, retry={"max_attempts": 2}, interrupt_policy="retry")
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    await _expire(maker, c.attempt.id)
    async with maker() as s, s.begin():
        await ops.reap_expired_leases(s)
    assert (await get_run(maker, run.id)).status == S.RETRY_WAIT


async def test_reaper_ignores_live_leases_and_lost_agent_cannot_report_late(maker: Maker) -> None:
    [run] = await runs(maker)
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    async with maker() as s, s.begin():
        assert await ops.reap_expired_leases(s) == []
    await _expire(maker, c.attempt.id)
    async with maker() as s, s.begin():
        await ops.reap_expired_leases(s)
    with pytest.raises(ops.LeaseMismatchError):  # fencing: the old lease is dead
        await end(maker, c, S.SUCCEEDED)
    assert (await get_run(maker, run.id)).status == S.LOST


async def test_cancel_requested_with_expired_lease_becomes_cancelled(maker: Maker) -> None:
    [run] = await runs(maker)
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING, S.CANCEL_REQUESTED)
    await _expire(maker, c.attempt.id)
    async with maker() as s, s.begin():
        await ops.reap_expired_leases(s)
    assert (await get_run(maker, run.id)).status == S.CANCELLED


# ---------------------------------------------------------------- cancellation


async def _cancel(maker: Maker, run_id: Any) -> Run:
    async with maker() as s, s.begin():
        return await ops.cancel_run(s, run_id, actor="test")


async def test_cancel_queued_run_ends_it_immediately(maker: Maker) -> None:
    [run] = await runs(maker)
    assert (await _cancel(maker, run.id)).status == S.CANCELLED
    assert await claim(maker, await agent(maker)) == []


async def test_cancel_dispatched_run_kills_the_lease_so_ack_fails(maker: Maker) -> None:
    [run] = await runs(maker)
    a = await agent(maker)
    [c] = await claim(maker, a)
    assert (await _cancel(maker, run.id)).status == S.CANCELLED
    with pytest.raises(ops.LeaseMismatchError):
        await advance(maker, c, S.STARTING)


async def test_cancel_running_run_requests_cancellation(maker: Maker) -> None:
    [run] = await runs(maker)
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    assert (await _cancel(maker, run.id)).status == S.CANCEL_REQUESTED
    assert (await _cancel(maker, run.id)).status == S.CANCEL_REQUESTED  # idempotent
    assert (await end(maker, c, S.CANCELLED)).status == S.CANCELLED


async def test_cancel_waiting_retry_and_finished_run(maker: Maker) -> None:
    [run] = await runs(maker, retry={"max_attempts": 3})
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    await end(maker, c, S.FAILED)
    assert (await _cancel(maker, run.id)).status == S.CANCELLED
    with pytest.raises(ops.RunFinishedError):
        await _cancel(maker, run.id)


# ---------------------------------------------------------------- manual retry


async def test_manual_retry_of_failed_run_and_refusal_for_success(maker: Maker) -> None:
    [failed, ok] = await runs(maker, 2)
    a = await agent(maker)
    c1, c2 = await claim(maker, a)
    for c, outcome in ((c1, S.FAILED), (c2, S.SUCCEEDED)):
        await advance(maker, c, S.STARTING, S.RUNNING)
        await end(maker, c, outcome)
    async with maker() as s, s.begin():
        r = await ops.retry_run(s, failed.id, actor="test")
    assert (r.status, r.current_attempt, r.max_attempts) == (S.QUEUED, 2, 2)
    with pytest.raises(ops.RunFinishedError):
        async with maker() as s, s.begin():
            await ops.retry_run(s, ok.id, actor="test")


# ---------------------------------------------------------------- concurrency limits


async def test_job_max_concurrent_holds_under_racing_agents(maker: Maker) -> None:
    await runs(maker, 10, max_concurrent=2)
    agents = [await agent(maker, f"a{i}") for i in range(5)]
    claimed = [
        c for batch in await asyncio.gather(*(claim(maker, a) for a in agents)) for c in batch
    ]
    assert len(claimed) == 2


async def test_queue_max_concurrency_and_pause(maker: Maker) -> None:
    await runs(maker, 6)
    async with maker() as s, s.begin():
        s.add(QueueSettings(name="default", max_concurrency=3, paused=False))
    agents = [await agent(maker, f"a{i}") for i in range(4)]
    claimed = [
        c for batch in await asyncio.gather(*(claim(maker, a) for a in agents)) for c in batch
    ]
    assert len(claimed) == 3
    async with maker() as s, s.begin():
        await s.execute(update(QueueSettings).values(max_concurrency=None, paused=True))
    assert await claim(maker, agents[0]) == []
    async with maker() as s, s.begin():
        await s.execute(update(QueueSettings).values(paused=False))
    assert len(await claim(maker, agents[0])) == 3


async def test_limited_job_does_not_block_other_jobs(maker: Maker) -> None:
    await runs(maker, 3, name="limited", max_concurrent=1, priority=10)
    await runs(maker, 2, name="free")
    claimed = await claim(maker, await agent(maker), limit=10)
    names = sorted(c.job.name for c in claimed)
    assert names == ["free", "free", "limited"]


async def test_limits_hold_and_nothing_is_lost_under_a_racing_fleet(maker: Maker) -> None:
    """12 agents claim and finish concurrently for a mix of limited jobs, a capped queue and an
    unlimited queue. At every sample no limit is exceeded; in the end every run ran once."""
    caps = {"lim-a": 2, "lim-b": 1, "lim-c": 3}
    for name, cap in caps.items():
        await runs(maker, 12, name=name, max_concurrent=cap)
    await runs(maker, 30, name="free")
    await runs(maker, 30, name="fast", queue="fast")
    async with maker() as s, s.begin():
        s.add(QueueSettings(name="default", max_concurrency=5, paused=False))
    total = 12 * 3 + 30 + 30
    fleet = [await agent(maker, f"r{i}", ("default", "fast")) for i in range(12)]
    leased = [st.value for st in LEASED]
    violations: list[str] = []
    claimed_ids: list[Any] = []
    done = asyncio.Event()

    async def finish(c: ops.Claimed, holder: Agent) -> None:
        await asyncio.sleep(random.uniform(0, 0.03))
        async with maker() as s, s.begin():
            a = await ops.lock_leased_attempt(
                s, c.attempt.id, holder.id, c.attempt.lease_token or ""
            )
            await ops.transition(s, a, S.STARTING, actor="t")
            await ops.transition(s, a, S.RUNNING, actor="t")
            await ops.finish(s, a, S.SUCCEEDED, actor="t")

    async def work(a: Agent) -> None:
        while not done.is_set():
            batch = await claim(maker, a, limit=3)
            claimed_ids.extend(c.attempt.id for c in batch)
            await asyncio.gather(*(finish(c, a) for c in batch))
            if not batch:
                await asyncio.sleep(0.01)

    async def monitor() -> None:
        async with maker() as s:
            job_names = dict((await s.execute(select(Job.id, Job.name))).all())
        while not done.is_set():
            async with maker() as s:
                per_job = (
                    await s.execute(
                        select(Run.job_id, func.count())
                        .join(RunAttempt, RunAttempt.run_id == Run.id)
                        .where(RunAttempt.status.in_(leased))
                        .group_by(Run.job_id)
                    )
                ).all()
                in_default = (
                    await s.execute(
                        select(func.count())
                        .select_from(RunAttempt)
                        .where(RunAttempt.status.in_(leased), RunAttempt.queue == "default")
                    )
                ).scalar_one()
                finished = (
                    await s.execute(
                        select(func.count()).select_from(Run).where(Run.status == S.SUCCEEDED)
                    )
                ).scalar_one()
            for job_id, n in per_job:
                cap = caps.get(job_names[job_id])
                if cap is not None and n > cap:
                    violations.append(f"{job_names[job_id]}: {n} running > {cap}")
            if in_default > 5:
                violations.append(f"queue default: {in_default} running > 5")
            if finished == total:
                done.set()
            await asyncio.sleep(0.02)

    await asyncio.wait_for(asyncio.gather(monitor(), *(work(a) for a in fleet)), timeout=120)
    assert violations == []
    assert len(claimed_ids) == len(set(claimed_ids)) == total


# ---------------------------------------------------------------- idempotency


async def test_idempotency_key_reserves_once_and_detects_mismatch(maker: Maker) -> None:
    async with maker() as s, s.begin():
        assert await ops.idempotency_begin(s, "trigger:x", "k1", "h1") is None
        await ops.idempotency_store(s, "trigger:x", "k1", 201, {"id": "run-1"})
    async with maker() as s, s.begin():
        stored = await ops.idempotency_begin(s, "trigger:x", "k1", "h1")
        assert stored is not None and stored.response == {"id": "run-1"}
    with pytest.raises(ops.IdempotencyConflictError):
        async with maker() as s, s.begin():
            await ops.idempotency_begin(s, "trigger:x", "k1", "different")


async def test_concurrent_duplicates_see_the_first_response(maker: Maker) -> None:
    async def attempt(n: int) -> dict[str, Any]:
        async with maker() as s, s.begin():
            stored = await ops.idempotency_begin(s, "trigger:y", "dup", "h")
            if stored is not None:
                assert stored.response is not None
                return stored.response
            await asyncio.sleep(0.2)  # hold the reservation while the others arrive
            await ops.idempotency_store(s, "trigger:y", "dup", 201, {"winner": n})
            return {"winner": n}

    results = await asyncio.gather(*(attempt(i) for i in range(5)))
    assert len({r["winner"] for r in results}) == 1


# ---------------------------------------------------------------- reaper vs control-plane outage


async def test_reaper_pauses_while_no_api_has_been_up_for_a_lease_period(
    maker: Maker, database_url: str
) -> None:
    from pydantic import SecretStr

    from torqrun_scheduler.main import Scheduler, SchedulerSettings

    [run] = await runs(maker)
    a = await agent(maker)
    [c] = await claim(maker, a)
    await advance(maker, c, S.STARTING, S.RUNNING)
    await _expire(maker, c.attempt.id)
    scheduler = Scheduler(
        SchedulerSettings(database_url=SecretStr(database_url), lease_ttl_seconds=60), maker
    )

    # 1) No API alive at all: an outage, not a dead agent. Nothing is reaped.
    assert (await scheduler.tick())["reap_expired_leases"] == 0
    # 2) An API just came back: agents haven't had a full lease period to renew yet.
    async with maker() as s, s.begin():
        await ops.control_plane_beat(s, "replica-1", ops.utcnow() - timedelta(seconds=5))
    assert (await scheduler.tick())["reap_expired_leases"] == 0
    assert (await get_run(maker, run.id)).status == S.RUNNING
    # 3) The API has been up longer than the lease TTL and the agent is still silent: reap.
    async with maker() as s, s.begin():
        await ops.control_plane_beat(s, "replica-2", ops.utcnow() - timedelta(seconds=120))
    assert (await scheduler.tick())["reap_expired_leases"] == 1
    assert (await get_run(maker, run.id)).status == S.LOST


async def test_dead_api_replicas_do_not_count_as_up(maker: Maker) -> None:
    async with maker() as s, s.begin():
        await ops.control_plane_beat(s, "old", ops.utcnow() - timedelta(hours=2))
        await s.execute(
            update(ControlPlaneHeartbeat).values(last_alive_at=ops.utcnow() - timedelta(minutes=1))
        )
    async with maker() as s:
        assert await ops.control_plane_up_since(s) is None
