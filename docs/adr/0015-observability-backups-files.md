# ADR-0015: Observability, backups, files

- Status: Accepted
- Date: 2026-10-20

## Context
Production operations.

## Decision
OpenTelemetry traces, Prometheus metrics, Grafana, JSON logs, Sentry (DSN optional). pgBackRest with PITR and nightly restore drill. S3-compatible object storage (MinIO) with presigned URLs and AV scan hook.

## Consequences
Only measured numbers go into docs/PERFORMANCE.md.
