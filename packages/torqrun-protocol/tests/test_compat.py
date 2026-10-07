"""Wire compatibility between agents and control planes one release apart."""

import json
import uuid
from datetime import UTC, datetime

from torqrun_protocol.agent import Assignment, ClaimResponse, HeartbeatRequest
from torqrun_protocol.jobs import JobSpec

# What an M6 agent understands in an assignment's spec. A job that uses only these features
# must serialize to only these keys, or older agents would reject the whole assignment.
M6_SPEC_KEYS = {
    "runtime",
    "script",
    "args",
    "env",
    "timeout_seconds",
    "queue",
    "priority",
    "retry",
    "interrupt_policy",
    "max_concurrent",
    "secrets",
}


def assignment(spec: JobSpec) -> Assignment:
    return Assignment.model_validate(
        {
            "run_id": uuid.uuid4(),
            "attempt_id": uuid.uuid4(),
            "attempt_no": 1,
            "lease_token": "t",
            "lease_expires_at": datetime.now(UTC),
            "job_id": uuid.uuid4(),
            "job_name": "j",
            "job_version": 1,
            "spec": spec.model_dump(),
        }
    )


def test_plain_jobs_serialize_without_newer_fields() -> None:
    spec = JobSpec(runtime="shell", script="echo hi", env={"A": "1"}, timeout_seconds=30)
    wire = json.loads(assignment(spec).model_dump_json())["spec"]
    assert set(wire) <= M6_SPEC_KEYS
    assert wire == {
        "runtime": "shell",
        "script": "echo hi",
        "env": {"A": "1"},
        "timeout_seconds": 30,
    }
    # And it round-trips to the same spec.
    assert JobSpec.model_validate(wire) == spec


def test_new_features_are_sent_when_used() -> None:
    spec = JobSpec(runtime="shell", script="x", executor="docker", container={"image": "alpine"})
    wire = json.loads(assignment(spec).model_dump_json())["spec"]
    assert wire["executor"] == "docker" and wire["container"] == {"image": "alpine"}


def test_agents_ignore_fields_from_newer_control_planes() -> None:
    payload = json.loads(
        ClaimResponse(
            assignments=[assignment(JobSpec(runtime="shell", script="x"))]
        ).model_dump_json()
    )
    payload["future_top_level"] = 1
    payload["assignments"][0]["future_field"] = {"x": 1}
    payload["assignments"][0]["spec"]["future_spec_field"] = True
    parsed = ClaimResponse.model_validate(payload)
    assert parsed.assignments[0].spec.script == "x"


def test_control_plane_ignores_fields_from_newer_agents() -> None:
    hb = HeartbeatRequest.model_validate({"running": [], "free_slots": 1, "gpu_count": 2})
    assert hb.free_slots == 1


def test_user_input_stays_strict() -> None:
    import pytest

    with pytest.raises(ValueError, match="Extra inputs"):
        JobSpec.model_validate(
            {"runtime": "shell", "script": "x", "timeout": 5}
        )  # typo of timeout_seconds
