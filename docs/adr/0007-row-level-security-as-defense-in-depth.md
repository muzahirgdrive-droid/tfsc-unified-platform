# ADR-0007: Row-Level Security as defense in depth

- Status: Accepted
- Date: 2026-10-20

## Context
API scope checks are primary but a single bug must not leak another org's data.

## Decision
RLS policies on org-scoped tables keyed on `current_setting('app.org_id')`; the API sets `app.org_id`/`app.actor_id` per transaction (`SET LOCAL`). The migration role owns tables; the app role is subject to RLS (`FORCE ROW LEVEL SECURITY`).

## Consequences
Background jobs must set the org explicitly; tests run as the restricted role.
