# ADR-0003: PostgreSQL 16 as the only production database; Alembic migrations

- Status: Accepted
- Date: 2026-10-20

## Context
Pilot used create_all and supported SQLite. 50k-user scale needs partitioning, RLS and real migrations.

## Decision
PostgreSQL 16 everywhere (tests included). Alembic with frozen, explicit revisions; `0001_pilot_baseline` reproduces the pilot schema exactly (verified by schema diff) so existing pilot DBs are adopted with `alembic stamp`. Every migration has a tested downgrade or a documented irreversibility note. SQLite remains only for the untouched pilot code path until cutover.

## Consequences
Local dev uses `tools/dev_services.sh` (PG 16 + Redis); CI uses service containers.
