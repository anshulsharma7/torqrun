"""M4: schedule API and firing semantics against a real PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.auth import authenticate

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import create_engine, migrate
from torqrun_db import schedules as sched_ops
from torqrun_db.models import Run, Schedule
from torqrun_db.runs import utcnow

Maker = async_sessionmaker[AsyncSession]


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    migrate.upgrade(database_url)
    with TestClient(
        create_app(Settings(database_url=SecretStr(database_url), environment="test"))
    ) as c:
        authenticate(c)
        yield c


@pytest.fixture
async def maker(database_url: str) -> AsyncIterator[Maker]:
    engine = create_engine(database_url, pool_size=5)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def make_schedule(c: TestClient, **over: Any) -> dict[str, Any]:
    job = c.post(
        "/api/v1/jobs",
        json={
            "name": f"job-{len(over)}-{over.get('name', 'x')}",
            "spec": {"runtime": "shell", "script": "true"},
        },
    ).json()
    body = {
        "name": "every-minute",
        "job_id": job["id"],
        "kind": "cron",
        "cron": "* * * * *",
        **over,
    }
    r = c.post("/api/v1/schedules", json=body)
    assert r.status_code == 201, r.text
    return dict(r.json())


async def set_next(maker: Maker, schedule_id: str, ago: timedelta) -> None:
    async with maker() as s, s.begin():
        slot = (utcnow() - ago).replace(second=0, microsecond=0)
        await s.execute(
            update(Schedule).where(Schedule.id == schedule_id).values(next_fire_at=slot)
        )


async def fire(maker: Maker) -> list[sched_ops.Fired]:
    async with maker() as s, s.begin():
        return await sched_ops.fire_due(s)


async def runs_of(maker: Maker, schedule_id: str) -> list[Run]:
    async with maker() as s:
        return list(
            (
                await s.execute(
                    select(Run).where(Run.schedule_id == schedule_id).order_by(Run.scheduled_for)
                )
            ).scalars()
        )


def test_create_preview_and_validation(client: TestClient) -> None:
    s = make_schedule(client, cron="0 9 * * mon-fri", timezone="Asia/Kolkata")
    assert s["next_fire_at"] is not None
    assert len(s["upcoming"]) == 3
    preview = client.post(
        "/api/v1/schedules/preview",
        json={"cron": "30 2 * * *", "timezone": "Europe/Berlin", "count": 3},
    )
    assert len(preview.json()["next"]) == 3
    bad = client.post("/api/v1/schedules/preview", json={"cron": "61 * * * *"})
    assert bad.status_code == 422
    assert "minute value 61 out of range" in bad.text
    assert (
        client.post(
            "/api/v1/schedules/preview", json={"cron": "* * * * *", "timezone": "Nope/Nowhere"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/schedules/preview", json={"kind": "interval", "interval_seconds": 5}
        ).status_code
        == 422
    )


async def test_due_slot_fires_exactly_once(client: TestClient, maker: Maker) -> None:
    s = make_schedule(client)
    await set_next(maker, s["id"], timedelta(seconds=5))
    fired = await fire(maker)
    assert sum(len(f.runs) for f in fired) == 1
    [run] = await runs_of(maker, s["id"])
    assert (run.trigger, run.status) == ("schedule", "QUEUED")
    assert await fire(maker) == []  # next slot is in the future
    detail = client.get(f"/api/v1/schedules/{s['id']}").json()
    assert detail["last_run_status"] == "QUEUED"
    assert client.get(f"/api/v1/runs/{run.id}").json()["scheduled_for"] is not None


async def test_racing_replicas_and_replays_never_duplicate_a_slot(
    client: TestClient, maker: Maker
) -> None:
    s = make_schedule(client)
    await set_next(maker, s["id"], timedelta(seconds=5))
    results = await asyncio.gather(*(fire(maker) for _ in range(5)))  # five "scheduler replicas"
    assert sum(len(f.runs) for r in results for f in r) == 1
    # Replay: something rewinds next_fire_at to the same slot (e.g. restored backup).
    await set_next(maker, s["id"], timedelta(seconds=5))
    await fire(maker)
    assert len(await runs_of(maker, s["id"])) == 1


@pytest.mark.parametrize(
    ("policy", "expected_runs"), [("skip", 0), ("run_once", 1), ("run_all", 3)]
)
async def test_missed_slots_after_downtime(
    client: TestClient, maker: Maker, policy: str, expected_runs: int
) -> None:
    s = make_schedule(client, misfire_policy=policy, max_catchup=3)
    await set_next(maker, s["id"], timedelta(minutes=10, seconds=30))  # down for ~10 minutes
    await fire(maker)
    runs = await runs_of(maker, s["id"])
    if policy == "skip":
        # Only an on-time slot may run; whether "now" is within grace depends on the second.
        assert len(runs) in (0, 1)
    else:
        assert len(runs) == expected_runs
    detail = client.get(f"/api/v1/schedules/{s['id']}").json()
    assert detail["skipped_count"] >= 7
    assert "misfire policy" in detail["last_skip_reason"]


async def test_overlap_skip_holds_while_previous_run_is_active(
    client: TestClient, maker: Maker
) -> None:
    s = make_schedule(client, overlap_policy="skip")
    await set_next(maker, s["id"], timedelta(seconds=5))
    await fire(maker)
    await set_next(maker, s["id"], timedelta(seconds=1))  # next slot due; first run still queued
    async with maker() as sess, sess.begin():
        await sess.execute(
            update(Run)
            .where(Run.schedule_id == s["id"])
            .values(scheduled_for=utcnow() - timedelta(minutes=5))
        )
    await fire(maker)
    assert len(await runs_of(maker, s["id"])) == 1
    assert "overlap policy" in client.get(f"/api/v1/schedules/{s['id']}").json()["last_skip_reason"]


async def test_pause_and_resume_from_now(client: TestClient, maker: Maker) -> None:
    s = make_schedule(client)
    assert client.post(f"/api/v1/schedules/{s['id']}/pause").json()["enabled"] is False
    await set_next(maker, s["id"], timedelta(minutes=30))
    assert await fire(maker) == []
    resumed = client.post(f"/api/v1/schedules/{s['id']}/resume").json()
    assert resumed["enabled"] is True
    assert resumed["next_fire_at"] > utcnow().isoformat()  # no burst of the 30 paused minutes
    assert await fire(maker) == []


async def test_interval_schedule_and_edit_and_delete(client: TestClient, maker: Maker) -> None:
    s = make_schedule(client, kind="interval", cron=None, interval_seconds=600)
    assert s["cron"] is None
    body = {
        k: s[k]
        for k in (
            "name",
            "job_id",
            "misfire_policy",
            "misfire_grace_seconds",
            "max_catchup",
            "overlap_policy",
            "enabled",
            "timezone",
        )
    }
    edited = client.put(
        f"/api/v1/schedules/{s['id']}", json={**body, "kind": "cron", "cron": "@hourly"}
    ).json()
    assert (edited["kind"], edited["cron"], edited["interval_seconds"]) == ("cron", "@hourly", None)
    assert client.delete(f"/api/v1/schedules/{s['id']}").status_code == 204
    async with maker() as sess:
        assert (await sess.execute(select(func.count()).select_from(Schedule))).scalar_one() == 0
