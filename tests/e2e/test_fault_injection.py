"""M2 acceptance: real processes, real failures.

Each test breaks something on purpose (kills the agent, restarts the API, cancels a running
job, sends duplicate requests) and checks the documented recovery behaviour.
"""

import os
import signal
import threading
import time
from pathlib import Path

from tests.e2e.conftest import Stack, wait_for


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return (
        Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
        if Path(f"/proc/{pid}").exists()
        else True
    )


def test_failed_attempts_retry_with_backoff_until_success(stack: Stack) -> None:
    job = stack.create_job(
        "flaky",
        runtime="shell",
        # Fails on attempts 1 and 2, succeeds on 3.
        script='echo "attempt $TORQRUN_ATTEMPT"\n[ "$TORQRUN_ATTEMPT" -ge 3 ] || { echo "transient error" >&2; exit 1; }',
        retry={"max_attempts": 3, "backoff_seconds": 1, "backoff_factor": 1},
    )
    run = stack.wait_finished(stack.trigger(job)["id"], timeout=40)
    assert run["status"] == "SUCCEEDED"
    assert run["current_attempt"] == 3
    assert [a["status"] for a in run["attempts"]] == ["FAILED", "FAILED", "SUCCEEDED"]
    assert [e["to_status"] for e in run["events"]].count("RETRY_WAIT") == 2
    assert stack.logs(run["id"], attempt=1)[0]["data"] == "attempt 1\n"
    assert [c["data"] for c in stack.logs(run["id"], attempt=3)] == ["attempt 3\n"]


def test_retries_exhausted_ends_failed(stack: Stack) -> None:
    job = stack.create_job(
        "always-fails",
        runtime="shell",
        script="exit 4",
        retry={"max_attempts": 2, "backoff_seconds": 1},
    )
    run = stack.wait_finished(stack.trigger(job)["id"], timeout=30)
    assert (run["status"], run["exit_code"], run["current_attempt"]) == ("FAILED", 4, 2)


def test_cancel_running_job_kills_its_process_tree(stack: Stack, tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    job = stack.create_job(
        "long",
        runtime="shell",
        script=f"sleep 300 &\necho $! > {child_pid_file}\necho working\nsleep 300",
    )
    run = stack.trigger(job)
    stack.wait_status(run["id"], {"RUNNING"})
    wait_for(child_pid_file.exists, 10, "child pid file")
    pid = stack.run(run["id"])["attempts"][0]["pid"]
    child = int(child_pid_file.read_text())
    started = time.monotonic()
    assert (
        stack.http.post(f"/api/v1/runs/{run['id']}/cancel").json()["status"] == "CANCEL_REQUESTED"
    )
    final = stack.wait_finished(run["id"], timeout=20)
    assert final["status"] == "CANCELLED"
    assert time.monotonic() - started < 8  # heartbeat 1s + SIGTERM grace
    wait_for(lambda: not pid_alive(pid) and not pid_alive(child), 5, "process tree gone")


def test_cancel_queued_run_never_starts(stack: Stack) -> None:
    stack.stop("agent")
    job = stack.create_job("never", runtime="shell", script="echo should-not-run")
    run = stack.trigger(job)
    assert stack.http.post(f"/api/v1/runs/{run['id']}/cancel").json()["status"] == "CANCELLED"
    stack.start_agent()
    time.sleep(2)
    assert stack.run(run["id"])["status"] == "CANCELLED"
    assert stack.logs(run["id"]) == []


def test_agent_crash_mid_run_is_recovered_on_restart_and_retried(
    stack: Stack, tmp_path: Path
) -> None:
    marker = tmp_path / "attempts.txt"
    job = stack.create_job(
        "crash-safe",
        runtime="shell",
        interrupt_policy="retry",
        retry={"max_attempts": 2, "backoff_seconds": 1},
        script=f'echo "$TORQRUN_ATTEMPT" >> {marker}\nif [ "$TORQRUN_ATTEMPT" = 1 ]; then sleep 300; fi\necho done',
    )
    run = stack.trigger(job)
    stack.wait_status(run["id"], {"RUNNING"})
    pid = stack.run(run["id"])["attempts"][0]["pid"]
    stack.stop("agent", signal.SIGKILL)  # crash: no cleanup, no report
    assert pid_alive(pid)  # the job outlives the crashed agent...
    stack.start_agent()  # ...until the restarted agent finds and kills it
    final = stack.wait_finished(run["id"], timeout=30)
    assert final["status"] == "SUCCEEDED"
    assert [a["status"] for a in final["attempts"]] == ["LOST", "SUCCEEDED"]
    assert "agent restarted" in final["attempts"][0]["error_summary"]
    assert not pid_alive(pid)
    assert marker.read_text().split() == ["1", "2"]


def test_agent_gone_for_good_marks_run_lost_and_does_not_retry_by_default(stack: Stack) -> None:
    job = stack.create_job(
        "not-idempotent", runtime="shell", script="sleep 300", retry={"max_attempts": 3}
    )
    run = stack.trigger(job)
    stack.wait_status(run["id"], {"RUNNING"})
    pid = stack.run(run["id"])["attempts"][0]["pid"]
    stack.stop("agent", signal.SIGKILL)
    try:
        final = stack.wait_finished(run["id"], timeout=30)  # lease TTL 10s + reaper
        assert final["status"] == "LOST"
        assert (
            final["current_attempt"] == 1
        )  # interrupt_policy "fail": partial side effects possible
        assert "lease expired" in final["error_summary"]
    finally:
        os.killpg(pid, signal.SIGKILL)  # the orphan no agent will ever clean up


def test_api_restart_mid_run_does_not_lose_the_result(stack: Stack) -> None:
    job = stack.create_job(
        "survivor", runtime="shell", script="for i in 1 2 3 4 5 6; do echo tick $i; sleep 1; done"
    )
    run = stack.trigger(job)
    stack.wait_status(run["id"], {"RUNNING"})
    stack.stop("api")
    time.sleep(3)  # job keeps running; log uploads fail and are retried
    stack.start_api()
    final = stack.wait_finished(run["id"], timeout=40)
    print(
        "EVENTS",
        [
            (e["at"][11:23], e["from_status"], e["to_status"], e["actor"], e["reason"])
            for e in final["events"]
        ],
    )
    assert final["status"] == "SUCCEEDED"
    assert [c["data"] for c in stack.logs(run["id"])] == [f"tick {i}\n" for i in range(1, 7)]


def test_result_survives_api_outage_longer_than_the_lease(stack: Stack) -> None:
    """The control plane is down for longer than the lease TTL (10 s) and the job finishes
    meanwhile. The run must not be declared lost: the scheduler pauses reaping while no API is
    up, and the agent delivers the spooled result once the API is back."""
    job = stack.create_job("quick", runtime="shell", script="sleep 2; echo finished")
    run = stack.trigger(job)
    stack.wait_status(run["id"], {"RUNNING"})
    stack.stop("api")
    time.sleep(15)
    stack.start_api()
    final = stack.wait_finished(run["id"], timeout=30)
    assert final["status"] == "SUCCEEDED"
    assert [c["data"] for c in stack.logs(run["id"])] == ["finished\n"]


def test_duplicate_trigger_requests_create_one_run(stack: Stack) -> None:
    job = stack.create_job("deploy", runtime="shell", script="echo deploying")
    ids: list[str] = []

    def fire() -> None:
        ids.append(stack.trigger(job, **{"Idempotency-Key": "release-2026.10.07"})["id"])

    threads = [threading.Thread(target=fire) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(ids)) == 1
    assert stack.http.get("/api/v1/runs", params={"job_id": job}).json()["total"] == 1


def test_job_concurrency_limit_is_respected(stack: Stack, tmp_path: Path) -> None:
    log = tmp_path / "overlap.log"
    job = stack.create_job(
        "serial",
        runtime="shell",
        max_concurrent=1,
        script=f'echo "start $(date +%s.%N)" >> {log}; sleep 1; echo "end $(date +%s.%N)" >> {log}',
    )
    runs = [stack.trigger(job)["id"] for _ in range(3)]
    for r in runs:
        assert stack.wait_finished(r, timeout=40)["status"] == "SUCCEEDED"
    events = [line.split() for line in log.read_text().splitlines()]
    depth = peak = 0
    for kind, _ in sorted(events, key=lambda e: float(e[1])):
        depth += 1 if kind == "start" else -1
        peak = max(peak, depth)
    assert peak == 1
