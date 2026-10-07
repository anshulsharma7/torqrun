"""Retry decisions and backoff. Pure functions: no clocks, no randomness unless injected.

Delivery semantics (docs/architecture/ARCHITECTURE.md §7.4): an attempt that *failed* or
*timed out* ran to a known end, so retrying it is the user's policy choice. An attempt that was
*lost* (agent vanished mid-run) may have had partial side effects; it is retried only when the
job declares itself safe to re-run (``interrupt_policy="retry"``).
"""

import random
from dataclasses import dataclass
from typing import Literal

from torqrun_core.states import RunStatus

InterruptPolicy = Literal["fail", "retry"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 10.0
    backoff_factor: float = 2.0
    max_backoff_seconds: float = 600.0
    retry_on_timeout: bool = False
    interrupt_policy: InterruptPolicy = "fail"


def should_retry(outcome: RunStatus, attempt_no: int, policy: RetryPolicy) -> bool:
    """Whether a finished attempt ``attempt_no`` (1-based) gets another try."""
    if attempt_no >= policy.max_attempts:
        return False
    if outcome is RunStatus.FAILED:
        return True
    if outcome is RunStatus.TIMED_OUT:
        return policy.retry_on_timeout
    if outcome is RunStatus.LOST:
        return policy.interrupt_policy == "retry"
    return False  # SUCCEEDED / CANCELLED never retry


def backoff_delay(attempt_no: int, policy: RetryPolicy, rng: random.Random | None = None) -> float:
    """Delay before the attempt that follows ``attempt_no``: exponential with full jitter.

    ``cap = min(max, base * factor^(attempt_no-1))`` and the delay is uniform in
    ``[cap/2, cap]`` ("equal jitter"): spread out so a burst of failures doesn't retry in
    lockstep, but never collapsing to ~0 the way pure full jitter can.
    """
    cap = min(
        policy.max_backoff_seconds,
        policy.backoff_seconds * policy.backoff_factor ** (attempt_no - 1),
    )
    r = (rng or random).random()
    return cap / 2 + r * cap / 2
