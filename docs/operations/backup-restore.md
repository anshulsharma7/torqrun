# Backup and restore

## What to protect

| Data | Where (Compose) | Backed up by `make backup` |
|---|---|---|
| Database: jobs, runs, logs, users, schedules, workflows, encrypted secrets | `pgdata` volume | ✅ `db.dump` (`pg_dump`, custom format) |
| Artifacts | `artifacts` volume | ✅ `artifacts.tar.gz` |
| **`TORQRUN_SECRET_KEY`** and the rest of `.env` | your host / secret manager | ❌ **separately**, on purpose |
| Agent identities | each agent's machine (`agentstate` volume for the bundled one) | ❌ re-enroll an agent if it's lost |

The secret key is deliberately left out. A backup that contains both the ciphertexts and the
key would expose every secret to anyone who obtains the backup. Store the key in a password
manager or secret store. **Without it, restored secrets and notification channels can't be
decrypted.** Everything else still restores.

## Back up

```bash
make backup                      # -> backups/torqrun-<UTC timestamp>/
```

The backup directory (mode 700) contains:
- `db.dump`
- `artifacts.tar.gz`
- `manifest.json`: Torqrun version, schema revision, and the secret key's *ID* (a fingerprint,
  not the key)
- `SHA256SUMS`

`pg_dump` takes a consistent snapshot while everything keeps running, so no downtime is
needed. Artifacts are copied after the dump, so a backup may contain a few files whose
database rows are newer than the dump. Those files are simply unreferenced; nothing breaks.

Schedule it, for example from cron on the Docker host, and copy the result off the machine:

```cron
15 2 * * *  cd /opt/torqrun && make backup && rclone copy backups/ remote:torqrun-backups
```

Prune old backups yourself. A backup's size is about that of the database (logs dominate)
plus the artifacts.

For managed or external PostgreSQL, use your provider's point-in-time recovery for the
database instead, and back up the artifacts directory and `.env`.

## Restore

```bash
make restore BACKUP=backups/torqrun-20261007T021500Z
```

What the restore does:
1. Verifies the checksums.
2. Checks that this stack's `TORQRUN_SECRET_KEY` matches the backup's key ID, and refuses
   otherwise.
3. Stops the API, scheduler, agent and web services.
4. Replaces the database and the artifacts with the backup's.
5. Runs migrations, which upgrades an older backup to the current release.
6. Starts everything.

**Restore is destructive**: the stack's current data is replaced.

Restoring into an **older** release than the backup is not supported; upgrade first. Agents
that enrolled after the backup was taken don't exist in the restored database and must be
re-enrolled. Runs that were in progress at backup time are reconciled by the lease reaper
(marked `LOST`, then retried if their policy allows).

## Drill

```bash
make backup-drill
```

The drill runs on a throwaway stack (`torqrun-drill`); your stack is untouched:
1. It creates a secret, a job that needs it and writes a 200 KB artifact, and a run.
2. It backs up.
3. It **deletes the database and artifact volumes**, then starts empty ones.
4. It restores.
5. It verifies:
   - the run count;
   - the old run;
   - the artifact, byte for byte (SHA-256);
   - that the secret is readable;
   - that a **new run of the job succeeds**, which proves the secret still decrypts with the
     key.

CI runs this drill on every push.

Result on the benchmark laptop (2026-10-07): **backup 2 s (272 KB), restore 16 s**, all checks
passed. Restore time is dominated by stopping and starting containers. `pg_restore` itself
scales with database size, about 1 minute per few GB on SSDs.

## Recovery objectives

With nightly backups, the recovery point objective is up to 24 hours of runs, logs and
changes. Back up more often, or use PostgreSQL point-in-time recovery, if that's too much.
The recovery time objective is restore time plus re-enrolling any lost agents: minutes for
small installations.
