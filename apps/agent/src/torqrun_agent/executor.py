"""Local-process executor: runs a job script as a child process of the agent.

This is NOT a sandbox. The script runs with the agent's OS user and can do anything that user
can. For isolation use the container executor (``docker_executor``). What this module
does guarantee:

* argv-only spawning (no shell interpolation of parameters),
* a minimal, explicit environment (the agent's own variables and credentials are not passed),
* its own process group, so timeout/cancel kills the whole tree, including grandchildren,
* separate stdout/stderr capture with per-line timestamps.
"""

import asyncio
import codecs
import contextlib
import logging
import os
import shutil
import signal
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from torqrun_protocol.jobs import JobSpec

logger = logging.getLogger(__name__)

Stream = Literal["stdout", "stderr"]
OutputCallback = Callable[[Stream, str], None]
SpawnCallback = Callable[[int], Awaitable[None]]

READ_SIZE = 64 * 1024
MAX_LINE = 8 * 1024  # longer lines are split so one chunk never exceeds the protocol limit
DRAIN_AFTER_EXIT_SECONDS = 2.0
STDERR_TAIL_LINES = 20


class ExecutorError(Exception):
    """The job can't be started by this executor (reported as a failed run)."""


@dataclass(frozen=True)
class ExecutionResult:
    outcome: Literal["succeeded", "failed", "timed_out", "cancelled"]
    exit_code: int | None
    error_summary: str | None


def _exit_code(returncode: int) -> tuple[int, str | None]:
    """Normalise a returncode; signals become 128+N like a shell reports them."""
    if returncode < 0:
        sig = -returncode
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = f"signal {sig}"
        return 128 + sig, f"terminated by {name}"
    return returncode, None


class LocalProcessExecutor:
    def __init__(self, *, job_path: str, kill_grace_seconds: float) -> None:
        self._job_path = job_path
        self._kill_grace = kill_grace_seconds

    def command(self, spec: JobSpec, script: Path) -> list[str]:
        if spec.runtime == "python":
            interpreter = shutil.which("python3", path=self._job_path)
        else:
            interpreter = shutil.which("bash", path=self._job_path)
        if interpreter is None:
            name = "python3" if spec.runtime == "python" else "bash"
            raise FileNotFoundError(f"{name} not found on job PATH {self._job_path!r}")
        return [interpreter, str(script), *spec.args]

    def environment(self, spec: JobSpec, workspace: Path, extra: dict[str, str]) -> dict[str, str]:
        env = {
            "PATH": self._job_path,
            "HOME": str(workspace),
            "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",  # so Python output streams live instead of at exit
        }
        if tz := os.environ.get("TZ"):
            env["TZ"] = tz
        env.update(spec.env)
        env.update(extra)  # TORQRUN_* run metadata; spec cannot override (validated)
        return env

    def launch(
        self, spec: JobSpec, workspace: Path, script: Path, extra: dict[str, str]
    ) -> tuple[list[str], dict[str, str]]:
        """The argv and environment of the process to start."""
        return self.command(spec, script), self.environment(spec, workspace, extra)

    async def run(
        self,
        spec: JobSpec,
        workspace: Path,
        *,
        extra_env: dict[str, str],
        on_output: OutputCallback,
        on_spawned: SpawnCallback,
        cancel: asyncio.Event | None = None,
    ) -> ExecutionResult:
        """Run ``spec`` to completion, timeout, or until ``cancel`` is set (then the process
        group is terminated and the outcome is "cancelled", unless it had already exited)."""
        script = workspace / ("main.py" if spec.runtime == "python" else "main.sh")
        script.write_text(spec.script)
        script.chmod(0o700)
        stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)

        def capture(stream: Stream, text: str) -> None:
            if stream == "stderr":
                stderr_tail.append(text)
            on_output(stream, text)

        try:
            argv, env = self.launch(spec, workspace, script, extra_env)
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workspace,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,  # own process group -> killpg reaches the whole tree
            )
        except (OSError, ExecutorError) as exc:
            return ExecutionResult("failed", None, f"could not start process: {exc}")

        if proc.stdout is None or proc.stderr is None:  # pragma: no cover - PIPE was requested
            raise RuntimeError("subprocess pipes missing")
        readers = [
            asyncio.create_task(_pump(proc.stdout, "stdout", capture)),
            asyncio.create_task(_pump(proc.stderr, "stderr", capture)),
        ]
        try:
            await on_spawned(proc.pid)
            waiter = asyncio.ensure_future(proc.wait())
            watchers: set[asyncio.Future[Any]] = {waiter}
            canceller = asyncio.ensure_future(cancel.wait()) if cancel is not None else None
            if canceller is not None:
                watchers.add(canceller)
            try:
                await asyncio.wait(
                    watchers, timeout=spec.timeout_seconds, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                if canceller is not None and not canceller.done():
                    canceller.cancel()
            timed_out = cancelled = False
            if waiter.done():
                returncode = waiter.result()
            elif canceller is not None and canceller.done():
                cancelled = True
                returncode = await self._terminate(proc)
            else:
                timed_out = True
                returncode = await self._terminate(proc)
            # Processes that outlive the main one (e.g. backgrounded with &) keep the pipes open.
            # Give output a moment to drain, then kill whatever is left in the group.
            _, pending = await asyncio.wait(readers, timeout=DRAIN_AFTER_EXIT_SECONDS)
            if pending:
                _killpg(proc.pid, signal.SIGKILL)
                await asyncio.wait(pending, timeout=DRAIN_AFTER_EXIT_SECONDS)
        except BaseException:
            # Cancellation (shutdown, lease loss) or a failed `started` report: never leave an
            # orphaned job running behind the agent's back.
            await asyncio.shield(self._terminate(proc))
            raise
        finally:
            for task in readers:
                task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)

        code, signal_note = _exit_code(returncode)
        if cancelled:
            return ExecutionResult("cancelled", code, "cancelled by user; process group terminated")
        if timed_out:
            return ExecutionResult(
                "timed_out", code, f"timed out after {spec.timeout_seconds}s; process group killed"
            )
        if code == 0:
            return ExecutionResult("succeeded", 0, None)
        tail = "".join(stderr_tail).strip()
        summary = signal_note or f"exited with code {code}"
        if tail:
            summary = f"{summary}\n{tail}"
        return ExecutionResult("failed", code, summary[-4000:])

    async def _terminate(self, proc: asyncio.subprocess.Process) -> int:
        """SIGTERM the process group, then SIGKILL after the grace period."""
        _killpg(proc.pid, signal.SIGTERM)
        try:
            return await asyncio.wait_for(proc.wait(), timeout=self._kill_grace)
        except TimeoutError:
            _killpg(proc.pid, signal.SIGKILL)
            return await proc.wait()


def _killpg(pgid: int, sig: signal.Signals) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, sig)


async def _pump(reader: asyncio.StreamReader, stream: Stream, emit: OutputCallback) -> None:
    """Forward output line by line, decoding UTF-8 incrementally (multi-byte safe)."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    while True:
        data = await reader.read(READ_SIZE)
        if not data:
            break
        pending += decoder.decode(data)
        while True:
            newline = pending.find("\n")
            if newline == -1 or newline >= MAX_LINE:
                if len(pending) >= MAX_LINE:
                    emit(stream, pending[:MAX_LINE])
                    pending = pending[MAX_LINE:]
                    continue
                break
            emit(stream, pending[: newline + 1])
            pending = pending[newline + 1 :]
    pending += decoder.decode(b"", final=True)
    if pending:
        emit(stream, pending)
