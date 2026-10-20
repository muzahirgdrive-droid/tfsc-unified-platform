# Runbook: backups, point-in-time recovery, restore drill

Targets (§24): RPO ≤ 5 min, RTO ≤ 1 h.

## What to back up
| Data | Where | How |
|---|---|---|
| CRM + Agreements + Keycloak databases | PostgreSQL 16 | Continuous WAL archiving + nightly base backup (pgBackRest or WAL-G to object storage with encryption and object lock); nightly `pg_dump -Fc` per database as a second, logical copy |
| CRM data dir (`/data`) | volume | setup key (single use) and `audit-anchors/*.jsonl`; copy anchors to a write-once bucket daily |
| Agreements contracts (`/contracts`) | volume | daily snapshot / object storage sync |
| Secrets | KMS / secret store | never in backups of the app |

## PITR (managed PostgreSQL or pgBackRest)
- `archive_mode=on`, `archive_timeout=60` (bounds RPO to about a minute even when idle), `wal_level=replica`.
- Keep 14 days of WAL + base backups; test restores monthly.
- Restore to a time: create a new instance from the latest base backup before T, replay WAL to `recovery_target_time = T`,
  point the apps at it, run `alembic current` (CRM) and verify the audit chain (Security Center → Verify).

## Restore drill (logical copy, automated)
`python tools/restore_drill.py postgresql://USER@HOST:PORT/crm [workdir]`
dumps the database (custom format, SHA-256 recorded), restores into a new database, and checks: same Alembic
revision, same row count in every table, audit chain verifies (links, content hashes, anchors). Exit code 0 = pass.

Measured (local PostgreSQL 16, 2026-10-20): demo workspace, 87 tables / 2,075 rows: dump 0.75 s, restore 0.46 s, all
checks passed. Larger measurements are in docs/PERFORMANCE.md.

## After any restore
1. `alembic upgrade head` (CRM) — Agreements migrates itself at start-up.
2. Security Center → Verify audit chain (must be intact; anchors after the restore point will be missing, which is expected).
3. Rollup reconciliation runs nightly; to run it now: `python -m tfsc_crm.workers.scheduler` once, or wait for 01:00 PT.
4. Outbox: dead letters → Retry; the TFSC side de-duplicates by event id.
