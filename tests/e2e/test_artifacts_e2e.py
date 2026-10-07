"""M7 acceptance: files a job writes to $TORQRUN_ARTIFACTS_DIR are uploaded by the real agent
and downloadable from the run; symlinks and directories are refused with a note in the log."""

import hashlib

from tests.e2e.conftest import Stack


def test_job_artifacts_are_uploaded_and_downloadable(stack: Stack) -> None:
    script = (
        'cd "$TORQRUN_ARTIFACTS_DIR"\n'
        "printf 'id,value\\n1,42\\n' > report.csv\n"
        "head -c 300000 /dev/urandom > blob.bin\n"
        "ln -s ../../../identity.json stolen.json\n"  # must not leak the agent's files
        "mkdir nested && echo x > nested/inner.txt\n"
        "echo done\n"
    )
    run = stack.wait_finished(
        stack.trigger(stack.create_job("makes-files", runtime="shell", script=script))["id"]
    )
    assert run["status"] == "SUCCEEDED", run

    artifacts = stack.http.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    assert [a["name"] for a in artifacts] == ["blob.bin", "report.csv"]
    report = next(a for a in artifacts if a["name"] == "report.csv")
    r = stack.http.get(f"/api/v1/runs/{run['id']}/artifacts/{report['id']}/download")
    assert r.status_code == 200 and r.content == b"id,value\n1,42\n"
    assert r.headers["content-type"] == "application/octet-stream"
    assert 'attachment; filename="report.csv"' in r.headers["content-disposition"]
    blob = next(a for a in artifacts if a["name"] == "blob.bin")
    data = stack.http.get(f"/api/v1/runs/{run['id']}/artifacts/{blob['id']}/download").content
    assert len(data) == blob["size_bytes"] == 300000
    assert hashlib.sha256(data).hexdigest() == blob["sha256"]

    system = [c["data"] for c in stack.logs(run["id"]) if c["stream"] == "system"]
    assert any("stolen.json: symbolic links are not uploaded" in line for line in system)
    assert any("nested/: directories are not uploaded" in line for line in system)
    assert any("report.csv uploaded" in line for line in system)
