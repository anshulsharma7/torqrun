"""Register and run a job programmatically with plain HTTP (a Python SDK comes later).

uv run python examples/api/run_job.py http://127.0.0.1:8080
"""

import os
import sys
import time

import httpx

TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "LOST"}
SCRIPT = """
import platform
print(f"hello from {platform.node()} running Python {platform.python_version()}")
"""


def main(base_url: str) -> int:
    token = os.environ.get("TORQRUN_TOKEN")
    if not token:
        sys.exit("set TORQRUN_TOKEN to an API token (create one under Account in the web UI)")
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(base_url=base_url, timeout=10, headers=headers) as api:
        job = (
            api.post(
                "/api/v1/jobs",
                json={
                    "name": f"sdk-example-{int(time.time())}",
                    "description": "created by examples/api/run_job.py",
                    "spec": {"runtime": "python", "script": SCRIPT, "timeout_seconds": 60},
                },
            )
            .raise_for_status()
            .json()
        )
        run = api.post(f"/api/v1/jobs/{job['id']}/runs").raise_for_status().json()
        print(f"run {run['id']} queued")
        while run["status"] not in TERMINAL:
            time.sleep(0.5)
            run = api.get(f"/api/v1/runs/{run['id']}").raise_for_status().json()
        for chunk in api.get(f"/api/v1/runs/{run['id']}/logs").json()["chunks"]:
            print(f"  [{chunk['stream']}] {chunk['data']}", end="")
        print(f"{run['status']} (exit code {run['exit_code']}, {run['duration_seconds']:.2f}s)")
        return 0 if run["status"] == "SUCCEEDED" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"))
