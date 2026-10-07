"""In-transaction domain events. Listeners run inside the transaction that caused the event,
so whatever they write (e.g. an outbox row) commits or rolls back together with it.

The Community Edition registers no listeners; extensions (e.g. notifications) do.
"""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from torqrun_db.models import Run, WorkflowRun

RunListener = Callable[[AsyncSession, "Run"], Awaitable[None]]
WorkflowListener = Callable[[AsyncSession, "WorkflowRun"], Awaitable[None]]

run_finished: list[RunListener] = []  # a run reached its final state (after retries)
workflow_finished: list[WorkflowListener] = []  # a workflow run ended


def on_run_finished(fn: RunListener) -> RunListener:
    if fn not in run_finished:
        run_finished.append(fn)
    return fn


def on_workflow_finished(fn: WorkflowListener) -> WorkflowListener:
    if fn not in workflow_finished:
        workflow_finished.append(fn)
    return fn


async def emit_run_finished(session: AsyncSession, run: "Run") -> None:
    for listener in run_finished:
        await listener(session, run)


async def emit_workflow_finished(session: AsyncSession, wr: "WorkflowRun") -> None:
    for listener in workflow_finished:
        await listener(session, wr)
