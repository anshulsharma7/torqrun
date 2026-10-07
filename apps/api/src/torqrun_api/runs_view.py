"""Mapping run rows to API models."""

from torqrun_api.schemas import RunOut
from torqrun_db.models import Run


def run_out(run: Run, job_name: str) -> RunOut:
    duration = (
        (run.finished_at - run.started_at).total_seconds()
        if run.started_at is not None and run.finished_at is not None
        else None
    )
    return RunOut(
        id=run.id,
        job_id=run.job_id,
        job_name=job_name,
        job_version=run.job_version,
        trigger=run.trigger,
        status=run.status,  # str, validated into RunStatus
        queue=run.queue,
        current_attempt=run.current_attempt,
        max_attempts=run.max_attempts,
        next_attempt_at=run.next_attempt_at,
        rerun_of=run.rerun_of,
        schedule_id=run.schedule_id,
        scheduled_for=run.scheduled_for,
        exit_code=run.exit_code,
        error_summary=run.error_summary,
        queued_at=run.queued_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_seconds=duration,
    )


IDEMPOTENCY_HEADER = "Idempotency-Key"
