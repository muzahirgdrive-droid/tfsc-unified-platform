# Threat model (STRIDE) — TFSC Sales CRM v2 + TFSC Agreements

Scope: the CRM API + web app, the Agreements portal, the signed bridge between them, Keycloak SSO, PostgreSQL,
Redis, the background workers, and the operators who run them. Assets, in order of harm if lost: provider contact
data (phones, emails), lead ownership and incentive attribution, signed agreements and signature images, audit
history, credentials/secrets.

Trust boundaries: browser ↔ CRM/Agreements (TLS, session cookie); CRM ↔ Agreements (HMAC-signed bridge);
apps ↔ Keycloak (OIDC); apps ↔ PostgreSQL/Redis (private network); operators ↔ servers.

Each row names the control and where it is tested. "Not covered" lists what a deployment must still do.

| # | Threat (STRIDE) | Control | Evidence |
|---|---|---|---|
| S1 | Spoofed user / stolen password | Keycloak SSO; TOTP mandatory for Supervisor+ (realm flow + CRM checks `amr`/`acr`); brute-force lockout; local password login only for the Super Admin break-glass when `CRM_ALLOW_LOCAL_LOGIN=0` | `tests_v2/test_oidc.py` (13), `apps/crm-web/e2e/sso.mjs` against real Keycloak 26 |
| S2 | Forged ID token / mix-up / replay | JWKS signature check, issuer, audience, azp, expiry, nonce; PKCE S256; state in HMAC-signed short-lived cookie | `test_oidc.py` (forged key, wrong aud/iss, expired, nonce replay, state mismatch, missing cookie) |
| S3 | Session theft | HttpOnly + SameSite=Lax (+Secure when hosted) cookie, hashed at rest, idle 30 min / absolute 12 h, remote end from Security Center, offboarding kills sessions | `test_security_center.py`, `test_security.py` |
| S4 | Spoofed bridge calls (CRM ↔ TFSC) | HMAC v1 over timestamp, nonce, method, path, body hash; 5-minute window; nonce replay store; idempotency keys | `packages/bridge-contract` vectors (Py + TS), `test_crm_v2.py`, E2E acceptance 12–13 |
| T1 | Tampering with history | Hash-chained `audit_log` (append-only for the app role), content hashes re-verified, daily anchors (insert-only table + append-only file); score ledger and revisions append-only | `test_security_center.py` (edit + re-hash detected; app role cannot UPDATE/DELETE) |
| T2 | Cross-site request forgery | Origin check + CSRF header on every mutation; SameSite cookies | `test_csrf_required_for_mutations`, `test_cross_origin_rejected` |
| T3 | CSV/formula injection in exports | Cells starting `= + - @ tab CR` prefixed with `'` | `test_export_requires_approval_...` |
| T4 | Malicious spreadsheet upload (XXE, zip bomb, formulas) | `defusedxml` with openpyxl, size caps (20/60 MB), row caps, read-only parsing, formulas never executed or trusted, cell text never interpreted as instructions | `test_file_import.py`, `test_legacy_import.py` |
| R1 | Repudiation of edits, approvals, exports | Every privileged action audited with actor, IP, before/after; exports: requested → approved → password revealed → downloaded | `test_security_center.py` (export audit trail) |
| I1 | Agents seeing other agents' data | Server-side scope on every query (`owner_filter`), PostgreSQL RLS by org, response models as allow-lists, pre-dial check returns a state only | `test_security.py` authz matrix (every endpoint × role), leak scan, RLS test |
| I2 | Meeting/domain/project leakage to agents | Agent DTOs, SSE events and notifications never carry them | `test_agent_responses_never_contain_routing_or_meeting_fields`, SSE tests |
| I3 | Bulk scraping by an insider | Read budgets (>500 distinct prospects/hour → throttle + red flag), pagination caps, copy limit in the grid, watermark | `test_api_capture.py` (acceptance 17) |
| I4 | Data at rest / backups | Field encryption (AES-GCM) for phones/emails, blind-index keys, encrypted exports (AES-256 ZIP), private signature images never in git or images | `.gitignore`, `.dockerignore`, `test_export...` |
| I5 | Secrets in repo/images | Env/KMS only; build CA passed as BuildKit secret; `.env` ignored | repository review, `infra/compose/.env.example` |
| D1 | Login / API abuse | Rate limits (login per IP/user, precheck, batch, uploads), body size limits | `ratelimit` tests, boundary middleware |
| D2 | Queue or delivery failure | Outbox with retries, dead letters visible + retryable in Security Center; idempotent consumers | `test_realtime_meetings.py`, `test_crm_v2.py` |
| E1 | Privilege escalation | Role grants capped below the granter (Super Admin excepted), capability checks before body handling, Agreements `admin` only by Super Admin | `test_role_grant_limits`, authz matrix |
| E2 | Host header / DNS rebinding | Host allow-list on both apps; internal service calls keep the public Host | boundary middleware, compose trial |

## Not covered by the code (deployment responsibilities)

- TLS termination and HSTS preload (Caddy/Ingress); keep Keycloak admin console off the public internet.
- Write-once (object-lock) storage for audit anchors and backups; KMS for `CRM_FIELD_KEY` / bridge secret rotation.
- Off-site copy of the encrypted backups and safekeeping of the backup passphrase; on Kubernetes / managed PostgreSQL,
  the provider's PITR backups (compose: pgBackRest with WAL archiving and a daily automated restore test,
  `docs/runbooks/backups-and-restore.md`).
- Endpoint security on agents' PCs: the watermark deters and traces screenshots; it cannot prevent them.
- Dependency and image scanning in CI (SBOM), and periodic external penetration testing.
