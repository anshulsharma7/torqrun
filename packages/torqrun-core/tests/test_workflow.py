from typing import Any

import pytest

from torqrun_core.workflow import TaskState, WorkflowError, next_step, outcome, parse

T = TaskState


def wf(**tasks: dict[str, Any]) -> Any:
    return parse({"tasks": tasks})


DIAMOND = {
    "a": {"job": "ja"},
    "b": {"job": "jb", "depends_on": ["a"]},
    "c": {"job": "jc", "depends_on": ["a"]},
    "d": {"job": "jd", "depends_on": ["b", "c"]},
}


# ---------------------------------------------------------------- validation


def test_parse_diamond_order_and_layers() -> None:
    w = parse({"tasks": DIAMOND})
    assert w.order == ("a", "b", "c", "d")
    assert w.layers == (("a",), ("b", "c"), ("d",))
    assert w.jobs == {"ja", "jb", "jc", "jd"}
    assert w.downstream("a") == {"b", "c", "d"}
    assert w.downstream("b") == {"d"}


def test_layers_use_longest_path() -> None:
    w = wf(
        a={"job": "x"},
        b={"job": "x", "depends_on": ["a"]},
        c={"job": "x", "depends_on": ["a", "b"]},
    )
    assert w.layers == (("a",), ("b",), ("c",))


def test_depends_on_accepts_single_string() -> None:
    assert wf(a={"job": "x"}, b={"job": "x", "depends_on": "a"}).tasks["b"].depends_on == ("a",)


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ({}, "non-empty mapping"),
        ({"tasks": {}}, "non-empty mapping"),
        ({"tasks": {"a": {"job": "x"}}, "steps": []}, "unknown top-level"),
        ({"tasks": {"A": {"job": "x"}}}, "lowercase"),
        ({"tasks": {"a": "x"}}, "must be a mapping"),
        ({"tasks": {"a": {}}}, "'job'"),
        ({"tasks": {"a": {"job": "x", "retries": 3}}}, "unknown field"),
        ({"tasks": {"a": {"job": "x", "depends_on": ["zzz"]}}}, "unknown task 'zzz'"),
        ({"tasks": {"a": {"job": "x", "depends_on": ["a"]}}}, "depends on itself"),
        ({"tasks": {"a": {"job": "x", "trigger_rule": "maybe"}}}, "trigger_rule must be"),
        ({"tasks": {"a": {"job": "x", "trigger_rule": "all_done"}}}, "needs depends_on"),
    ],
)
def test_invalid_definitions(definition: dict[str, Any], message: str) -> None:
    with pytest.raises(WorkflowError, match=message):
        parse(definition)


def test_cycle_is_reported_with_its_tasks() -> None:
    with pytest.raises(WorkflowError, match="cycle among tasks: a, b, c"):
        wf(
            a={"job": "x", "depends_on": ["c"]},
            b={"job": "x", "depends_on": ["a"]},
            c={"job": "x", "depends_on": ["b"]},
            d={"job": "x"},
        )


# ---------------------------------------------------------------- execution


def test_roots_start_first_then_parallel_branches() -> None:
    w = parse({"tasks": DIAMOND})
    assert next_step(w, {}).start == ["a"]
    assert next_step(w, {"a": T.ACTIVE}).start == []
    assert next_step(w, {"a": T.SUCCEEDED}).start == ["b", "c"]  # in parallel
    assert next_step(w, {"a": T.SUCCEEDED, "b": T.SUCCEEDED, "c": T.ACTIVE}).start == []
    assert next_step(w, {"a": T.SUCCEEDED, "b": T.SUCCEEDED, "c": T.SUCCEEDED}).start == ["d"]


def test_failure_skips_everything_downstream_at_once() -> None:
    w = parse({"tasks": DIAMOND})
    step = next_step(w, {"a": T.FAILED})
    assert (step.start, sorted(step.skip)) == ([], ["b", "c", "d"])


def test_failed_branch_skips_join_but_sibling_still_runs() -> None:
    w = parse({"tasks": DIAMOND})
    step = next_step(w, {"a": T.SUCCEEDED, "b": T.FAILED, "c": T.PENDING})
    assert step.start == ["c"]
    step = next_step(w, {"a": T.SUCCEEDED, "b": T.FAILED, "c": T.SUCCEEDED})
    assert step.skip == ["d"]


def test_all_done_cleanup_runs_after_failure_and_one_failed_alerts() -> None:
    w = wf(
        work={"job": "w"},
        cleanup={"job": "c", "depends_on": ["work"], "trigger_rule": "all_done"},
        alert={"job": "a", "depends_on": ["work"], "trigger_rule": "one_failed"},
        report={"job": "r", "depends_on": ["work"]},
    )
    failed = next_step(w, {"work": T.FAILED})
    assert (sorted(failed.start), failed.skip) == (["alert", "cleanup"], ["report"])
    ok = next_step(w, {"work": T.SUCCEEDED})
    assert (sorted(ok.start), ok.skip) == (["cleanup", "report"], ["alert"])


def test_skip_cascades_into_all_done_which_still_runs() -> None:
    w = wf(
        a={"job": "x"},
        b={"job": "x", "depends_on": ["a"]},
        final={"job": "x", "depends_on": ["b"], "trigger_rule": "all_done"},
    )
    step = next_step(w, {"a": T.FAILED})
    assert (step.skip, step.start) == (["b"], ["final"])


def test_outcome() -> None:
    assert outcome({"a": T.SUCCEEDED, "b": T.ACTIVE}) == "RUNNING"
    assert outcome({"a": T.SUCCEEDED, "b": T.PENDING}) == "RUNNING"
    assert outcome({"a": T.SUCCEEDED, "b": T.SKIPPED}) == "SUCCEEDED"
    assert outcome({"a": T.FAILED, "cleanup": T.SUCCEEDED}) == "FAILED"


def test_large_chain_is_fast() -> None:
    tasks = {
        f"t{i}": {"job": "x", "depends_on": [f"t{i - 1}"]} if i else {"job": "x"}
        for i in range(500)
    }
    w = parse({"tasks": tasks})
    assert len(w.layers) == 500
    step = next_step(w, {"t0": T.FAILED})
    assert len(step.skip) == 499
