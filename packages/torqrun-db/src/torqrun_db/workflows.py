"""Workflow persistence and execution.

A workflow run keeps a small per-task state map; task runs are ordinary runs (same queue,
retries, agents, logs) tagged with ``workflow_run_id`` and ``task_key``. ``advance()`` reads
the task runs' statuses, asks :func:`torqrun_core.workflow.next_step` what to do, starts and
skips tasks, and finalises the workflow. It runs under the workflow run's row lock, so the
scheduler's advancer and API calls (start, cancel) never act on a stale view.
"""

import contextlib
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from torqrun_core.states import RunStatus, is_terminal
from torqrun_core.workflow import TaskState, Workflow, WorkflowError, next_step, outcome, parse
from torqrun_db import events
from torqrun_db import runs as ops
from torqrun_db.models import Job, JobVersion, Run, WorkflowRun, WorkflowVersion
from torqrun_db.models import Workflow as WorkflowRow
from torqrun_db.runs import utcnow

T = TaskState


class RerunError(Exception):
    pass


async def missing_jobs(session: AsyncSession, wf: Workflow) -> list[str]:
    existing = set((await session.execute(select(Job.name).where(Job.name.in_(wf.jobs)))).scalars())
    return sorted(wf.jobs - existing)


async def validate(session: AsyncSession, definition: dict[str, Any]) -> Workflow:
    wf = parse(definition)
    missing = await missing_jobs(session, wf)
    if missing:
        raise WorkflowError(f"unknown job(s): {', '.join(missing)} (create them first)")
    return wf


def _task_state(run_status: str | None) -> TaskState:
    if run_status is None:
        return T.PENDING
    status = RunStatus(run_status)
    if status is RunStatus.SUCCEEDED:
        return T.SUCCEEDED
    if is_terminal(status):
        return T.FAILED  # failed, timed out, lost or cancelled (after any retries)
    return T.ACTIVE  # queued, running, waiting to retry…


async def _states(session: AsyncSession, wr: WorkflowRun) -> dict[str, TaskState]:
    run_ids = [
        uuid.UUID(t["run_id"])
        for t in wr.tasks.values()
        if t.get("run_id") and not t.get("reused_from")
    ]
    statuses: dict[str, str] = {}
    if run_ids:
        statuses = {
            str(rid): st
            for rid, st in (
                await session.execute(select(Run.id, Run.status).where(Run.id.in_(run_ids)))
            ).all()
        }
    out: dict[str, TaskState] = {}
    for key, t in wr.tasks.items():
        if t["state"] == T.SKIPPED:
            out[key] = T.SKIPPED
        elif t.get("reused_from"):
            out[key] = T.SUCCEEDED  # carried over from the run being rerun
        elif t.get("run_id"):
            out[key] = _task_state(statuses.get(t["run_id"]))
        elif t["state"] == T.FAILED:
            out[key] = T.FAILED  # failed before a run could be created (job deleted)
        else:
            out[key] = T.PENDING
    return out


async def start(
    session: AsyncSession,
    workflow: WorkflowRow,
    version: WorkflowVersion,
    *,
    trigger: str,
    actor: str,
    schedule_id: uuid.UUID | None = None,
    scheduled_for: datetime | None = None,
    rerun_of: uuid.UUID | None = None,
    reuse: dict[str, str] | None = None,
) -> WorkflowRun:
    """Create a workflow run and start its root tasks (in the caller's transaction)."""
    wf = parse(version.definition)
    reuse = reuse or {}
    tasks = {
        key: (
            {
                "state": T.SUCCEEDED,
                "run_id": reuse[key],
                "reused_from": reuse[key],
                "note": "reused from previous run",
            }
            if key in reuse
            else {"state": T.PENDING, "run_id": None, "reused_from": None, "note": None}
        )
        for key in wf.order
    }
    wr = WorkflowRun(
        workflow_id=workflow.id,
        workflow_version_id=version.id,
        version=version.version,
        status="RUNNING",
        trigger=trigger,
        rerun_of=rerun_of,
        schedule_id=schedule_id,
        scheduled_for=scheduled_for,
        tasks=tasks,
    )
    session.add(wr)
    await session.flush()
    await advance(session, wr, actor=actor)
    return wr


async def advance(session: AsyncSession, wr: WorkflowRun, *, actor: str = "system") -> bool:
    """Start/skip tasks whose dependencies are done and finalise the run. Returns True if
    anything changed. Caller holds the row lock (or just created the row)."""
    if wr.status != "RUNNING":
        return False
    version = await session.get(WorkflowVersion, wr.workflow_version_id)
    workflow = await session.get(WorkflowRow, wr.workflow_id)
    if version is None or workflow is None:  # pragma: no cover - guaranteed by foreign keys
        raise LookupError(str(wr.id))
    wf = parse(version.definition)
    states = await _states(session, wr)
    step = next_step(wf, states)
    tasks = {k: dict(v) for k, v in wr.tasks.items()}
    changed = False

    for key in step.skip:
        tasks[key]["state"] = T.SKIPPED
        deps = ", ".join(wf.tasks[key].depends_on)
        tasks[key]["note"] = f"skipped: trigger rule {wf.tasks[key].trigger_rule} not met by {deps}"
        states[key] = T.SKIPPED
        changed = True

    for key in step.start:
        task = wf.tasks[key]
        job = (await session.execute(select(Job).where(Job.name == task.job))).scalar_one_or_none()
        if job is None:
            tasks[key]["state"] = T.FAILED
            tasks[key]["note"] = f"job {task.job!r} no longer exists"
            states[key] = T.FAILED
            changed = True
            continue
        jv = (
            await session.execute(
                select(JobVersion).where(
                    JobVersion.job_id == job.id, JobVersion.version == job.current_version
                )
            )
        ).scalar_one()
        run = await ops.create_run(
            session,
            job,
            jv,
            trigger="workflow",
            actor=f"workflow:{workflow.name}",
            reason=f"workflow {workflow.name!r} task {key!r}",
        )
        run.workflow_run_id = wr.id
        run.task_key = key
        tasks[key].update(state=T.ACTIVE, run_id=str(run.id))
        states[key] = T.ACTIVE
        changed = True

    # Refresh terminal task states into the stored map (for display and later decisions).
    for key, st in states.items():
        if tasks[key]["state"] != st:
            tasks[key]["state"] = st
            changed = True

    result = outcome(states)
    if result != "RUNNING":
        wr.status = result
        wr.finished_at = utcnow()
        changed = True
    if changed:
        wr.tasks = tasks
        await session.flush()
    if result != "RUNNING":
        await events.emit_workflow_finished(session, wr)
    return changed


async def advance_running(session: AsyncSession, *, limit: int = 100) -> int:
    """Scheduler step: advance every running workflow run (each under its row lock)."""
    running = (
        (
            await session.execute(
                select(WorkflowRun)
                .where(WorkflowRun.status == "RUNNING")
                .order_by(WorkflowRun.updated_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    return sum([await advance(session, wr) for wr in running])


async def cancel(session: AsyncSession, wr_id: uuid.UUID, *, actor: str) -> WorkflowRun:
    wr = await session.get(WorkflowRun, wr_id, with_for_update=True)
    if wr is None:
        raise LookupError(str(wr_id))
    if wr.status != "RUNNING":
        raise ops.RunFinishedError(f"workflow run already {wr.status.lower()}")
    tasks = {k: dict(v) for k, v in wr.tasks.items()}
    for key, t in tasks.items():
        if t["state"] == T.PENDING:
            t["state"] = T.SKIPPED
            t["note"] = "skipped: workflow cancelled"
        elif t["state"] == T.ACTIVE and t.get("run_id"):
            with contextlib.suppress(ops.RunFinishedError):  # it may have finished meanwhile
                await ops.cancel_run(session, uuid.UUID(t["run_id"]), actor=actor)
            tasks[key] = t
    wr.tasks = tasks
    wr.status = "CANCELLED"
    wr.finished_at = utcnow()
    await session.flush()
    return wr


async def rerun(session: AsyncSession, wr_id: uuid.UUID, *, mode: str, actor: str) -> WorkflowRun:
    """``mode="all"``: new run of the workflow's current version. ``mode="failed"``: new run of
    the same version that reuses tasks which succeeded, re-running only failed/skipped ones."""
    old = await session.get(WorkflowRun, wr_id)
    if old is None:
        raise LookupError(str(wr_id))
    workflow = await session.get(WorkflowRow, old.workflow_id)
    assert workflow is not None  # noqa: S101 - FK
    if mode == "failed":
        if old.status not in ("FAILED", "CANCELLED"):
            raise RerunError("only failed or cancelled workflow runs can rerun their failed tasks")
        version = await session.get(WorkflowVersion, old.workflow_version_id)
        reuse = {
            k: t["run_id"]
            for k, t in old.tasks.items()
            if t["state"] == T.SUCCEEDED and t.get("run_id")
        }
    else:
        version = (
            await session.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.version == workflow.current_version,
                )
            )
        ).scalar_one()
        reuse = {}
    assert version is not None  # noqa: S101
    return await start(
        session, workflow, version, trigger="rerun", actor=actor, rerun_of=old.id, reuse=reuse
    )
