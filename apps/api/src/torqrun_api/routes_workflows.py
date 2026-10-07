"""Workflows: DAGs of jobs, defined in YAML, versioned like jobs."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from torqrun_api.deps import SessionDep
from torqrun_api.idempotency import idempotent, validate_key
from torqrun_api.schemas import Page
from torqrun_api.security import ActorDep
from torqrun_core.states import RunStatus
from torqrun_core.workflow import WorkflowError, parse
from torqrun_db import runs as ops
from torqrun_db import workflows as wf_ops
from torqrun_db.models import Run, Workflow, WorkflowRun, WorkflowVersion

router = APIRouter(prefix="/api/v1", tags=["workflows"])

Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_.-]{0,62}$")]
MAX_SOURCE = 256 * 1024


class SourceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(max_length=MAX_SOURCE, description="YAML (or JSON) workflow definition")


class WorkflowCreate(SourceIn):
    name: Name
    description: str = Field(default="", max_length=2000)


class WorkflowUpdate(SourceIn):
    description: str = Field(default="", max_length=2000)


class TaskInfo(BaseModel):
    key: str
    job: str
    depends_on: list[str]
    trigger_rule: str


class ValidationOut(BaseModel):
    ok: bool
    error: str | None = None
    tasks: list[TaskInfo] = Field(default_factory=list)
    layers: list[list[str]] = Field(default_factory=list)


class WorkflowRunBrief(BaseModel):
    id: uuid.UUID
    status: str
    created_at: datetime
    finished_at: datetime | None


class WorkflowOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    current_version: int
    source: str
    tasks: list[TaskInfo]
    layers: list[list[str]]
    created_at: datetime
    updated_at: datetime
    last_run: WorkflowRunBrief | None


class TaskRunInfo(TaskInfo):
    state: str
    run_id: uuid.UUID | None
    run_status: RunStatus | None
    reused: bool
    note: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_seconds: float | None


class WorkflowRunOut(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    workflow_name: str
    version: int
    status: str
    trigger: str
    rerun_of: uuid.UUID | None
    schedule_id: uuid.UUID | None
    scheduled_for: datetime | None
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    counts: dict[str, int]


class WorkflowRunDetail(WorkflowRunOut):
    tasks: list[TaskRunInfo]
    layers: list[list[str]]


def _load(source: str) -> dict[str, Any]:
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        raise WorkflowError(
            f"invalid YAML{where}: {getattr(exc, 'problem', None) or exc}"
        ) from None
    if not isinstance(data, dict):
        raise WorkflowError("the definition must be a mapping with a 'tasks' key")
    return data


def _task_infos(definition: dict[str, Any]) -> tuple[list[TaskInfo], list[list[str]]]:
    w = parse(definition)
    tasks = [
        TaskInfo(
            key=k,
            job=w.tasks[k].job,
            depends_on=list(w.tasks[k].depends_on),
            trigger_rule=w.tasks[k].trigger_rule,
        )
        for k in w.order
    ]
    return tasks, [list(layer) for layer in w.layers]


async def _definition(session: SessionDep, source: str) -> dict[str, Any]:
    try:
        definition = _load(source)
        await wf_ops.validate(session, definition)
    except WorkflowError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from None
    return definition


async def _current(session: SessionDep, w: Workflow) -> WorkflowVersion:
    return (
        await session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.workflow_id == w.id, WorkflowVersion.version == w.current_version
            )
        )
    ).scalar_one()


async def _workflow_out(session: SessionDep, w: Workflow) -> WorkflowOut:
    v = await _current(session, w)
    tasks, layers = _task_infos(v.definition)
    last = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_id == w.id)
            .order_by(WorkflowRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return WorkflowOut(
        id=w.id,
        name=w.name,
        description=w.description,
        current_version=w.current_version,
        source=v.source,
        tasks=tasks,
        layers=layers,
        created_at=w.created_at,
        updated_at=w.updated_at,
        last_run=WorkflowRunBrief(
            id=last.id, status=last.status, created_at=last.created_at, finished_at=last.finished_at
        )
        if last
        else None,
    )


async def _get(session: SessionDep, workflow_id: uuid.UUID) -> Workflow:
    w = await session.get(Workflow, workflow_id)
    if w is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workflow not found")
    return w


def _counts(tasks: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tasks.values():
        out[t["state"]] = out.get(t["state"], 0) + 1
    return out


async def _run_out(session: SessionDep, wr: WorkflowRun) -> WorkflowRunOut:
    w = await session.get(Workflow, wr.workflow_id)
    duration = (wr.finished_at - wr.started_at).total_seconds() if wr.finished_at else None
    return WorkflowRunOut(
        id=wr.id,
        workflow_id=wr.workflow_id,
        workflow_name=w.name if w else "?",
        version=wr.version,
        status=wr.status,
        trigger=wr.trigger,
        rerun_of=wr.rerun_of,
        schedule_id=wr.schedule_id,
        scheduled_for=wr.scheduled_for,
        started_at=wr.started_at,
        finished_at=wr.finished_at,
        duration_seconds=duration,
        counts=_counts(wr.tasks),
    )


@router.post("/workflows/validate")
async def validate_source(body: SourceIn, session: SessionDep) -> ValidationOut:
    """Check a definition without saving it (used by the editor for live feedback)."""
    try:
        definition = _load(body.source)
        await wf_ops.validate(session, definition)
        tasks, layers = _task_infos(definition)
    except WorkflowError as exc:
        return ValidationOut(ok=False, error=str(exc))
    return ValidationOut(ok=True, tasks=tasks, layers=layers)


@router.post("/workflows", status_code=status.HTTP_201_CREATED)
async def create_workflow(body: WorkflowCreate, session: SessionDep) -> WorkflowOut:
    definition = await _definition(session, body.source)
    await session.rollback()  # end the validation read before starting the write transaction
    try:
        async with session.begin():
            w = Workflow(name=body.name, description=body.description, current_version=1)
            session.add(w)
            await session.flush()
            session.add(
                WorkflowVersion(
                    workflow_id=w.id, version=1, definition=definition, source=body.source
                )
            )
    except IntegrityError:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"a workflow named {body.name!r} already exists"
        ) from None
    return await _workflow_out(session, w)


@router.get("/workflows")
async def list_workflows(session: SessionDep) -> list[WorkflowOut]:
    rows = (await session.execute(select(Workflow).order_by(Workflow.name))).scalars().all()
    return [await _workflow_out(session, w) for w in rows]


@router.get("/workflows/{workflow_id}")
async def get_workflow(workflow_id: uuid.UUID, session: SessionDep) -> WorkflowOut:
    return await _workflow_out(session, await _get(session, workflow_id))


@router.put("/workflows/{workflow_id}")
async def update_workflow(
    workflow_id: uuid.UUID, body: WorkflowUpdate, session: SessionDep
) -> WorkflowOut:
    """A changed definition creates a new version; past runs keep the version they ran."""
    definition = await _definition(session, body.source)
    await session.rollback()
    async with session.begin():
        w = await session.get(Workflow, workflow_id, with_for_update=True)
        if w is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workflow not found")
        current = await _current(session, w)
        w.description = body.description
        if definition != current.definition or body.source != current.source:
            w.current_version += 1
            session.add(
                WorkflowVersion(
                    workflow_id=w.id,
                    version=w.current_version,
                    definition=definition,
                    source=body.source,
                )
            )
    return await _workflow_out(session, w)


@router.post(
    "/workflows/{workflow_id}/runs",
    status_code=status.HTTP_201_CREATED,
    response_model=WorkflowRunOut,
)
async def run_workflow(
    actor: ActorDep,
    workflow_id: uuid.UUID,
    session: SessionDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    async def work() -> WorkflowRunOut:
        w = await _get(session, workflow_id)
        version = await _current(session, w)
        missing = await wf_ops.missing_jobs(session, parse(version.definition))
        if missing:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"job(s) no longer exist: {', '.join(missing)}"
            )
        wr = await wf_ops.start(session, w, version, trigger="manual", actor=actor)
        return await _run_out(session, wr)

    return await idempotent(
        session,
        key=validate_key(idempotency_key),
        scope=f"workflow:{workflow_id}",
        payload={},
        work=work,
    )


@router.get("/workflow-runs")
async def list_workflow_runs(
    session: SessionDep,
    workflow_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[WorkflowRunOut]:
    stmt = select(WorkflowRun)
    if workflow_id:
        stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
    total = (await session.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars()
    return Page(
        items=[await _run_out(session, r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.get("/workflow-runs/{run_id}")
async def get_workflow_run(run_id: uuid.UUID, session: SessionDep) -> WorkflowRunDetail:
    wr = await session.get(WorkflowRun, run_id)
    if wr is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="workflow run not found")
    version = await session.get(WorkflowVersion, wr.workflow_version_id)
    assert version is not None  # noqa: S101 - FK
    infos, layers = _task_infos(version.definition)
    run_ids = [uuid.UUID(t["run_id"]) for t in wr.tasks.values() if t.get("run_id")]
    runs = (
        {r.id: r for r in (await session.execute(select(Run).where(Run.id.in_(run_ids)))).scalars()}
        if run_ids
        else {}
    )
    tasks: list[TaskRunInfo] = []
    for info in infos:
        t = wr.tasks.get(info.key, {"state": "PENDING"})
        run = runs.get(uuid.UUID(t["run_id"])) if t.get("run_id") else None
        tasks.append(
            TaskRunInfo(
                **info.model_dump(),
                state=t["state"],
                run_id=run.id if run else None,
                run_status=RunStatus(run.status) if run else None,
                reused=bool(t.get("reused_from")),
                note=t.get("note"),
                started_at=run.started_at if run else None,
                finished_at=run.finished_at if run else None,
                duration_seconds=(run.finished_at - run.started_at).total_seconds()
                if run and run.started_at and run.finished_at
                else None,
            )
        )
    base = await _run_out(session, wr)
    return WorkflowRunDetail(**base.model_dump(), tasks=tasks, layers=layers)


@router.post("/workflow-runs/{run_id}/cancel")
async def cancel_workflow_run(
    actor: ActorDep, run_id: uuid.UUID, session: SessionDep
) -> WorkflowRunOut:
    """Cancel the workflow: running tasks are cancelled, pending ones skipped."""
    async with session.begin():
        try:
            wr = await wf_ops.cancel(session, run_id, actor=actor)
        except LookupError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            ) from None
        except ops.RunFinishedError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
        return await _run_out(session, wr)


@router.post("/workflow-runs/{run_id}/rerun", status_code=status.HTTP_201_CREATED)
async def rerun_workflow(
    actor: ActorDep,
    run_id: uuid.UUID,
    session: SessionDep,
    mode: Literal["failed", "all"] = Query(
        default="failed", description="'failed': reuse tasks that succeeded"
    ),
) -> WorkflowRunOut:
    async with session.begin():
        try:
            wr = await wf_ops.rerun(session, run_id, mode=mode, actor=actor)
        except LookupError:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="workflow run not found"
            ) from None
        except wf_ops.RerunError as exc:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from None
        return await _run_out(session, wr)
