# ADR-0013: Pacific business clock

- Status: Accepted
- Date: 2026-10-20

## Context
Users sit in PKT and US zones; business dates must not depend on the PC clock.

## Decision
`America/Los_Angeles` via `zoneinfo` (API) and `date-fns-tz`/Intl (web). One helper per language with shared DST test vectors (2026-03-08 gap, 2026-11-01 overlap, PKT late night). Client clock offset from `/api/time`.

## Consequences
See decision.
