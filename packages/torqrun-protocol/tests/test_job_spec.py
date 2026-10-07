import pytest
from pydantic import ValidationError

from torqrun_protocol.jobs import MAX_SCRIPT_BYTES, JobSpec


def test_defaults() -> None:
    spec = JobSpec(runtime="python", script="print('hi')")
    assert spec.queue == "default"
    assert spec.timeout_seconds == 3600
    assert spec.args == []
    assert spec.env == {}


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        JobSpec.model_validate({"runtime": "python", "script": "x", "shell": True})


def test_rejects_unknown_runtime() -> None:
    with pytest.raises(ValidationError):
        JobSpec.model_validate({"runtime": "ruby", "script": "x"})


def test_rejects_oversized_script() -> None:
    with pytest.raises(ValidationError, match="KiB"):
        JobSpec(runtime="shell", script="x" * (MAX_SCRIPT_BYTES + 1))


@pytest.mark.parametrize("name", ["1BAD", "HAS-DASH", "has space", ""])
def test_rejects_invalid_env_names(name: str) -> None:
    with pytest.raises(ValidationError, match="invalid environment variable name"):
        JobSpec(runtime="shell", script="x", env={name: "v"})


@pytest.mark.parametrize("name", ["TORQRUN_RUN_ID", "torqrun_token"])
def test_rejects_reserved_env_prefix(name: str) -> None:
    with pytest.raises(ValidationError, match="reserved"):
        JobSpec(runtime="shell", script="x", env={name: "v"})


@pytest.mark.parametrize("queue", ["Default", "-x", "a" * 64, "has space"])
def test_rejects_invalid_queue_names(queue: str) -> None:
    with pytest.raises(ValidationError):
        JobSpec(runtime="shell", script="x", queue=queue)


@pytest.mark.parametrize("timeout", [0, 7 * 24 * 3600 + 1])
def test_timeout_bounds(timeout: int) -> None:
    with pytest.raises(ValidationError):
        JobSpec(runtime="shell", script="x", timeout_seconds=timeout)


def test_retry_and_concurrency_defaults_are_safe() -> None:
    spec = JobSpec(runtime="shell", script="x")
    assert spec.retry.max_attempts == 1
    assert spec.interrupt_policy == "fail"
    assert spec.max_concurrent is None


def test_old_specs_without_new_fields_still_load() -> None:
    stored = {
        "runtime": "shell",
        "script": "x",
        "args": [],
        "env": {},
        "timeout_seconds": 60,
        "queue": "default",
        "priority": 0,
    }
    assert JobSpec.model_validate(stored).retry.max_attempts == 1


@pytest.mark.parametrize(
    "retry", [{"max_attempts": 0}, {"max_attempts": 21}, {"backoff_seconds": 0}]
)
def test_retry_bounds(retry: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        JobSpec.model_validate({"runtime": "shell", "script": "x", "retry": retry})
