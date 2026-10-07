"""Run state machine.

A *run* is one request to execute a job version; an *attempt* is one physical execution of it
on one agent. Attempt statuses use the same vocabulary as runs, except ``RETRY_WAIT``, which
only applies to runs (it is the gap between two attempts).

The transition table is the single source of truth: the database layer applies transitions
as compare-and-set updates and rejects anything not listed here. See
docs/architecture/ARCHITECTURE.md §7 for the reasoning behind each edge.
"""

from enum import StrEnum


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    DISPATCHED = "DISPATCHED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    LOST = "LOST"


S = RunStatus

TERMINAL: frozenset[RunStatus] = frozenset(
    {S.SUCCEEDED, S.FAILED, S.CANCELLED, S.TIMED_OUT, S.LOST}
)

# Statuses in which an agent holds a lease on the attempt.
LEASED: frozenset[RunStatus] = frozenset({S.DISPATCHED, S.STARTING, S.RUNNING, S.CANCEL_REQUESTED})

TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    S.QUEUED: frozenset({S.DISPATCHED, S.CANCELLED}),
    # DISPATCHED -> QUEUED is safe only because agents never spawn before a successful ack.
    S.DISPATCHED: frozenset({S.STARTING, S.QUEUED, S.CANCELLED}),
    S.STARTING: frozenset({S.RUNNING, S.FAILED, S.CANCEL_REQUESTED, S.LOST}),
    S.RUNNING: frozenset({S.SUCCEEDED, S.FAILED, S.TIMED_OUT, S.CANCEL_REQUESTED, S.LOST}),
    S.CANCEL_REQUESTED: frozenset({S.CANCELLED, S.SUCCEEDED, S.FAILED, S.TIMED_OUT}),
    # Terminal outcomes may be followed by a retry; the run (not the attempt) waits.
    S.FAILED: frozenset({S.RETRY_WAIT}),
    S.TIMED_OUT: frozenset({S.RETRY_WAIT}),
    S.LOST: frozenset({S.RETRY_WAIT}),
    S.RETRY_WAIT: frozenset({S.QUEUED, S.CANCELLED}),
    S.SUCCEEDED: frozenset(),
    S.CANCELLED: frozenset(),
}


class InvalidTransitionError(ValueError):
    def __init__(self, current: RunStatus, target: RunStatus) -> None:
        super().__init__(f"invalid transition {current} -> {target}")
        self.current = current
        self.target = target


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    return target in TRANSITIONS[current]


def check_transition(current: RunStatus, target: RunStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransitionError(current, target)


def is_terminal(status: RunStatus) -> bool:
    return status in TERMINAL
