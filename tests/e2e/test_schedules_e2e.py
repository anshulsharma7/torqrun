"""M4 acceptance: the real scheduler process fires schedules and the agent runs them."""

import time

from tests.e2e.conftest import Stack, wait_for


def test_interval_schedule_fires_repeatedly_and_pause_stops_it(stack: Stack) -> None:
    job = stack.create_job("tick", runtime="shell", script='echo "scheduled at $TORQRUN_RUN_ID"')
    sched = stack.http.post(
        "/api/v1/schedules",
        json={
            "name": "every-10s",
            "job_id": job,
            "kind": "interval",
            "interval_seconds": 10,
        },
    ).json()

    def scheduled_runs() -> list[dict[str, object]]:
        items = stack.http.get("/api/v1/runs", params={"job_id": job, "limit": 50}).json()["items"]
        return [r for r in items if r["trigger"] == "schedule"]

    def at_least_two() -> list[dict[str, object]] | None:
        found = scheduled_runs()
        return found if len(found) >= 2 else None

    runs = wait_for(at_least_two, 40, "two scheduled runs")
    assert all(r["schedule_id"] == sched["id"] for r in runs)
    slots = sorted(r["scheduled_for"] for r in runs)
    assert len(set(slots)) == len(slots)  # one run per slot
    for r in runs:
        assert stack.wait_finished(str(r["id"]))["status"] == "SUCCEEDED"

    stack.http.post(f"/api/v1/schedules/{sched['id']}/pause")
    count = len(scheduled_runs())
    time.sleep(12)
    assert len(scheduled_runs()) == count  # paused: nothing new
