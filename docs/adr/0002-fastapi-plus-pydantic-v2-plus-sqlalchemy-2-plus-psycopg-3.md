# ADR-0002: FastAPI + Pydantic v2 + SQLAlchemy 2 + psycopg 3

- Status: Accepted
- Date: 2026-10-20

## Context
The pilot used FastAPI with untyped dict payloads.

## Decision
v2 endpoints declare Pydantic v2 request/response models for every route (no dict payloads), SQLAlchemy 2.x Core/ORM with psycopg 3. OpenAPI generated from the models is the API contract.

## Consequences
Typed TS client can be generated (ADR-0010); response models double as field allow-lists, preventing leaks of meeting/domain/project fields to agents.
