"""M5 acceptance: a real diamond workflow on the real agent and scheduler."""

from pathlib import Path

from tests.e2e.conftest import Stack, wait_for

DIAMOND = """
tasks:
  extract: {job: wf-extract}
  left:    {job: wf-left, depends_on: [extract]}
  right:   {job: wf-right, depends_on: [extract]}
  join:    {job: wf-join, depends_on: [left, right]}
"""


def stamp(log: Path, name: str, seconds: float = 1.0) -> str:
    return f'echo "{name} start $(date +%s.%N)" >> {log}; sleep {seconds}; echo "{name} end $(date +%s.%N)" >> {log}'


def test_diamond_workflow_runs_in_order_with_parallel_middle(stack: Stack, tmp_path: Path) -> None:
    log = tmp_path / "wf.log"
    for name in ("extract", "left", "right", "join"):
        stack.create_job(f"wf-{name}", runtime="shell", script=stamp(log, name))
    wf = stack.http.post("/api/v1/workflows", json={"name": "diamond", "source": DIAMOND}).json()
    wr = stack.http.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    done = wait_for(
        lambda: (
            (d := stack.http.get(f"/api/v1/workflow-runs/{wr['id']}").json())["status"] != "RUNNING"
            and d
        ),
        60,
        "workflow to finish",
    )
    assert done["status"] == "SUCCEEDED"
    t = {}
    for line in log.read_text().splitlines():
        name, kind, ts = line.split()
        t[(name, kind)] = float(ts)
    assert t[("extract", "end")] <= t[("left", "start")]
    assert t[("extract", "end")] <= t[("right", "start")]
    # The two branches overlapped (the agent has 2 slots).
    assert t[("left", "start")] < t[("right", "end")] and t[("right", "start")] < t[("left", "end")]
    assert t[("join", "start")] >= max(t[("left", "end")], t[("right", "end")])
    for task in done["tasks"]:
        run = stack.run(task["run_id"])
        assert run["trigger"] == "workflow"


def test_failed_branch_then_rerun_failed(stack: Stack, tmp_path: Path) -> None:
    flag = tmp_path / "fixed"
    stack.create_job("wf-a", runtime="shell", script="echo a")
    stack.create_job(
        "wf-b",
        runtime="shell",
        script=f'[ -f {flag} ] || {{ echo "not fixed yet" >&2; exit 3; }}; echo "TASK=$TORQRUN_TASK"',
    )
    stack.create_job("wf-c", runtime="shell", script="echo c")
    src = "tasks:\n  a: {job: wf-a}\n  b: {job: wf-b, depends_on: [a]}\n  c: {job: wf-c, depends_on: [b]}\n"
    wf = stack.http.post("/api/v1/workflows", json={"name": "fragile", "source": src}).json()
    wr = stack.http.post(f"/api/v1/workflows/{wf['id']}/runs").json()
    first = wait_for(
        lambda: (
            (d := stack.http.get(f"/api/v1/workflow-runs/{wr['id']}").json())["status"] != "RUNNING"
            and d
        ),
        60,
        "first run",
    )
    assert first["status"] == "FAILED"
    assert {t["key"]: t["state"] for t in first["tasks"]} == {
        "a": "SUCCEEDED",
        "b": "FAILED",
        "c": "SKIPPED",
    }

    flag.touch()  # "fix" the problem, then rerun only what failed
    again = stack.http.post(f"/api/v1/workflow-runs/{wr['id']}/rerun?mode=failed").json()
    second = wait_for(
        lambda: (
            (d := stack.http.get(f"/api/v1/workflow-runs/{again['id']}").json())["status"]
            != "RUNNING"
            and d
        ),
        60,
        "rerun",
    )
    assert second["status"] == "SUCCEEDED"
    tasks = {t["key"]: t for t in second["tasks"]}
    assert tasks["a"]["reused"] is True
    assert [c["data"] for c in stack.logs(tasks["b"]["run_id"])] == [
        "TASK=b\n"
    ]  # task context in env
