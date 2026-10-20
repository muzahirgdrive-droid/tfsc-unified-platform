# ADR-0009: Server-Sent Events for realtime

- Status: Accepted
- Date: 2026-10-20

## Context
Celebrations, dashboard nudges and notifications are server→client only.

## Decision
An async SSE gateway subscribed to Redis pub/sub channels per org; events carry ids for `Last-Event-ID` replay from a short Redis stream. Payloads are role-filtered before publish (agent channel never receives meeting/domain/project data).

## Consequences
Simpler than WebSockets through proxies; horizontally scalable.
