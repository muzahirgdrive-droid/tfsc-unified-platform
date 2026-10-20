# @tfsc/bridge-contract — signed CRM ⇄ TFSC integration contract

- `schemas/*.json` — JSON Schemas for the handoff, account provisioning (§19.2) and event envelope (§19.3).
- `python/tfsc_bridge` and `ts/src` — signer/verifier (HMAC-SHA256 v1, compatible with the pilot) + schema validation.
- `signature-vectors.json` — shared vectors; both test suites must pass them.

Delivery semantics: at-least-once via each app's outbox; receivers deduplicate on `event_id` (inbox table);
every message carries `schema_version`; a new nonce per attempt, same idempotency key/body per business request.
