"""Upgrades: a database written by an older release migrates to head with its data intact and
working, and every migration can be reverted."""

import uuid

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, inspect, text
from tests.auth import authenticate
from tests.integration.test_agent_protocol import NOW, TOKEN, enroll

from torqrun_api.main import create_app
from torqrun_api.settings import Settings
from torqrun_db import migrate
from torqrun_db.engine import normalize_url

OLD = "0005_remote_agents"  # M3: before schedules, workflows, users, secrets, notifications
# A spec as M3 stored it: none of the keys added since (secrets, executor, container…).
OLD_SPEC = '{"runtime": "shell", "script": "echo upgraded", "args": [], "env": {}, "timeout_seconds": 60, "queue": "default", "priority": 0, "retry": {"max_attempts": 1, "backoff_seconds": 10, "backoff_factor": 2, "max_backoff_seconds": 600, "retry_on_timeout": false}, "interrupt_policy": "fail", "max_concurrent": null}'


def seed_old_release(url: str) -> dict[str, uuid.UUID]:
    ids = {
        k: uuid.uuid4()
        for k in ("job", "version", "done_run", "done_attempt", "queued_run", "queued_attempt")
    }
    engine = create_engine(normalize_url(url))
    with engine.begin() as c:
        c.execute(
            text(
                "INSERT INTO jobs (id, name, description, current_version) VALUES (:job, 'legacy', 'from M3', 1)"
            ),
            ids,
        )
        c.execute(
            text(
                "INSERT INTO job_versions (id, job_id, version, spec) VALUES (:version, :job, 1, CAST(:spec AS jsonb))"
            ),
            {**ids, "spec": OLD_SPEC},
        )
        for run, attempt, status in (
            ("done_run", "done_attempt", "SUCCEEDED"),
            ("queued_run", "queued_attempt", "QUEUED"),
        ):
            c.execute(
                text(
                    "INSERT INTO runs (id, job_id, job_version_id, job_version, trigger, status, queue, priority,"
                    " spec_snapshot, current_attempt, max_attempts, exit_code)"
                    " VALUES (:run, :job, :version, 1, 'manual', :status, 'default', 0, CAST(:spec AS jsonb), 1, 1,"
                    " CAST(:exit_code AS integer))"
                ),
                {
                    "run": ids[run],
                    "job": ids["job"],
                    "version": ids["version"],
                    "status": status,
                    "spec": OLD_SPEC,
                    "exit_code": 0 if status == "SUCCEEDED" else None,
                },
            )
            c.execute(
                text(
                    "INSERT INTO run_attempts (id, run_id, attempt_no, status, queue, priority, log_bytes)"
                    " VALUES (:attempt, :run, 1, :status, 'default', 0, 0)"
                ),
                {"attempt": ids[attempt], "run": ids[run], "status": status},
            )
            c.execute(
                text(
                    "INSERT INTO run_events (run_id, to_status, reason, actor) VALUES (:run, 'QUEUED', 'created', 'user:dev')"
                ),
                {"run": ids[run]},
            )
    engine.dispose()
    return ids


def test_old_database_upgrades_and_keeps_working(database_url: str) -> None:
    migrate.upgrade(database_url, OLD)
    ids = seed_old_release(database_url)
    migrate.upgrade(database_url)  # to head

    settings = Settings(
        database_url=SecretStr(database_url),
        environment="development",
        dev_enrollment_token=SecretStr(TOKEN),
        claim_poll_interval_seconds=0.05,
    )
    with TestClient(create_app(settings)) as c:
        authenticate(c)  # users didn't exist in M3: first-run setup works on upgraded data
        done = c.get(f"/api/v1/runs/{ids['done_run']}").json()
        assert done["status"] == "SUCCEEDED" and done["spec"]["executor"] == "process"
        job = c.get(f"/api/v1/jobs/{ids['job']}").json()
        assert job["spec"]["secrets"] == {} and job["spec"]["container"] is None

        # The run queued before the upgrade is still dispatched and completes normally.
        auth = enroll(c)
        r = c.post("/api/v1/agent/claim", json={"free_slots": 1, "wait_seconds": 1}, headers=auth)
        [a] = r.json()["assignments"]
        assert a["run_id"] == str(ids["queued_run"]) and a["spec"]["script"] == "echo upgraded"
        base = f"/api/v1/agent/attempts/{a['attempt_id']}"
        lease = {"lease_token": a["lease_token"]}
        assert c.post(f"{base}/ack", json=lease, headers=auth).status_code == 204
        c.post(f"{base}/started", json={**lease, "pid": 1, "started_at": NOW}, headers=auth)
        done_body = {**lease, "outcome": "succeeded", "exit_code": 0, "finished_at": NOW}
        assert c.post(f"{base}/complete", json=done_body, headers=auth).status_code == 204
        assert c.get(f"/api/v1/runs/{ids['queued_run']}").json()["status"] == "SUCCEEDED"

        # Editing the legacy job creates v2 with today's spec format.
        upd = c.put(
            f"/api/v1/jobs/{ids['job']}", json={"description": "edited", "spec": job["spec"]}
        )
        assert upd.status_code == 200 and upd.json()["current_version"] == 2


def test_every_migration_can_be_reverted(database_url: str) -> None:
    migrate.upgrade(database_url)
    migrate.downgrade(database_url, "base")
    engine = create_engine(normalize_url(database_url))
    with engine.connect() as c:
        leftover = set(inspect(c).get_table_names()) - {"alembic_version"}
    engine.dispose()
    assert leftover == set()
    migrate.upgrade(database_url)  # and forward again from nothing
