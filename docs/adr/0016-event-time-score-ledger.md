# ADR-0016: Event-time score ledger

- Status: Accepted
- Date: 2026-10-20

## Context
Pilot quality changed retroactively when a lead advanced (CRM-03).

## Decision
`score_ledger` stores deltas when milestones happen (+10 attempt, +10 connect, +10 touch, +20 strong confirmed, +50 sign-up; reversals negative). Per-activity sum always equals the exclusive ladder value (property test). Dashboards default to event-time, with a labelled 'credited to activity date' toggle.

## Consequences
See decision.
