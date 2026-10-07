# Schedules

A schedule starts runs of a job automatically, on a **cron expression** or a **fixed
interval**, in any IANA time zone. Create one from the job page (**Schedules → Add schedule**)
or **Schedules → New schedule**; the editor previews the next five run times.

## Cron syntax

Five fields: `minute hour day-of-month month day-of-week`.

| Expression | Meaning |
|---|---|
| `*/15 * * * *` | every 15 minutes |
| `0 2 * * *` | every day at 02:00 |
| `30 9 * * 1-5` | weekdays at 09:30 |
| `0 8 * * mon` | Mondays at 08:00 |
| `0 0 1,15 * *` | the 1st and 15th at midnight |
| `@hourly` `@daily` `@weekly` `@monthly` `@yearly` | shortcuts |

Supported: `*`, ranges `a-b`, steps `*/n` and `a-b/n`, lists `a,b`, month and weekday names,
`7` as Sunday. When both day-of-month and day-of-week are set, a day matches if **either** does
(classic cron behaviour). Interval schedules run every *N* seconds (minimum 10), keeping their
phase from when the schedule was created.

## Time zones and daylight saving time

Cron fields are evaluated in the schedule's time zone:

* A time that doesn't exist on the day clocks go forward (e.g. `02:30` in Europe/Berlin on the
  last Sunday of March) runs at the first moment after the jump (`03:00`).
* A time that happens twice when clocks go back runs **once**, at its first occurrence.

## When the scheduler was down

| Policy | Behaviour after downtime |
|---|---|
| **Run once to catch up** (default) | one run for the most recent missed slot |
| **Run each missed slot** | one run per missed slot, up to *max catch-up* (default 5), newest first |
| **Skip them** | missed slots are dropped; the next on-time slot runs |

A slot counts as missed when it's more than the grace period (default 60 s) late. Skipped
slots are counted on the schedule (`skipped_count`, `last_skip_reason`).

## Overlap

**If the previous run is still going → Skip this run** prevents piling up runs of a slow job.
The default starts the new run anyway (job-level *Max concurrent runs* still applies).

## Pause, resume, edit

Pausing stops new runs; running ones are unaffected. Resuming and editing take effect **from
now**: slots that passed while paused are not run.

## Guarantees

Each slot produces at most one run, even with several scheduler replicas or after a restart:
schedules are claimed with `FOR UPDATE SKIP LOCKED` and runs are unique on
`(schedule_id, scheduled_for)`.

## API

```bash
curl -X POST localhost:8080/api/v1/schedules -H 'content-type: application/json' -d '{
  "name": "nightly", "job_id": "<job id>", "cron": "0 2 * * *", "timezone": "Europe/Berlin"
}'
curl -X POST localhost:8080/api/v1/schedules/preview -H 'content-type: application/json' \
  -d '{"cron": "30 2 * * *", "timezone": "Europe/Berlin", "count": 5}'
```

Also: `GET /api/v1/schedules[?job_id=]`, `PUT /api/v1/schedules/{id}`,
`POST /api/v1/schedules/{id}/pause|resume`, `DELETE /api/v1/schedules/{id}`,
`GET /api/v1/schedules/timezones`.
