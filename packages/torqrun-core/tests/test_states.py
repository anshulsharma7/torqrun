import itertools

import pytest

from torqrun_core.states import (
    LEASED,
    TERMINAL,
    TRANSITIONS,
    InvalidTransitionError,
    RunStatus,
    can_transition,
    check_transition,
    is_terminal,
)

S = RunStatus

HAPPY_PATH = [S.QUEUED, S.DISPATCHED, S.STARTING, S.RUNNING, S.SUCCEEDED]


def test_every_status_has_an_entry() -> None:
    assert set(TRANSITIONS) == set(RunStatus)


def test_happy_path_is_allowed() -> None:
    for current, target in itertools.pairwise(HAPPY_PATH):
        check_transition(current, target)


@pytest.mark.parametrize("terminal", [S.SUCCEEDED, S.CANCELLED])
def test_final_states_have_no_exits(terminal: RunStatus) -> None:
    assert TRANSITIONS[terminal] == frozenset()


@pytest.mark.parametrize("status", [S.FAILED, S.TIMED_OUT, S.LOST])
def test_failures_can_only_move_to_retry_wait(status: RunStatus) -> None:
    assert TRANSITIONS[status] == frozenset({S.RETRY_WAIT})


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.QUEUED, S.RUNNING),  # must be dispatched and acked first
        (S.QUEUED, S.SUCCEEDED),
        (S.DISPATCHED, S.RUNNING),  # skipping the ack would break ack-before-spawn
        (S.RUNNING, S.QUEUED),  # a started process can never silently go back to the queue
        (S.SUCCEEDED, S.FAILED),
        (S.CANCELLED, S.QUEUED),
        (S.RETRY_WAIT, S.RUNNING),
    ],
)
def test_invalid_transitions_are_rejected(current: RunStatus, target: RunStatus) -> None:
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransitionError, match=f"{current} -> {target}"):
        check_transition(current, target)


def test_only_unstarted_work_can_be_requeued() -> None:
    can_requeue = {s for s in RunStatus if can_transition(s, S.QUEUED)}
    assert can_requeue == {S.DISPATCHED, S.RETRY_WAIT}


def test_terminal_and_leased_sets() -> None:
    assert {S.SUCCEEDED, S.FAILED, S.CANCELLED, S.TIMED_OUT, S.LOST} == TERMINAL
    assert all(is_terminal(s) for s in TERMINAL)
    assert not TERMINAL & LEASED
    assert not is_terminal(S.RETRY_WAIT)


def test_all_targets_are_known_statuses() -> None:
    for targets in TRANSITIONS.values():
        assert targets <= set(RunStatus)
