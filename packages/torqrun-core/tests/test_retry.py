import random

import pytest

from torqrun_core.retry import RetryPolicy, backoff_delay, should_retry
from torqrun_core.states import RunStatus

S = RunStatus


def test_no_retry_by_default() -> None:
    assert not should_retry(S.FAILED, 1, RetryPolicy())


def test_failed_attempts_retry_until_max() -> None:
    p = RetryPolicy(max_attempts=3)
    assert should_retry(S.FAILED, 1, p)
    assert should_retry(S.FAILED, 2, p)
    assert not should_retry(S.FAILED, 3, p)


@pytest.mark.parametrize("outcome", [S.SUCCEEDED, S.CANCELLED])
def test_success_and_cancel_never_retry(outcome: RunStatus) -> None:
    assert not should_retry(outcome, 1, RetryPolicy(max_attempts=5))


def test_timeout_retries_only_when_enabled() -> None:
    assert not should_retry(S.TIMED_OUT, 1, RetryPolicy(max_attempts=3))
    assert should_retry(S.TIMED_OUT, 1, RetryPolicy(max_attempts=3, retry_on_timeout=True))


def test_lost_retries_only_when_job_declares_it_safe() -> None:
    assert not should_retry(S.LOST, 1, RetryPolicy(max_attempts=3))
    assert should_retry(S.LOST, 1, RetryPolicy(max_attempts=3, interrupt_policy="retry"))


def test_backoff_grows_exponentially_within_jitter_band() -> None:
    p = RetryPolicy(backoff_seconds=10, backoff_factor=2, max_backoff_seconds=1000)
    rng = random.Random(42)
    for attempt, cap in [(1, 10), (2, 20), (3, 40), (4, 80)]:
        for _ in range(200):
            d = backoff_delay(attempt, p, rng)
            assert cap / 2 <= d <= cap


def test_backoff_is_capped() -> None:
    p = RetryPolicy(backoff_seconds=10, backoff_factor=10, max_backoff_seconds=60)
    assert all(30 <= backoff_delay(9, p, random.Random(i)) <= 60 for i in range(50))


def test_backoff_jitter_actually_spreads_values() -> None:
    p = RetryPolicy(backoff_seconds=100)
    values = {round(backoff_delay(1, p, random.Random(i)), 3) for i in range(100)}
    assert len(values) > 90
