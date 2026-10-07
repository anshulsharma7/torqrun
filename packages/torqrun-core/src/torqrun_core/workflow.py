"""Workflow (DAG) definitions: validation, layout and execution decisions. Pure, no I/O.

A definition is a mapping of task keys to tasks::

    tasks:
      extract:   {job: extract-orders}
      transform: {job: transform-orders, depends_on: [extract]}
      report:    {job: send-report, depends_on: [transform]}
      cleanup:   {job: cleanup-tmp, depends_on: [transform], trigger_rule: all_done}

Trigger rules decide whether a task runs once all its dependencies have finished:

* ``all_success`` (default): every dependency succeeded; otherwise the task is skipped.
* ``all_done``: dependencies finished in any way (cleanup, reporting).
* ``one_failed``: at least one dependency failed; otherwise skipped (alerting, compensation).

A skipped dependency counts as "not succeeded", so skips cascade through ``all_success``.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

TriggerRule = Literal["all_success", "all_done", "one_failed"]
TRIGGER_RULES: tuple[TriggerRule, ...] = ("all_success", "all_done", "one_failed")
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
MAX_TASKS = 500


class WorkflowError(ValueError):
    """The definition is invalid; the message says why, for display to the user."""


class TaskState(StrEnum):
    PENDING = "PENDING"  # not started yet (waiting for dependencies)
    ACTIVE = "ACTIVE"  # a run exists and hasn't finished (queued, running, retrying…)
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # finished unsuccessfully (failed, timed out, lost, cancelled)
    SKIPPED = "SKIPPED"  # never ran: its trigger rule was not met


DONE = frozenset({TaskState.SUCCEEDED, TaskState.FAILED, TaskState.SKIPPED})


@dataclass(frozen=True)
class Task:
    key: str
    job: str
    depends_on: tuple[str, ...] = ()
    trigger_rule: TriggerRule = "all_success"


@dataclass(frozen=True)
class Workflow:
    tasks: dict[str, Task]
    order: tuple[str, ...]  # topological
    layers: tuple[tuple[str, ...], ...] = field(default=())  # longest-path layering, for drawing

    @property
    def jobs(self) -> set[str]:
        return {t.job for t in self.tasks.values()}

    def downstream(self, key: str) -> set[str]:
        """All tasks that (transitively) depend on ``key``."""
        children: dict[str, list[str]] = {k: [] for k in self.tasks}
        for t in self.tasks.values():
            for d in t.depends_on:
                children[d].append(t.key)
        seen: set[str] = set()
        stack = list(children[key])
        while stack:
            k = stack.pop()
            if k not in seen:
                seen.add(k)
                stack.extend(children[k])
        return seen


def parse(definition: Mapping[str, Any]) -> Workflow:
    if not isinstance(definition, Mapping):
        raise WorkflowError("the definition must be a mapping with a 'tasks' key")
    unknown_top = set(definition) - {"tasks", "description"}
    if unknown_top:
        raise WorkflowError(f"unknown top-level key(s): {', '.join(sorted(unknown_top))}")
    raw = definition.get("tasks")
    if not isinstance(raw, Mapping) or not raw:
        raise WorkflowError("'tasks' must be a non-empty mapping of task name to task")
    if len(raw) > MAX_TASKS:
        raise WorkflowError(f"at most {MAX_TASKS} tasks are allowed")

    tasks: dict[str, Task] = {}
    for key, body in raw.items():
        if not isinstance(key, str) or not KEY.match(key):
            raise WorkflowError(f"task name {key!r}: use lowercase letters, digits, '-' and '_'")
        if not isinstance(body, Mapping):
            raise WorkflowError(f"task {key!r}: must be a mapping like {{job: my-job}}")
        extra = set(body) - {"job", "depends_on", "trigger_rule"}
        if extra:
            raise WorkflowError(f"task {key!r}: unknown field(s) {', '.join(sorted(extra))}")
        job = body.get("job")
        if not isinstance(job, str) or not job:
            raise WorkflowError(f"task {key!r}: 'job' (the job name to run) is required")
        deps = body.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise WorkflowError(f"task {key!r}: 'depends_on' must be a list of task names")
        rule = body.get("trigger_rule", "all_success")
        if rule not in TRIGGER_RULES:
            raise WorkflowError(
                f"task {key!r}: trigger_rule must be one of {', '.join(TRIGGER_RULES)}"
            )
        tasks[key] = Task(
            key=key, job=job, depends_on=tuple(dict.fromkeys(deps)), trigger_rule=rule
        )

    for t in tasks.values():
        for d in t.depends_on:
            if d == t.key:
                raise WorkflowError(f"task {t.key!r} depends on itself")
            if d not in tasks:
                raise WorkflowError(f"task {t.key!r} depends on unknown task {d!r}")
        if t.trigger_rule != "all_success" and not t.depends_on:
            raise WorkflowError(f"task {t.key!r}: trigger_rule {t.trigger_rule} needs depends_on")

    order = _toposort(tasks)
    depth: dict[str, int] = {}
    for k in order:
        depth[k] = 1 + max((depth[d] for d in tasks[k].depends_on), default=-1)
    layers = tuple(
        tuple(k for k in order if depth[k] == level) for level in range(max(depth.values()) + 1)
    )
    return Workflow(tasks=tasks, order=order, layers=layers)


def _toposort(tasks: dict[str, Task]) -> tuple[str, ...]:
    """Kahn's algorithm; on a cycle, report the tasks involved."""
    indegree = {k: len(t.depends_on) for k, t in tasks.items()}
    children: dict[str, list[str]] = {k: [] for k in tasks}
    for t in tasks.values():
        for d in t.depends_on:
            children[d].append(t.key)
    ready = [k for k in tasks if indegree[k] == 0]  # definition order: stable
    order: list[str] = []
    while ready:
        k = ready.pop(0)
        order.append(k)
        for c in children[k]:
            indegree[c] -= 1
            if indegree[c] == 0:
                ready.append(c)
    if len(order) != len(tasks):
        cyclic = sorted(k for k, n in indegree.items() if n > 0)
        raise WorkflowError(f"dependency cycle among tasks: {', '.join(cyclic)}")
    return tuple(order)


@dataclass(frozen=True)
class Step:
    start: list[str]  # tasks to start now (dependencies satisfied)
    skip: list[str]  # tasks that will never run (trigger rule not met)


def next_step(wf: Workflow, states: Mapping[str, TaskState]) -> Step:
    """Decide which pending tasks to start or skip, given current task states.

    Skips are resolved to a fixpoint, so a whole chain behind a failed task is skipped at once.
    """
    current = dict(states)
    start: list[str] = []
    skip: list[str] = []
    changed = True
    while changed:
        changed = False
        for key in wf.order:
            if current.get(key, TaskState.PENDING) is not TaskState.PENDING:
                continue
            task = wf.tasks[key]
            dep_states = [current.get(d, TaskState.PENDING) for d in task.depends_on]
            if not all(s in DONE for s in dep_states):
                continue
            if task.trigger_rule == "all_success":
                run = all(s is TaskState.SUCCEEDED for s in dep_states)
            elif task.trigger_rule == "all_done":
                run = True
            else:  # one_failed
                run = any(s is TaskState.FAILED for s in dep_states)
            if run:
                start.append(key)
                current[key] = TaskState.ACTIVE
            else:
                skip.append(key)
                current[key] = TaskState.SKIPPED
                changed = True  # a skip may unblock (skip) tasks after it
    return Step(start=start, skip=skip)


def outcome(states: Mapping[str, TaskState]) -> Literal["RUNNING", "SUCCEEDED", "FAILED"]:
    """Overall result: RUNNING until every task is done; FAILED if any task failed (even when a
    cleanup or alert task ran afterwards: the workflow didn't do its job)."""
    values = list(states.values())
    if any(s in (TaskState.PENDING, TaskState.ACTIVE) for s in values):
        return "RUNNING"
    return "FAILED" if any(s is TaskState.FAILED for s in values) else "SUCCEEDED"
