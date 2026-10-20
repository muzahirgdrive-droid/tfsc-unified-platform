# ADR-0008: Redis 7 for pub/sub, rate limits, cache and locks; Dramatiq workers

- Status: Accepted
- Date: 2026-10-20

## Context
Realtime fan-out, sliding-window limits and background jobs are needed.

## Decision
Redis 7. Background jobs with Dramatiq (Redis broker): imports, rollups, outbox delivery, notifications, Google sync, exports. Outbox table is the durable source; Redis is never the only copy of a business event.

## Consequences
Redis loss degrades realtime only; outbox replays.
