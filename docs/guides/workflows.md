# Workflows

A workflow chains existing **jobs** into a pipeline. Each task runs one job; a task starts when
the tasks it depends on have finished, and independent tasks run **in parallel** (across
agents and slots). Task runs are ordinary runs: same queues, retries, timeouts, logs and
cancellation as any job run.

Create one under **Workflows → New workflow**. The editor validates as you type and draws the
graph.

## Definition (YAML)

```yaml
tasks:
  extract:
    job: extract-orders
  clean:
    job: clean-orders
    depends_on: [extract]
  enrich:
    job: enrich-orders
    depends_on: [extract]          # clean and enrich run in parallel
  load:
    job: load-warehouse
    depends_on: [clean, enrich]    # waits for both
  cleanup:
    job: cleanup-tmp
    depends_on: [load]
    trigger_rule: all_done         # runs even if load failed or was skipped
  page-oncall:
    job: send-alert
    depends_on: [load]
    trigger_rule: one_failed       # runs only if load failed
```

| Field | |
|---|---|
| `job` | name of an existing job (its *current version* is used when the task starts) |
| `depends_on` | list (or single name) of tasks that must finish first |
| `trigger_rule` | `all_success` (default): all dependencies succeeded · `all_done`: dependencies finished in any way · `one_failed`: at least one dependency failed |

Task names: lowercase letters, digits, `-`, `_`. Up to 500 tasks. Cycles, unknown tasks and
unknown jobs are rejected with a message naming the problem.

## How a run behaves

* A task whose trigger rule is not met is **skipped**; skips cascade through `all_success`.
* The workflow run **succeeds** only if no task failed. A failed task makes it **fail** even if
  a cleanup or alert task ran afterwards: the pipeline didn't do its job.
* A task's retries (from its job's settings) happen before the workflow sees it as failed.
* Task runs get `TORQRUN_WORKFLOW_RUN_ID` and `TORQRUN_TASK` in their environment.
* **Cancel** cancels running tasks and skips pending ones.
* **Rerun failed** starts a new run of the *same version* that reuses tasks which succeeded and
  runs only the failed and skipped ones. **Run again** starts from scratch with the current
  version.
* Editing a workflow creates a new version; past runs keep theirs.

Workflows can be scheduled like jobs (**Schedules → New schedule**, choose the workflow).

## API

```bash
curl -X POST localhost:8080/api/v1/workflows -H 'content-type: application/json' \
  -d '{"name": "nightly-etl", "source": "tasks:\n  a: {job: extract}\n  b: {job: load, depends_on: [a]}\n"}'
curl -X POST localhost:8080/api/v1/workflows/<id>/runs -H 'Idempotency-Key: release-42'
curl localhost:8080/api/v1/workflow-runs/<run id>
curl -X POST "localhost:8080/api/v1/workflow-runs/<run id>/rerun?mode=failed"
```

Also: `POST /api/v1/workflows/validate`, `PUT /api/v1/workflows/{id}`,
`GET /api/v1/workflow-runs?workflow_id=`, `POST /api/v1/workflow-runs/{id}/cancel`.

## Why YAML, not a Python SDK (yet)

Definitions are data: the control plane can validate and store them without executing user
code, and they can be edited in the UI. A Python SDK that *emits* the same format can be added
later without changing the engine.
