# ADR-0017: Scale math and capacity design

- Status: Accepted
- Date: 2026-10-20

## Inputs (§4.3)
| Users | Attempts/day (×170) | Rows/year (×250 workdays) |
|---|---|---|
| 550 | 93,500 | ~23M |
| 10,000 | 1.7M | ~425M |
| 50,000 | 8.5M | ~2.1B |

Writes: 8.5M rows over ~9 h ≈ 260 rows/s average; design peak 2,000 rows/s (paste-imports of 1,000 rows).

## Decisions
- `activities` partitioned monthly (ADR-0004): at 50k users one month ≈ 180M rows ≈ 60–90 GB with indexes
  (≈ 350 B/row heap + ~150 B/row indexes, measured later in PERFORMANCE.md). Indexes stay partition-local.
- Duplicate detection: `phone_registry` O(1) by PK `(org_id, phone_key)` (ADR-0005).
- Dashboards read only rollups (`agent_day_stats`, `team_day_stats`, `org_day_stats`, `agent_hour_stats`),
  maintained incrementally from the outbox with a nightly reconciliation; Redis caches widget payloads 5–15 s.
- Leaderboards ranked/paginated server-side; charts get pre-aggregated series.
- PgBouncer transaction pooling in front of PostgreSQL; API pods are stateless.
- Beyond one primary: (1) read replicas for dashboards/reports, (2) Citus distributing `activities`,
  `phone_registry` and rollups by `org_id` (single-org deployments: by `owner_id`), keeping reference tables replicated.
- Retention: partitions older than 18 months detached/archived only after owner approval.

## Proof
No capacity claim without the k6 results in `docs/PERFORMANCE.md` (§24).
