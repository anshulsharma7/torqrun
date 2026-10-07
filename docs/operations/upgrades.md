# Upgrades

## Procedure (Compose)

```bash
make backup                       # always: a restore point
git pull                          # or check out the release tag
make up                           # rebuilds images, runs migrations, restarts
```

`make up` runs the `migrate` service before starting the API and scheduler. Migrations are
forward-only during an upgrade. Agents keep running throughout: the API's restart is a short
control-plane outage, and agents ride it out:
- Running jobs continue.
- Results are spooled to disk and re-sent.
- The lease reaper waits a full lease period after the API returns before declaring
  anything lost.

**Order with separate hosts:**
1. Database migrations.
2. API and scheduler.
3. Agents, at your own pace, for example with `curl … /agent/install.sh | sudo bash`.
   Re-running the installer upgrades in place and keeps the agent's identity.

## Compatibility promises

- **Agents and control plane may be one release apart, in either direction.**
  - Agent-protocol messages ignore unknown fields, and every field added later has a default.
  - The control plane sends job specs without fields that are at their default value, so a
    new feature is invisible to an older agent unless a job actually uses it.
  - Such jobs are capability-gated (for example, container jobs only go to agents that
    advertise `docker`).
  - Covered by `packages/torqrun-protocol/tests/test_compat.py`.
- **Databases from older releases upgrade in place.** `tests/integration/test_upgrades.py`
  takes a database as the M3 release left it, with a finished run and a queued run in the
  old spec format. It migrates it to the current schema, then checks that:
  - the old data reads correctly;
  - the queued run is dispatched and completes;
  - first-run user setup works;
  - editing the old job creates a new version.
- **Every migration can be reverted** (`downgrade base` leaves no tables). That test also runs
  on every push.
- User-facing JSON input stays strict: a misspelled field in a job spec is an error, not
  silently ignored.

Until 1.0, minor versions may still change the HTTP API. Breaking changes are listed in the
[CHANGELOG](../../CHANGELOG.md).

## Rolling back

- **Application only (no migration in between):** check out the previous version and run
  `make up`.
- **The release added migrations:**
  1. Stop the API and scheduler.
  2. Run `docker compose run --rm migrate python -m torqrun_db.migrate downgrade <previous head>`.
     The `CHANGELOG` names each release's head revision; `python -m torqrun_db.migrate heads`
     prints the current one.
  3. Deploy the previous version.

  Downgrades drop the columns and tables the newer release added, together with their data
  (for example notification channels when rolling back past M7).
- **When in doubt, restore the pre-upgrade backup.** The restore runs migrations up to the
  version you deploy, never down.
