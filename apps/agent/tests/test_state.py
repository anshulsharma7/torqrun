import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from torqrun_agent.state import LocalState, process_start_time

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc is Linux-only")


def test_spool_round_trip_and_removal(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    a, b = uuid.uuid4(), uuid.uuid4()
    state.spool(a, {"outcome": "succeeded"})
    state.spool(b, {"outcome": "failed"})
    assert dict(state.spooled()) == {a: {"outcome": "succeeded"}, b: {"outcome": "failed"}}
    state.unspool(a)
    assert [x for x, _ in state.spooled()] == [b]


def test_spool_files_are_private(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    state.spool(uuid.uuid4(), {"lease_token": "secret"})
    [f] = (tmp_path / "spool").glob("*.json")
    assert f.stat().st_mode & 0o077 == 0


def test_torn_spool_file_is_discarded(tmp_path: Path) -> None:
    (tmp_path / "spool").mkdir()
    (tmp_path / "spool" / f"{uuid.uuid4()}.json").write_text("{not json")
    assert LocalState(tmp_path).spooled() == []


@linux_only
def test_kill_only_the_exact_process_we_started(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)  # noqa: S607
    try:
        start = process_start_time(proc.pid)
        assert start is not None
        assert not LocalState.kill_if_same_process(proc.pid, start + 1)  # "recycled" PID: untouched
        assert proc.poll() is None
        assert LocalState.kill_if_same_process(proc.pid, start)
        assert proc.wait(timeout=5) == -signal.SIGKILL
    finally:
        if proc.poll() is None:
            proc.kill()


@linux_only
def test_running_records_survive_and_clear(tmp_path: Path) -> None:
    state = LocalState(tmp_path)
    attempt, run = uuid.uuid4(), uuid.uuid4()
    state.record_running(attempt, pid=os.getpid(), lease_token="tok", run_id=run)
    [rec] = state.leftover_running()
    assert rec["attempt_id"] == str(attempt)
    assert rec["start_time"] == process_start_time(os.getpid())
    state.clear_running(attempt)
    assert state.leftover_running() == []
