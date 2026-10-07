"""Container executor against a real Docker daemon (skipped when none is available)."""

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from torqrun_agent.docker_executor import (
    DockerExecutor,
    container_name,
    docker_available,
    find_docker,
    remove_leftover_containers,
)
from torqrun_agent.executor import ExecutionResult
from torqrun_protocol.jobs import JobSpec

IMAGE = os.environ.get("TORQRUN_TEST_IMAGE", "python:3.12-slim-bookworm")
DOCKER = find_docker()


def _image_present() -> bool:
    if not (DOCKER and docker_available(DOCKER)):
        return False
    r = subprocess.run([DOCKER, "image", "inspect", IMAGE], capture_output=True, check=False)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(
    not _image_present(), reason=f"needs a Docker daemon and the {IMAGE} image (docker pull it)"
)
AGENT = "pytest-docker-agent"


def spec(script: str, runtime: str = "shell", **container: object) -> JobSpec:
    return JobSpec.model_validate(
        {
            "runtime": runtime,
            "script": script,
            "timeout_seconds": 60,
            "executor": "docker",
            "container": {"image": IMAGE, "pull": "never", **container},
            "env": {"GREETING": "hello"},
        }
    )


async def run(
    s: JobSpec,
    tmp_path: Path,
    *,
    cancel: asyncio.Event | None = None,
    extra: dict[str, str] | None = None,
) -> tuple[ExecutionResult, list[tuple[str, str]]]:
    assert DOCKER
    ex = DockerExecutor(binary=DOCKER, agent_name=AGENT, kill_grace_seconds=2)
    workspace = tmp_path / "0199aaaa-test-attempt"
    workspace.mkdir(mode=0o700)
    out: list[tuple[str, str]] = []

    async def spawned(pid: int) -> None:
        pass

    result = await ex.run(
        s,
        workspace,
        extra_env=extra or {"TORQRUN_RUN_ID": "r1"},
        on_output=lambda stream, text: out.append((stream, text)),
        on_spawned=spawned,
        cancel=cancel,
    )
    return result, out


def stdout(out: list[tuple[str, str]]) -> str:
    return "".join(t for s, t in out if s == "stdout")


async def test_python_job_runs_in_a_container_as_the_agent_user(tmp_path: Path) -> None:
    result, out = await run(
        spec(
            "import os, socket\n"
            "print(os.getuid(), os.getgid())\n"
            "print(os.environ['GREETING'], os.environ['TORQRUN_RUN_ID'], os.getcwd())\n"
            "open('result.txt', 'w').write('done')\n",
            runtime="python",
        ),
        tmp_path,
    )
    assert result.outcome == "succeeded", out
    lines = stdout(out).splitlines()
    assert lines[0] == f"{os.getuid()} {os.getgid()}"
    assert lines[1] == "hello r1 /workspace"
    # The workspace is shared with the host (artifacts, etc.).
    assert (tmp_path / "0199aaaa-test-attempt" / "result.txt").read_text() == "done"


async def test_secret_values_stay_out_of_the_command_line(tmp_path: Path) -> None:
    assert DOCKER
    ex = DockerExecutor(binary=DOCKER, agent_name=AGENT, kill_grace_seconds=2)
    s = spec("echo $DB_PASSWORD")
    argv, env = ex.launch(s, tmp_path, tmp_path / "main.sh", {"DB_PASSWORD": "s3cret-value"})
    assert "s3cret-value" not in " ".join(argv)
    assert "DB_PASSWORD" in argv and env["DB_PASSWORD"] == "s3cret-value"
    assert {"--cap-drop", "ALL", "no-new-privileges", "--rm"} <= set(argv)
    result, out = await run(s, tmp_path, extra={"DB_PASSWORD": "s3cret-value"})
    assert result.outcome == "succeeded" and stdout(out) == "s3cret-value\n"


async def test_exit_code_and_dropped_capabilities(tmp_path: Path) -> None:
    result, out = await run(
        spec("grep -c '^CapEff:[[:space:]]*0*$' /proc/self/status; exit 7", memory_mb=64),
        tmp_path,
    )
    assert result.outcome == "failed" and result.exit_code == 7
    assert stdout(out).strip() == "1"  # no effective Linux capabilities


async def test_network_none_cuts_the_job_off(tmp_path: Path) -> None:
    script = "import socket\nsocket.create_connection(('1.1.1.1', 53), timeout=3)\n"
    result, out = await run(spec(script, runtime="python", network="none"), tmp_path)
    assert result.outcome == "failed"
    assert "Network is unreachable" in "".join(t for _, t in out)


async def test_cancel_stops_and_removes_the_container(tmp_path: Path) -> None:
    cancel = asyncio.Event()
    task = asyncio.create_task(run(spec("echo started; sleep 300"), tmp_path, cancel=cancel))
    name = container_name(tmp_path / "0199aaaa-test-attempt")
    for _ in range(100):
        if _container_exists(name):
            break
        await asyncio.sleep(0.1)
    assert _container_exists(name)
    cancel.set()
    result, _ = await asyncio.wait_for(task, timeout=30)
    assert result.outcome == "cancelled"
    await asyncio.sleep(0.5)
    assert not _container_exists(name)


async def test_missing_image_fails_clearly(tmp_path: Path) -> None:
    s = JobSpec.model_validate(
        {
            "runtime": "shell",
            "script": "true",
            "executor": "docker",
            "container": {"image": "torqrun-test/does-not-exist:never", "pull": "never"},
        }
    )
    result, _ = await run(s, tmp_path)
    assert result.outcome == "failed" and result.exit_code == 125
    assert "No such image" in (result.error_summary or "")


def test_leftover_containers_are_removed() -> None:
    assert DOCKER
    subprocess.run(
        [DOCKER, "run", "-d", "--label", f"torqrun.agent={AGENT}-crash", IMAGE, "sleep", "300"],
        check=True,
        capture_output=True,
    )
    assert remove_leftover_containers(DOCKER, f"{AGENT}-crash") == 1
    assert remove_leftover_containers(DOCKER, f"{AGENT}-crash") == 0


def _container_exists(name: str) -> bool:
    assert DOCKER
    r = subprocess.run(
        [DOCKER, "ps", "-aq", "--filter", f"name=^{name}$"], capture_output=True, text=True
    )
    return bool(r.stdout.strip())
