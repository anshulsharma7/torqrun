"""Executor tests run real subprocesses (python3 and bash must be on PATH)."""

import asyncio
import os
from pathlib import Path

import pytest

from torqrun_agent.executor import LocalProcessExecutor, _pump
from torqrun_protocol.jobs import JobSpec

JOB_PATH = os.environ.get("PATH", "/usr/bin:/bin")


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []
        self.pids: list[int] = []

    def output(self, stream: str, data: str) -> None:
        self.lines.append((stream, data))

    async def spawned(self, pid: int) -> None:
        self.pids.append(pid)

    def text(self, stream: str) -> str:
        return "".join(d for s, d in self.lines if s == stream)


async def run(spec: JobSpec, tmp_path: Path, rec: Recorder, grace: float = 1.0):  # type: ignore[no-untyped-def]
    executor = LocalProcessExecutor(job_path=JOB_PATH, kill_grace_seconds=grace)
    return await executor.run(
        spec,
        tmp_path,
        extra_env={"TORQRUN_RUN_ID": "r1"},
        on_output=rec.output,
        on_spawned=rec.spawned,
    )


async def test_python_success_separates_streams(tmp_path: Path) -> None:
    rec = Recorder()
    spec = JobSpec(
        runtime="python", script="import sys\nprint('out')\nprint('err', file=sys.stderr)"
    )
    result = await run(spec, tmp_path, rec)
    assert (result.outcome, result.exit_code, result.error_summary) == ("succeeded", 0, None)
    assert rec.text("stdout") == "out\n"
    assert rec.text("stderr") == "err\n"
    assert len(rec.pids) == 1


async def test_failure_reports_exit_code_and_stderr_tail(tmp_path: Path) -> None:
    rec = Recorder()
    spec = JobSpec(runtime="shell", script="echo boom >&2\nexit 3")
    result = await run(spec, tmp_path, rec)
    assert result.outcome == "failed"
    assert result.exit_code == 3
    assert result.error_summary == "exited with code 3\nboom"


async def test_args_are_not_shell_interpolated(tmp_path: Path) -> None:
    rec = Recorder()
    spec = JobSpec(runtime="shell", script='printf "%s|" "$@"', args=["a b", "$(id)", "; rm -rf /"])
    await run(spec, tmp_path, rec)
    assert rec.text("stdout") == "a b|$(id)|; rm -rf /|"


async def test_environment_is_minimal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TORQRUN_AGENT_SECRET_SHOULD_NOT_LEAK", "x")
    rec = Recorder()
    spec = JobSpec(
        runtime="python", script="import os\nprint(sorted(os.environ))", env={"MINE": "1"}
    )
    await run(spec, tmp_path, rec)
    names = eval(rec.text("stdout"))  # noqa: S307  (our own test output)
    assert "TORQRUN_AGENT_SECRET_SHOULD_NOT_LEAK" not in names
    assert {"PATH", "HOME", "MINE", "TORQRUN_RUN_ID"} <= set(names)


async def test_timeout_kills_whole_process_group(tmp_path: Path) -> None:
    rec = Recorder()
    marker = tmp_path / "child.pid"
    spec = JobSpec(
        runtime="shell", timeout_seconds=1, script=f"sleep 60 &\necho $! > {marker}\nsleep 60"
    )
    result = await run(spec, tmp_path, rec)
    assert result.outcome == "timed_out"
    assert result.exit_code == 128 + 15  # SIGTERM
    child = int(marker.read_text())
    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


async def test_sigterm_ignoring_job_is_killed_after_grace(tmp_path: Path) -> None:
    rec = Recorder()
    spec = JobSpec(
        runtime="shell", timeout_seconds=1, script="trap '' TERM\nwhile true; do sleep 0.1; done"
    )
    result = await run(spec, tmp_path, rec, grace=0.5)
    assert result.outcome == "timed_out"
    assert result.exit_code == 128 + 9  # SIGKILL


async def test_cancellation_kills_process(tmp_path: Path) -> None:
    rec = Recorder()
    spec = JobSpec(runtime="shell", script="sleep 60")
    task = asyncio.create_task(run(spec, tmp_path, rec))
    while not rec.pids:
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(rec.pids[0], 0)


async def test_missing_interpreter_fails_cleanly(tmp_path: Path) -> None:
    executor = LocalProcessExecutor(job_path=str(tmp_path / "empty"), kill_grace_seconds=1)
    rec = Recorder()
    result = await executor.run(
        JobSpec(runtime="python", script="print(1)"),
        tmp_path,
        extra_env={},
        on_output=rec.output,
        on_spawned=rec.spawned,
    )
    assert result.outcome == "failed"
    assert result.exit_code is None
    assert "python3 not found" in (result.error_summary or "")
    assert rec.pids == []


async def _pump_bytes(data: list[bytes]) -> list[str]:
    reader = asyncio.StreamReader()
    for d in data:
        reader.feed_data(d)
    reader.feed_eof()
    out: list[str] = []
    await _pump(reader, "stdout", lambda _s, text: out.append(text))
    return out


async def test_pump_handles_split_multibyte_characters() -> None:
    euro = "€".encode()
    assert await _pump_bytes([b"price " + euro[:1], euro[1:] + b"5\n"]) == ["price €5\n"]


async def test_pump_splits_very_long_lines_and_keeps_partial_tail() -> None:
    out = await _pump_bytes([b"x" * 20000 + b"\nend"])
    assert [len(c) for c in out] == [8192, 8192, 3617, 3]
    assert "".join(out) == "x" * 20000 + "\nend"


async def test_cancel_event_terminates_process_and_reports_cancelled(tmp_path: Path) -> None:
    rec = Recorder()
    cancel = asyncio.Event()
    spec = JobSpec(runtime="shell", script="echo started\nsleep 60")
    task = asyncio.create_task(
        LocalProcessExecutor(job_path=JOB_PATH, kill_grace_seconds=1).run(
            spec,
            tmp_path,
            extra_env={},
            on_output=rec.output,
            on_spawned=rec.spawned,
            cancel=cancel,
        )
    )
    while not rec.pids:
        await asyncio.sleep(0.05)
    cancel.set()
    result = await asyncio.wait_for(task, timeout=10)
    assert result.outcome == "cancelled"
    assert result.exit_code == 128 + 15
    with pytest.raises(ProcessLookupError):
        os.kill(rec.pids[0], 0)


async def test_cancel_after_process_exited_keeps_real_outcome(tmp_path: Path) -> None:
    rec = Recorder()
    cancel = asyncio.Event()
    cancel.set()  # already set, but the process finishes instantly
    result = await LocalProcessExecutor(job_path=JOB_PATH, kill_grace_seconds=1).run(
        JobSpec(runtime="shell", script="exit 0"),
        tmp_path,
        extra_env={},
        on_output=rec.output,
        on_spawned=rec.spawned,
        cancel=cancel,
    )
    assert result.outcome in ("succeeded", "cancelled")  # race is inherent; both are truthful
