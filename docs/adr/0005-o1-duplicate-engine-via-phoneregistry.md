# ADR-0005: O(1) duplicate engine via phone_registry

- Status: Accepted
- Date: 2026-10-20

## Context
Pilot duplicate check scanned 20 activity rows.

## Decision
One `phone_registry` row per (org, phone_key) holds first-seen/attempt/connect facts and the pipeline state; saves lock that row with `SELECT ... FOR UPDATE` and decide severity from it. Batch imports do one `= ANY(keys)` lookup.

## Consequences
Constant-time checks independent of history size; registry must be maintained transactionally on every save/transition.
