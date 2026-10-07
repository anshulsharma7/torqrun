"""Container executor: runs each job in a throwaway Docker container.

The script and workspace are bind-mounted at ``/workspace``; the container runs as the agent's
own UID/GID with all Linux capabilities dropped, ``no-new-privileges`` and a PID limit, and is
removed when it exits. Job environment variables (including secrets) are handed to the
``docker`` CLI through its environment and forwarded with ``-e NAME``, so values never appear
in the command line (``ps``).

Requirements and limits:

* the Docker daemon must be local (bind mounts refer to the agent's filesystem);
* whoever can use the Docker socket is effectively root on that host. Run the agent as a
  dedicated user in the ``docker`` group and treat that host as belonging to Torqrun;
* the image must provide ``python3`` (python jobs) or ``bash`` (shell jobs).
"""

import asyncio
import contextlib
import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

from torqrun_agent.executor import (
    ExecutionResult,
    ExecutorError,
    LocalProcessExecutor,
    OutputCallback,
    SpawnCallback,
    _killpg,
)
from torqrun_protocol.jobs import JobSpec

logger = logging.getLogger(__name__)

WORKDIR = "/workspace"
LABEL = "torqrun.agent"
PIDS_LIMIT = "1024"
# Variables the docker CLI itself reads; a job variable with the same name is passed by value.
CLI_ENV = (
    "DOCKER_HOST",
    "DOCKER_CONFIG",
    "DOCKER_CONTEXT",
    "DOCKER_CERT_PATH",
    "DOCKER_TLS_VERIFY",
)


def find_docker() -> str | None:
    return shutil.which("docker")


def docker_available(binary: str | None = None, timeout: float = 5.0) -> bool:
    """True if the docker CLI exists and the daemon answers."""
    binary = binary or find_docker()
    if binary is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv
            [binary, "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def container_name(workspace: Path) -> str:
    return f"torqrun-{workspace.name}"  # the workspace is named after the attempt ID


class DockerExecutor(LocalProcessExecutor):
    def __init__(self, *, binary: str, agent_name: str, kill_grace_seconds: float) -> None:
        super().__init__(job_path="", kill_grace_seconds=kill_grace_seconds)
        self._docker = binary
        self._agent = agent_name
        self._names: dict[int, str] = {}  # CLI pid -> container name, for termination

    def launch(
        self, spec: JobSpec, workspace: Path, script: Path, extra: dict[str, str]
    ) -> tuple[list[str], dict[str, str]]:
        c = spec.container
        if c is None:  # guaranteed by JobSpec validation
            raise ExecutorError("no container settings")
        job_env = {"LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1", "HOME": WORKDIR}
        job_env.update(spec.env)
        job_env.update(extra)

        cli_env = {k: v for k in CLI_ENV if (v := os.environ.get(k))}
        if "DOCKER_CONFIG" not in cli_env and (home := os.environ.get("HOME")):
            cli_env["DOCKER_CONFIG"] = str(Path(home) / ".docker")
        argv = [
            self._docker,
            "run",
            "--rm",
            "--init",
            "--name",
            container_name(workspace),
            "--label",
            f"{LABEL}={self._agent}",
            "--pull",
            c.pull,
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            c.network,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            PIDS_LIMIT,
            "--volume",
            f"{workspace}:{WORKDIR}",
            "--workdir",
            WORKDIR,
        ]
        if c.memory_mb:
            argv += ["--memory", f"{c.memory_mb}m"]
        if c.cpus:
            argv += ["--cpus", str(c.cpus)]
        env = dict(cli_env)
        for name, value in job_env.items():
            if name in cli_env:
                argv += ["--env", f"{name}={value}"]  # can't share the CLI's own variable
            else:
                argv += ["--env", name]
                env[name] = value
        interpreter = "python3" if spec.runtime == "python" else "bash"
        argv += [c.image, interpreter, f"{WORKDIR}/{script.name}", *spec.args]
        return argv, env

    @property
    def binary(self) -> str:
        return self._docker

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
        # Remember which container belongs to which CLI process so _terminate can stop it.
        name = container_name(workspace)

        async def spawned(pid: int) -> None:
            self._names[pid] = name
            await on_spawned(pid)

        try:
            return await super().run(
                spec,
                workspace,
                extra_env=extra_env,
                on_output=on_output,
                on_spawned=spawned,
                cancel=cancel,
            )
        finally:
            for pid in [p for p, n in self._names.items() if n == name]:
                self._names.pop(pid, None)

    async def _terminate(self, proc: asyncio.subprocess.Process) -> int:
        """Stop the container (killing the CLI alone would leave it running)."""
        name = self._names.get(proc.pid)
        if name is not None:
            await self._docker_quiet("stop", "--time", str(int(self._kill_grace)), name)
        try:
            return await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            if name is not None:
                await self._docker_quiet("kill", name)
            _killpg(proc.pid, signal.SIGKILL)
            return await proc.wait()

    async def _docker_quiet(self, *args: str) -> None:
        with contextlib.suppress(OSError, TimeoutError):
            p = await asyncio.create_subprocess_exec(
                self._docker,
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(p.wait(), timeout=self._kill_grace + 15)


def remove_leftover_containers(binary: str, agent_name: str) -> int:
    """After an agent crash: remove containers this agent started that are still around."""
    try:
        listed = subprocess.run(  # noqa: S603 - fixed argv
            [binary, "ps", "-aq", "--filter", f"label={LABEL}={agent_name}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        ids = listed.stdout.split()
        if ids:
            subprocess.run(  # noqa: S603
                [binary, "rm", "-f", *ids], capture_output=True, timeout=60, check=False
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not clean up leftover job containers: %s", exc)
        return 0
    return len(ids)
