"""M1 acceptance: real API server + real agent process + real scripts, end to end.

Starts ``torqrun-api`` and ``torqrun-agent`` as subprocesses against a fresh database,
then drives everything through the public HTTP API, the same way the UI does.
"""

import json
import time
from typing import Any

from tests.e2e.conftest import Stack


def run_job(stack: Stack, name: str, spec: dict[str, Any]) -> dict[str, Any]:
    run = stack.trigger(stack.create_job(name, **spec))
    return stack.wait_finished(run["id"])


def test_python_job_runs_end_to_end(stack: Stack) -> None:
    run = run_job(
        stack,
        "hello",
        {
            "runtime": "python",
            "script": "import sys\nprint('hello from torqrun')\nprint('careful', file=sys.stderr)\n",
        },
    )
    assert run["status"] == "SUCCEEDED"
    assert run["exit_code"] == 0
    assert run["duration_seconds"] is not None and run["duration_seconds"] >= 0
    assert run["attempts"][0]["agent_name"] == "e2e-agent"
    assert [e["to_status"] for e in run["events"]] == [
        "QUEUED",
        "DISPATCHED",
        "STARTING",
        "RUNNING",
        "SUCCEEDED",
    ]
    assert [(c["stream"], c["data"]) for c in stack.logs(run["id"])] == [
        ("stdout", "hello from torqrun\n"),
        ("stderr", "careful\n"),
    ]


def test_failing_shell_job_reports_exit_code(stack: Stack) -> None:
    run = run_job(stack, "fails", {"runtime": "shell", "script": "echo 'disk full' >&2\nexit 7"})
    assert run["status"] == "FAILED"
    assert run["exit_code"] == 7
    assert "disk full" in run["error_summary"]


def test_timeout_is_enforced(stack: Stack) -> None:
    run = run_job(stack, "slow", {"runtime": "shell", "script": "sleep 30", "timeout_seconds": 1})
    assert run["status"] == "TIMED_OUT"


def test_live_stream_delivers_logs_then_end(stack: Stack) -> None:
    job = stack.http.post(
        "/api/v1/jobs",
        json={
            "name": "streamed",
            "spec": {
                "runtime": "shell",
                "script": "for i in 1 2 3; do echo line$i; sleep 0.3; done",
            },
        },
    ).json()
    run = stack.http.post(f"/api/v1/jobs/{job['id']}/runs").json()
    events: list[tuple[str, Any]] = []
    with stack.http.stream("GET", f"/api/v1/runs/{run['id']}/stream", timeout=30) as r:
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                events.append((event or "", json.loads(line[6:])))
                if event == "end":
                    break
    lines = [c["data"] for kind, data in events if kind == "logs" for c in data]
    assert lines == ["line1\n", "line2\n", "line3\n"]
    statuses = [data["status"] for kind, data in events if kind == "run"]
    assert statuses[-1] == "SUCCEEDED"
    assert events[-1] == ("end", {"status": "SUCCEEDED"})


def test_parallel_runs_use_both_slots(stack: Stack) -> None:
    job = stack.http.post(
        "/api/v1/jobs", json={"name": "par", "spec": {"runtime": "shell", "script": "sleep 1"}}
    ).json()
    runs = [stack.http.post(f"/api/v1/jobs/{job['id']}/runs").json() for _ in range(2)]
    started = time.monotonic()
    finished = [stack.wait_finished(r["id"]) for r in runs]
    assert all(f["status"] == "SUCCEEDED" for f in finished)
    assert time.monotonic() - started < 1.9  # default agent has 2 slots: they ran concurrently


def test_agent_restart_keeps_identity_and_history(stack: Stack) -> None:
    first = run_job(stack, "before-restart", {"runtime": "shell", "script": "echo one"})
    stack.stop("agent")  # graceful SIGTERM shutdown
    assert stack.procs["agent"].returncode == 0
    stack.start_agent()
    second = run_job(stack, "after-restart", {"runtime": "shell", "script": "echo two"})
    agents = stack.http.get("/api/v1/agents").json()
    assert [a["name"] for a in agents] == ["e2e-agent"]  # re-used its saved identity
    assert first["attempts"][0]["agent_id"] == second["attempts"][0]["agent_id"]
    assert stack.http.get(f"/api/v1/runs/{first['id']}").json()["status"] == "SUCCEEDED"
