# ADR-0001: Monorepo with two separately deployed sites

- Status: Accepted
- Date: 2026-10-20

## Context
The owner wants one platform but two sites (sales vs agreements) with separate data and authorization (§4.1).

## Decision
One GitHub monorepo: `apps/crm-api`, `apps/crm-web`, `apps/agreements`, `packages/bridge-contract`, `packages/refine`, `infra/`, `docs/`. Each app has its own database, migrations, container image and authorization model. Shared code lives only in `packages/` and is versioned with contract tests run by both apps. Alpha Bridge stays out of scope.

## Consequences
Atomic cross-app changes (contract + both sides) in one PR; CI must run per-app jobs.
