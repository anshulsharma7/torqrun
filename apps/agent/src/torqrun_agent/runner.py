"""Agent main loop: heartbeats, claiming work, running assignments, and crash recovery."""

import asyncio
import contextlib
import logging
import re
import shutil
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx

from torqrun_agent import docker_executor, identity, sysinfo
from torqrun_agent.client import AuthError, ControlPlane, ControlPlaneError, LeaseLostError
from torqrun_agent.config import AgentSettings
from torqrun_agent.executor import ExecutionResult, LocalProcessExecutor
from torqrun_agent.logship import LogShipper
from torqrun_agent.redact import Redactor
from torqrun_agent.state import LocalState
from torqrun_protocol.agent import (
    Assignment,
    ClaimRequest,
    CompleteRequest,
    HeartbeatRequest,
    LeaseRef,
    LogBatch,
    LogChunk,
    StartedRequest,
)

logger = logging.getLogger(__name__)

ERROR_BACKOFF_MAX = 30.0


@dataclass
class _Running:
    assignment: Assignment
    task: asyncio.Task[None] | None = None
    lease_lost: bool = field(default=False)
    # Set when the control plane asks to cancel (heartbeat or log-upload response).
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


ARTIFACTS = "artifacts"
ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


def detect_capabilities(settings: AgentSettings) -> tuple[list[str], str | None]:
    """Executors this agent offers, and the docker binary if the container executor is on."""
    if settings.docker == "off":
        return ["process"], None
    binary = docker_executor.find_docker()
    if binary and docker_executor.docker_available(binary):
        return ["process", "docker"], binary
    if settings.docker == "on":
        raise SystemExit("TORQRUN_AGENT_DOCKER=on but no working Docker daemon was found")
    return ["process"], None


class Agent:
    def __init__(
        self,
        settings: AgentSettings,
        client: ControlPlane,
        ident: identity.Identity | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.ident = ident
        self._status = "ONLINE"
        self.executor = LocalProcessExecutor(
            job_path=settings.job_path, kill_grace_seconds=settings.kill_grace_seconds
        )
        self.capabilities, docker_bin = detect_capabilities(settings)
        self.docker = (
            docker_executor.DockerExecutor(
                binary=docker_bin,
                agent_name=settings.name,
                kill_grace_seconds=settings.kill_grace_seconds,
            )
            if docker_bin
            else None
        )
        logger.info("executors: %s", ", ".join(self.capabilities))
        self.state = LocalState(settings.state_dir)
        self._running: dict[UUID, _Running] = {}
        self._stopping = asyncio.Event()
        self._slot_freed = asyncio.Event()
        self._fatal: BaseException | None = None

    def stop(self) -> None:
        if not self._stopping.is_set():
            logger.info("shutdown requested: no new work will be claimed")
        self._stopping.set()

    async def run(self) -> None:
        self._recover_after_crash()
        await self._flush_spool()
        heartbeat = asyncio.create_task(self._heartbeat_loop(), name="heartbeat")
        try:
            await self._claim_loop()
        finally:
            await self._drain()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
        if self._fatal is not None:
            raise self._fatal

    # Heartbeats -------------------------------------------------------------------------

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                await self._heartbeat_once()
            except AuthError as exc:
                logger.error("control plane rejected this agent: %s", exc)
                self._fatal = exc
                self.stop()
                return
            except (httpx.HTTPError, ControlPlaneError) as exc:
                logger.warning("heartbeat failed: %s", exc)
            await asyncio.sleep(self.settings.heartbeat_interval_seconds)

    async def _heartbeat_once(self) -> None:
        running = [
            LeaseRef(attempt_id=r.assignment.attempt_id, lease_token=r.assignment.lease_token)
            for r in self._running.values()
        ]
        response = await self.client.heartbeat(
            HeartbeatRequest(
                running=running,
                free_slots=self._free_slots(),
                system=sysinfo.collect(self.settings.state_dir),
                capabilities=self.capabilities,
            )
        )
        if response.agent_status != self._status:
            logger.info("control plane reports this agent as %s", response.agent_status)
            self._status = response.agent_status
        for attempt_id in response.revoked_leases:
            entry = self._running.get(attempt_id)
            if entry is not None and entry.task is not None:
                logger.warning("lease revoked by control plane; stopping attempt %s", attempt_id)
                entry.lease_lost = True
                entry.task.cancel()
        for attempt_id in response.cancel:
            entry = self._running.get(attempt_id)
            if entry is not None and not entry.cancel.is_set():
                logger.info("cancel requested for attempt %s", attempt_id)
                entry.cancel.set()
        await self._flush_spool()
        await self._maybe_rotate_credential()

    async def _maybe_rotate_credential(self) -> None:
        """Swap the credential for a fresh one every ``credential_rotate_days``. The new one is
        saved before use; the server keeps the old one valid for 5 minutes as a safety net."""
        if self.ident is None:
            return
        issued = datetime.fromisoformat(self.ident.issued_at) if self.ident.issued_at else None
        if issued is not None and datetime.now(UTC) - issued < timedelta(
            days=self.settings.credential_rotate_days
        ):
            return
        if issued is None:  # identity from an older agent version: start the clock now
            self.ident = replace(self.ident, issued_at=datetime.now(UTC).isoformat())
            identity.save(self.settings.state_dir, self.ident)
            return
        try:
            fresh = await self.client.rotate_credential()
        except (httpx.HTTPError, ControlPlaneError) as exc:
            logger.warning("credential rotation failed (%s); will retry", exc)
            return
        self.ident = replace(
            self.ident, credential=fresh.credential, issued_at=datetime.now(UTC).isoformat()
        )
        identity.save(self.settings.state_dir, self.ident)
        self.client.set_credential(fresh.credential)
        logger.info("agent credential rotated")

    # Crash recovery and result spool ----------------------------------------------------

    def _recover_after_crash(self) -> None:
        """Kill job processes left behind by a previous agent process and report them lost.

        Without this, a job could keep running unsupervised while the control plane, after its
        lease expires, decides to retry it elsewhere.
        """
        if self.docker is not None:
            removed = docker_executor.remove_leftover_containers(
                self.docker.binary, self.settings.name
            )
            if removed:
                logger.warning("removed %d job container(s) left by a previous run", removed)
        for record in self.state.leftover_running():
            attempt_id = UUID(record["attempt_id"])
            killed = self.state.kill_if_same_process(record["pid"], record.get("start_time"))
            note = "killed the leftover process" if killed else "its process had already exited"
            logger.warning("attempt %s was interrupted by an agent restart; %s", attempt_id, note)
            self.state.spool(
                attempt_id,
                {
                    "complete": CompleteRequest(
                        lease_token=record["lease_token"],
                        outcome="lost",
                        exit_code=None,
                        finished_at=datetime.now(UTC),
                        error_summary=f"agent restarted while the job was running; {note}",
                    ).model_dump(mode="json"),
                    "logs": [],
                },
            )
            self.state.clear_running(attempt_id)
            if not self.settings.keep_workspaces:
                shutil.rmtree(
                    self.settings.state_dir / "work" / str(attempt_id), ignore_errors=True
                )

    async def _report(
        self, attempt_id: UUID, result: CompleteRequest, unsent_logs: list[LogChunk] | None = None
    ) -> None:
        """Deliver a final result durably: spool first, delete once accepted or rejected.

        Log lines that could not be uploaded travel with the result and are sent before it, so
        "finished" still implies "all output stored".
        """
        logs = unsent_logs or []
        self.state.spool(
            attempt_id,
            {
                "complete": result.model_dump(mode="json"),
                "logs": [c.model_dump(mode="json") for c in logs],
            },
        )
        await self._deliver(attempt_id, result, logs)

    async def _deliver(
        self, attempt_id: UUID, result: CompleteRequest, logs: list[LogChunk]
    ) -> bool:
        try:
            for i in range(0, len(logs), 500):  # idempotent by seq: resending is harmless
                await self.client.logs(
                    attempt_id, LogBatch(lease_token=result.lease_token, chunks=logs[i : i + 500])
                )
            await self.client.complete(attempt_id, result)
        except AuthError:
            raise
        except ControlPlaneError as exc:  # lease gone or invalid: retrying cannot help
            logger.warning("result for %s rejected (%s); dropping it", attempt_id, exc)
        except httpx.HTTPError as exc:
            logger.warning("result for %s not delivered yet (%s); kept in spool", attempt_id, exc)
            return False
        self.state.unspool(attempt_id)
        logger.info("delivered result for %s", attempt_id)
        return True

    async def _flush_spool(self) -> None:
        for attempt_id, body in self.state.spooled():
            if attempt_id in self._running:
                continue  # still being reported by its own task
            result = CompleteRequest.model_validate(body["complete"])
            logs = [LogChunk.model_validate(c) for c in body.get("logs", [])]
            if not await self._deliver(attempt_id, result, logs):
                return  # control plane unreachable: try again on the next heartbeat

    # Claiming ---------------------------------------------------------------------------

    def _free_slots(self) -> int:
        return 0 if self._stopping.is_set() else self.settings.max_slots - len(self._running)

    async def _claim_loop(self) -> None:
        backoff = 1.0
        while not self._stopping.is_set():
            free = self._free_slots()
            if free <= 0:
                self._slot_freed.clear()
                await _race(self._slot_freed.wait(), self._stopping)
                continue
            try:
                response = await _race(
                    self.client.claim(
                        ClaimRequest(free_slots=free, wait_seconds=self.settings.claim_wait_seconds)
                    ),
                    self._stopping,
                )
            except AuthError as exc:
                logger.error("control plane rejected this agent: %s", exc)
                self._fatal = exc
                return
            except (httpx.HTTPError, ControlPlaneError) as exc:
                logger.warning("claim failed: %s; retrying in %.0fs", exc, backoff)
                await _race(asyncio.sleep(backoff), self._stopping)
                backoff = min(ERROR_BACKOFF_MAX, backoff * 2)
                continue
            backoff = 1.0
            if response is None:  # stopping
                return
            for assignment in response.assignments:
                entry = _Running(assignment)
                self._running[assignment.attempt_id] = entry
                entry.task = asyncio.create_task(
                    self._execute(entry), name=f"attempt-{assignment.attempt_id}"
                )

    async def _drain(self) -> None:
        tasks = [r.task for r in self._running.values() if r.task is not None]
        if not tasks:
            return
        logger.info(
            "waiting up to %.0fs for %d running job(s)",
            self.settings.shutdown_grace_seconds,
            len(tasks),
        )
        _, pending = await asyncio.wait(tasks, timeout=self.settings.shutdown_grace_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    # Executing one assignment -----------------------------------------------------------

    async def _execute(self, entry: _Running) -> None:
        a = entry.assignment
        log = logging.LoggerAdapter(logger, {"attempt_id": str(a.attempt_id)})
        workspace = self.settings.state_dir / "work" / str(a.attempt_id)
        try:
            try:
                # Ack BEFORE spawning: if the ack fails the control plane may safely requeue.
                await self.client.ack(a.attempt_id, a.lease_token)
            except (LeaseLostError, ControlPlaneError, httpx.HTTPError) as exc:
                log.warning("could not acknowledge %s (%s); not running it", a.job_name, exc)
                return
            log.info("running %s v%d (run %s)", a.job_name, a.job_version, a.run_id)
            workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
            result, last_seq, unsent = await self._run_process(entry, workspace)
            await self._report(
                a.attempt_id,
                CompleteRequest(
                    lease_token=a.lease_token,
                    outcome=result.outcome,
                    exit_code=result.exit_code,
                    finished_at=datetime.now(UTC),
                    error_summary=result.error_summary,
                    last_log_seq=last_seq,
                ),
                unsent,
            )
            log.info("%s finished: %s (exit code %s)", a.job_name, result.outcome, result.exit_code)
        except LeaseLostError:
            log.warning("lease lost for %s; process stopped, result not reported", a.job_name)
        except asyncio.CancelledError:
            if entry.lease_lost:
                return
            # Shutdown with the job still running: report the truth rather than leaving it hanging.
            with contextlib.suppress(Exception):
                await asyncio.shield(
                    self._report(
                        a.attempt_id,
                        CompleteRequest(
                            lease_token=a.lease_token,
                            outcome="failed",
                            exit_code=None,
                            finished_at=datetime.now(UTC),
                            error_summary="agent shut down before the job finished; process killed",
                        ),
                    )
                )
            raise
        except (ControlPlaneError, httpx.HTTPError) as exc:
            log.error("job %s failed to start or report: %s", a.job_name, exc)
        finally:
            self.state.clear_running(a.attempt_id)
            self._running.pop(a.attempt_id, None)
            self._slot_freed.set()
            if not self.settings.keep_workspaces:
                shutil.rmtree(workspace, ignore_errors=True)

    async def _upload_artifacts(
        self,
        a: Assignment,
        directory: Path,
        emit: Callable[[Literal["stdout", "stderr", "system"], str], None],
    ) -> None:
        """Send files the job left in $TORQRUN_ARTIFACTS_DIR. Problems are reported in the run's
        log but never change its outcome."""
        files, skipped = await asyncio.to_thread(_artifact_files, directory)
        for note in skipped:
            emit("system", f"artifact skipped: {note}\n")
        for path in files:
            try:
                size = path.lstat().st_size
                await self.client.upload_artifact(a.attempt_id, a.lease_token, path.name, path)
            except LeaseLostError:
                raise
            except (ControlPlaneError, OSError) as exc:
                emit("system", f"artifact {path.name} not uploaded: {exc}\n")
            else:
                emit("system", f"artifact {path.name} uploaded ({size} bytes)\n")

    async def _run_process(
        self, entry: _Running, workspace: Path
    ) -> tuple[ExecutionResult, int | None, list[LogChunk]]:
        a = entry.assignment

        async def send(batch: list[LogChunk]) -> None:
            response = await self.client.logs(
                a.attempt_id, LogBatch(lease_token=a.lease_token, chunks=batch)
            )
            if response.cancel_requested and not entry.cancel.is_set():
                logger.info("cancel requested for attempt %s", a.attempt_id)
                entry.cancel.set()

        shipper = LogShipper(
            send,
            max_bytes=self.settings.max_log_bytes,
            flush_interval=self.settings.log_flush_interval_seconds,
        )

        async def on_spawned(pid: int) -> None:
            # Record locally first: if the agent dies from here on, the next start cleans up.
            self.state.record_running(
                a.attempt_id, pid=pid, lease_token=a.lease_token, run_id=a.run_id
            )
            await self.client.started(
                a.attempt_id,
                StartedRequest(lease_token=a.lease_token, pid=pid, started_at=datetime.now(UTC)),
            )

        redact = Redactor(a.secret_env.values())

        def emit(stream: Literal["stdout", "stderr", "system"], text: str) -> None:
            shipper.emit(stream, redact(text))

        if a.missing_secrets:
            names = ", ".join(a.missing_secrets)
            emit("system", f"secret(s) not available: {names}; the job was not started\n")
            await shipper.flush()
            return (
                ExecutionResult("failed", None, f"secret(s) not available: {names}"),
                shipper.last_seq,
                shipper.unsent(),
            )

        executor = self.docker if a.spec.executor == "docker" else self.executor
        if executor is None:
            emit("system", "this agent cannot run container jobs (no Docker); not started\n")
            await shipper.flush()
            failure = ExecutionResult("failed", None, "agent has no Docker for a container job")
            return failure, shipper.last_seq, shipper.unsent()

        artifacts = workspace / ARTIFACTS
        artifacts.mkdir(mode=0o700, exist_ok=True)
        artifacts_env = f"/workspace/{ARTIFACTS}" if executor is self.docker else str(artifacts)

        flusher = asyncio.create_task(shipper.run())
        execution = asyncio.create_task(
            executor.run(
                a.spec,
                workspace,
                extra_env={
                    "TORQRUN_RUN_ID": str(a.run_id),
                    "TORQRUN_ATTEMPT": str(a.attempt_no),
                    "TORQRUN_JOB_NAME": a.job_name,
                    "TORQRUN_ARTIFACTS_DIR": artifacts_env,
                    **(
                        {
                            "TORQRUN_WORKFLOW_RUN_ID": str(a.workflow_run_id),
                            "TORQRUN_TASK": a.task_key or "",
                        }
                        if a.workflow_run_id
                        else {}
                    ),
                    **a.secret_env,
                },
                on_output=emit,
                on_spawned=on_spawned,
                cancel=entry.cancel,
            )
        )
        try:
            # The flusher only ends early on a fatal error (lease lost, auth): stop the job then.
            await asyncio.wait({execution, flusher}, return_when=asyncio.FIRST_COMPLETED)
            if flusher.done():
                execution.cancel()
                await asyncio.gather(execution, return_exceptions=True)
                flusher.result()  # re-raise the fatal error
            result = execution.result()
        finally:
            for task in (execution, flusher):
                if not task.done():
                    task.cancel()
            await asyncio.gather(execution, flusher, return_exceptions=True)
        await self._upload_artifacts(a, artifacts, emit)
        # Every log line must be stored before completion is reported (the UI relies on it).
        # If the control plane is unreachable right now, the remaining lines are handed back
        # and spooled with the result instead of being lost, or blocking the report.
        try:
            await shipper.flush()
        except (LeaseLostError, AuthError):
            raise
        except (httpx.HTTPError, ControlPlaneError) as exc:
            logger.warning("final log upload for %s failed (%s); spooling", a.attempt_id, exc)
        if result.error_summary and redact:
            result = replace(result, error_summary=redact(result.error_summary))
        return result, shipper.last_seq, shipper.unsent()


def _artifact_files(directory: Path) -> tuple[list[Path], list[str]]:
    """Regular files directly in ``directory``; everything else is skipped with a reason."""
    if directory.is_symlink() or not directory.is_dir():
        return [], [f"{ARTIFACTS}/ was replaced by something other than a directory; ignored"]
    files: list[Path] = []
    skipped: list[str] = []
    for entry in sorted(directory.iterdir()):
        st = entry.lstat()
        if stat.S_ISLNK(st.st_mode):
            skipped.append(f"{entry.name}: symbolic links are not uploaded")
        elif stat.S_ISDIR(st.st_mode):
            skipped.append(f"{entry.name}/: directories are not uploaded (archive them first)")
        elif not stat.S_ISREG(st.st_mode):
            skipped.append(f"{entry.name}: not a regular file")
        elif not ARTIFACT_NAME.match(entry.name):
            skipped.append(f"{entry.name}: use letters, digits, '.', '-', '_' in file names")
        else:
            files.append(entry)
    return files, skipped


async def _race[T](work: Awaitable[T], stop: asyncio.Event) -> T | None:
    """Await ``work`` unless ``stop`` is set first; then cancel ``work`` and return None."""
    work_task = asyncio.ensure_future(work)
    stop_task = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({work_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (work_task, stop_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(work_task, stop_task, return_exceptions=True)
    if work_task.done() and not work_task.cancelled():
        return work_task.result()
    return None
