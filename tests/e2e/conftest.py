"""Real-process stack for end-to-end tests: torqrun-api + torqrun-scheduler + torq-agent.

Timeouts are shortened (lease 10 s, ack 3 s, heartbeat 1 s) so failure scenarios resolve in
seconds. Each test gets a fresh database (see tests/conftest.py).
"""

import os
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.auth import authenticate

from torqrun_db import migrate

TOKEN = "e2e-enrollment-token"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}
BIN = Path(sys.executable).parent


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(predicate: Callable[[], Any], timeout: float, what: str) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.2)
    raise AssertionError(f"timed out waiting for {what}")


class Stack:
    def __init__(self, database_url: str, tmp: Path) -> None:
        self.tmp = tmp
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = httpx.Client(base_url=self.base, timeout=10)
        common = {
            **os.environ,
            "TORQRUN_DATABASE_URL": database_url,
            "TORQRUN_LOG_FORMAT": "console",
        }
        self.api_env = {
            **common,
            "TORQRUN_API_PORT": str(self.port),
            "TORQRUN_DEV_ENROLLMENT_TOKEN": TOKEN,
            "TORQRUN_LEASE_TTL_SECONDS": "10",
            "TORQRUN_SECRET_KEY": "e2e-secret-key-0123456789abcdef0123",
            "TORQRUN_ARTIFACTS_DIR": str(tmp / "artifacts"),
        }
        self.scheduler_env = {
            **common,
            "TORQRUN_SCHEDULER_INTERVAL_SECONDS": "0.5",
            "TORQRUN_LEASE_TTL_SECONDS": "10",
            "TORQRUN_DISPATCH_ACK_TIMEOUT_SECONDS": "3",
            "TORQRUN_SCHEDULER_HEARTBEAT_FILE": str(tmp / "scheduler.alive"),
        }
        self.agent_env = {
            **os.environ,
            "TORQRUN_AGENT_SERVER_URL": self.base,
            "TORQRUN_AGENT_ENROLLMENT_TOKEN": TOKEN,
            "TORQRUN_AGENT_STATE_DIR": str(tmp / "agent-state"),
            "TORQRUN_AGENT_NAME": "e2e-agent",
            "TORQRUN_AGENT_HEARTBEAT_INTERVAL_SECONDS": "1",
            "TORQRUN_AGENT_KILL_GRACE_SECONDS": "2",
            "TORQRUN_AGENT_JOB_PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        }
        self.procs: dict[str, subprocess.Popen[bytes]] = {}
        self.authenticated = False

    def _spawn(self, name: str, cmd: list[str], env: dict[str, str]) -> None:
        log = (self.tmp / f"{name}.log").open("ab")
        self.procs[name] = subprocess.Popen(cmd, env=env, stdout=log, stderr=subprocess.STDOUT)

    def start_api(self) -> None:
        self._spawn("api", [str(BIN / "torqrun-api")], self.api_env)
        wait_for(lambda: _ok(self.http, "/readyz"), 20, "api readiness")
        if not self.authenticated:  # sessions live in the database: survive API restarts
            authenticate(self.http)
            self.authenticated = True

    def start_scheduler(self) -> None:
        self._spawn("scheduler", [str(BIN / "torqrun-scheduler")], self.scheduler_env)

    def start_agent(self) -> None:
        self._spawn("agent", [str(BIN / "torq-agent"), "start"], self.agent_env)
        wait_for(
            lambda: any(a["connected"] for a in self.http.get("/api/v1/agents").json()),
            20,
            "agent online",
        )

    def stop(self, name: str, sig: int = signal.SIGTERM) -> None:
        proc = self.procs[name]
        proc.send_signal(sig)
        proc.wait(timeout=30)

    # API helpers ------------------------------------------------------------------------
    def create_job(self, name: str, **spec: Any) -> str:
        r = self.http.post("/api/v1/jobs", json={"name": name, "spec": spec})
        assert r.status_code == 201, r.text
        return str(r.json()["id"])

    def trigger(self, job_id: str, **headers: str) -> dict[str, Any]:
        r = self.http.post(f"/api/v1/jobs/{job_id}/runs", headers=headers)
        assert r.status_code == 201, r.text
        return dict(r.json())

    def run(self, run_id: str) -> dict[str, Any]:
        return dict(self.http.get(f"/api/v1/runs/{run_id}").json())

    def wait_status(self, run_id: str, statuses: set[str], timeout: float = 30) -> dict[str, Any]:
        return wait_for(  # type: ignore[no-any-return]
            lambda: (r := self.run(run_id))["status"] in statuses and r,
            timeout,
            f"run in {statuses}",
        )

    def wait_finished(self, run_id: str, timeout: float = 30) -> dict[str, Any]:
        return self.wait_status(run_id, TERMINAL, timeout)

    def logs(self, run_id: str, attempt: int | None = None) -> list[dict[str, Any]]:
        params = {"attempt": attempt} if attempt else {}
        return list(self.http.get(f"/api/v1/runs/{run_id}/logs", params=params).json()["chunks"])


def _ok(http: httpx.Client, path: str) -> bool:
    try:
        return http.get(path).status_code == 200
    except httpx.TransportError:
        return False


@pytest.fixture
def stack(database_url: str, tmp_path: Path) -> Iterator[Stack]:
    migrate.upgrade(database_url)
    s = Stack(database_url, tmp_path)
    try:
        s.start_api()
        s.start_scheduler()
        s.start_agent()
        yield s
    finally:
        for name in ("agent", "scheduler", "api"):
            proc = s.procs.get(name)
            if proc and proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)
        for log in sorted(tmp_path.glob("*.log")):
            sys.stdout.write(f"\n--- {log.name} ---\n{log.read_text()[-3000:]}")
