# ADR-0004: Monthly range partitioning of activities without a hard pg_partman dependency

- Status: Accepted
- Date: 2026-10-20

## Context
8.5M activity rows/day at 50k users (§4.3). pg_partman is not available on every managed Postgres.

## Decision
`activities` is RANGE-partitioned by `business_date` monthly. A SQL function `crm_ensure_activity_partitions(months_ahead)` (run by migration and a daily job) creates partitions ahead; pg_partman can replace it where available. BRIN on `committed_at`, B-tree `(org_id, owner_id, business_date)`, `(org_id, phone_key)`. Partition keys force composite PKs `(id, business_date)`.

## Consequences
Cold partitions can be detached for archive (owner-approved retention only).
