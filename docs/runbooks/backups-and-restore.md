# Runbook: backups, point-in-time recovery, restore tests

Targets (§24): RPO ≤ 5 min, RTO ≤ 1 h.

## What is backed up, and how
| Data | How | Where |
|---|---|---|
| PostgreSQL server: `crm`, `tfsc` (Agreements) and `keycloak` databases | pgBackRest. Every WAL file is archived as soon as it is complete, and at least once a minute while anything is written (`archive_timeout=60`). Full backup weekly, differential daily, in the `BACKUP_HOUR` hour (`BACKUP_TZ`, default 02:00 Pacific). AES-256 encrypted with `BACKUP_PASSPHRASE`, zstd-compressed. Restore to any moment of the last 14 days | `backups` volume (repo1); optionally also an off-site S3 bucket (repo2, `infra/compose/backup-offsite.env`) |
| Photos, import files, export ZIPs | bucket `crm-files` | versioning or replication at the storage provider (owner) |
| Daily audit anchors | object-lock bucket `crm-audit-anchors` (COMPLIANCE retention) | write-once storage |
| CRM data dir (`/data`) | setup key (single use) and the local anchor sink | volume snapshot |
| Agreements contracts (`/contracts`) | generated documents | daily volume snapshot / object storage sync (owner) |
| Secrets | `.env`, `backup-offsite.env` | password manager / secret store, never inside a backup |

**The passphrase.** Keep `BACKUP_PASSPHRASE` (and `PGBACKREST_REPO2_CIPHER_PASS` if you use off-site backups) in the
company password manager. Without it no backup can be read. It cannot be changed for an existing repository: to rotate,
start a new repository (new volume or bucket path), take a full backup, keep the old one until it ages out.

**Off-site.** On a single server the `backups` volume dies with the disk. For production, copy `backup-offsite.env.example`
to `backup-offsite.env`, point it at a bucket at another provider or region (versioning or object lock on, keys that
cannot delete if the provider allows it), and restart `postgres` and `backup`. WAL then goes to both repositories and
every backup is taken twice. Verified on the trial stack against an S3-compatible server over HTTPS (RustFS with a
private CA, `PGBACKREST_REPO2_S3_CA_FILE`); not tried against a commercial provider.

## The automated restore test (after every backup)
`tfsc-backup` (infra/compose/postgres) runs in the `backup` service. After each backup it:
1. writes a "before" marker row, notes the time, writes an "after" marker, waits until the WAL holding both is archived;
2. restores the latest backup plus WAL to that time into a scratch directory and starts it as a separate server
   (Unix socket only, no network, no archiving, so it can never write into the backup repository);
3. checks: the "before" marker is there and the "after" one is not (point-in-time recovery works); the same databases
   and migration revisions as the live server; `pg_amcheck` finds no corrupt table or index in any database; the audit
   chain links are intact and the newest audit entry of every workspace has the same hash as the live one;
4. records the result in `ops.backup_runs` (live `crm` database) and removes the copy.

Results: Security Center → Overview → **Backups** (last backup, last restore test, WAL archiving); metrics
`crm_backup_*`, `crm_restore_test_*`, `crm_wal_archive_*`; alerts `BackupMissing` (no good backup for 26 h),
`RestoreTestFailing`, `WalArchivingFailing` (infra/observability/alerts.yml); Grafana row "Backups".
Run one by hand: `docker compose exec backup tfsc-backup restore-test` (`-e BACKUP_TEST_REPO=2` tests the off-site copy).
Status: `docker compose exec backup tfsc-backup status`.

Measured on the compose trial stack (2026-10-21; a small database: 57 MB on disk, 181 tables, ~1,600 rows across the
three databases): full backup 3–4 s (6.4 MB stored after compression and encryption; 12 s to the off-site bucket);
restore test 8–10 s end to end (restore 2–3 s, WAL replay 1–2 s, checks 2 s). Larger databases take longer: re-measure
on production data. Negative checks: a wrong passphrase is recorded as a failed test; repository files start with
OpenSSL's `Salted__` header (encrypted, not readable as zstd); no timeline of the scratch copy ever reached the archive.
CI runs the first backup and a restore test on a fresh server on every push.

## Disaster recovery (compose)
Restore the server to the last archived WAL (disk lost or data directory damaged):
```
cd infra/compose
docker compose stop crm-api crm-bulk crm-dispatcher crm-scheduler agreements agreements-worker keycloak backup postgres
docker compose rm -f backup postgres
docker volume rm <project>_postgres-data      # only if the data directory is lost or beyond repair
docker compose run --rm --no-deps --user postgres --entrypoint tfsc-backup postgres restore
docker compose up -d
docker compose exec backup tfsc-backup backup full
```
To a moment in time (for example before a bad import), give it as the last argument:
`... --entrypoint tfsc-backup postgres restore "2026-10-21 09:30:00-07"`. The server opens on a new timeline; later
backups continue in the same repository.
On a new server: install Docker, restore the repository and `.env` (with `BACKUP_PASSPHRASE`). With an off-site copy,
put `backup-offsite.env` in place and add `-e BACKUP_RESTORE_REPO=2` to the `run` command; the local repository can
then start empty.

Drill on the trial stack (2026-10-21):
- point in time: restored to 04:24:06 UTC; the marker written before it was there, the one written after it and a
  later change to a user's name were not; the server continued on timeline 2.
- whole server lost: the data volume deleted, restored to the latest WAL: restore 2 s, WAL replay and open 2 s, CRM
  healthy 12 s later, Agreements and Keycloak serving about 20 s after the restore began (container logs). The last
  write before the stop was there.

## After any restore
1. Migrations: `crm-migrate` runs on `docker compose up`; Agreements migrates itself at start-up.
2. Security Center → Verify audit chain (must be intact; anchors after the restore point are missing, as expected).
3. Take a full backup (`docker compose exec backup tfsc-backup backup full`).
4. Rollup reconciliation runs nightly; to run it now: `python -m tfsc_crm.workers.scheduler` once, or wait for 01:00 PT.
5. Outbox: dead letters → Retry; the TFSC side de-duplicates by event id.

## Kubernetes / managed PostgreSQL
The Helm chart uses an external PostgreSQL. Use the provider's point-in-time backups (or an operator such as
CloudNativePG), encrypted, at least 14 days. The Security Center then says that the database provider takes the
backups, and the backup alerts stay silent (nothing is reported). Test a restore monthly: restore to a new instance and
run `tools/restore_drill.py` against it.

## Logical copy (independent of pgBackRest)
`python tools/restore_drill.py postgresql://USER@HOST:PORT/crm [workdir]`
dumps the database (custom format, SHA-256 recorded), restores into a new database, and checks: same Alembic
revision, same row count in every table, audit chain verifies (links, content hashes, anchors). Exit code 0 = pass.
Useful as a second copy (`pg_dump -Fc` per database, nightly, to object storage) and for moving data between servers.

Measured (local PostgreSQL 16, 2026-10-20): demo workspace, 87 tables / 2,075 rows: dump 0.75 s, restore 0.46 s, all
checks passed. Larger measurements are in docs/PERFORMANCE.md.
