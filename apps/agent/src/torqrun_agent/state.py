"""Durable local state: running-process records and the result spool.

Both live under the agent's state dir and survive agent restarts:

* ``running/<attempt>.json``: the process group of every job in flight. After a crash the
  agent kills leftovers on startup (verifying the PID's start time, so a recycled PID is never
  touched) and reports those attempts as lost.
* ``spool/<attempt>.json``: a final result is written here *before* it is sent and deleted once
  the control plane accepts it (or definitively rejects it), so results survive API outages
  and agent restarts.
"""

import contextlib
import json
import os
import signal
from pathlib import Path
from typing import Any
from uuid import UUID


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read_all(directory: Path) -> list[tuple[Path, dict[str, Any]]]:
    out: list[tuple[Path, dict[str, Any]]] = []
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            out.append((path, json.loads(path.read_text())))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)  # torn write from a crash: nothing usable
    return out


def process_start_time(pid: int) -> int | None:
    """Kernel start time of ``pid`` in clock ticks (Linux), or None if unknown/not running."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    # The command name (field 2) may contain spaces; fields after the closing ')' are fixed.
    fields = stat[stat.rindex(")") + 2 :].split()
    return int(fields[19])  # field 22 overall


class LocalState:
    def __init__(self, state_dir: Path) -> None:
        self.running_dir = state_dir / "running"
        self.spool_dir = state_dir / "spool"

    # running-process records ----------------------------------------------------------
    def record_running(self, attempt_id: UUID, *, pid: int, lease_token: str, run_id: UUID) -> None:
        _write_json(
            self.running_dir / f"{attempt_id}.json",
            {
                "attempt_id": str(attempt_id),
                "run_id": str(run_id),
                "pid": pid,
                "start_time": process_start_time(pid),
                "lease_token": lease_token,
            },
        )

    def clear_running(self, attempt_id: UUID) -> None:
        (self.running_dir / f"{attempt_id}.json").unlink(missing_ok=True)

    def leftover_running(self) -> list[dict[str, Any]]:
        return [data for _, data in _read_all(self.running_dir)]

    @staticmethod
    def kill_if_same_process(pid: int, start_time: int | None) -> bool:
        """SIGKILL the process group of ``pid`` only if it is provably the process we started."""
        if start_time is None or process_start_time(pid) != start_time:
            return False
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
            return True
        return False

    # result spool ---------------------------------------------------------------------
    def spool(self, attempt_id: UUID, body: dict[str, Any]) -> None:
        _write_json(
            self.spool_dir / f"{attempt_id}.json", {"attempt_id": str(attempt_id), "body": body}
        )

    def unspool(self, attempt_id: UUID) -> None:
        (self.spool_dir / f"{attempt_id}.json").unlink(missing_ok=True)

    def spooled(self) -> list[tuple[UUID, dict[str, Any]]]:
        return [(UUID(d["attempt_id"]), d["body"]) for _, d in _read_all(self.spool_dir)]
