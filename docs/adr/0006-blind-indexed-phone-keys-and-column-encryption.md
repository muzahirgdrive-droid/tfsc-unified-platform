# ADR-0006: Blind-indexed phone keys and column encryption

- Status: Accepted
- Date: 2026-10-20

## Context
A DB dump should not reveal the phone set (§5).

## Decision
`phone_key = HMAC-SHA256(PHONE_KEY_SECRET, E.164)` (hex). Phone display, email and Meet URLs are AES-GCM encrypted at the application layer with a key from env/KMS when `CRM_FIELD_KEY` is set (hosted mode requires it).

## Consequences
Key rotation needs a re-index job (documented in runbook). Equality search only on keys.
