# RESUME HERE — TFSC Sales CRM v2 + Agreements (unified platform)

This file is the single source of truth for "where did the work stop". Any Claude Code
session (this account or another) continues by reading this file top to bottom, then
doing the first unchecked item in **Next actions**.

## The task (owner's request, verbatim intent)

> Execute the master command for the unified system (`docs/handoff/MASTER-COMMAND.md`,
> phases P0–P7 in §28). Auto-resume after usage limits. If a limit would take >48 h,
> hand over a compact ZIP so another Claude Code account can continue with full history.

- Master command: `docs/handoff/MASTER-COMMAND.md` (copy of the owner's file; §0–§29).
- Session log / chat history summary: `docs/handoff/SESSION-LOG.md`.
- Raw inputs (NOT in git; owner must re-attach in a new account):
  - `TFSC-CRM-Handoff-2026-10-15.zip` → already unpacked into `apps/crm-api`, `apps/crm-web`, `docs/legacy-handoff/crm`
  - `TFSC-Compliance-Handoff-2026-10-15.zip` → unpacked into `apps/agreements`, `docs/legacy-handoff/agreements`
    (private signature PNG/JSON deliberately excluded from git: `apps/agreements/private-assets/signatures/`)
  - `Sales - Office 2 - Tracking (3).xlsx` (real provider data, SHA-256 `278fe143…3f2d`) — never commit.
    Needed only for the §20.5 reconciliation run (P7). Put it at `.data/inputs/workbook.xlsx` (gitignored).
  - `Alpha-Bridge-Developer-Handoff.zip` — context only, out of scope (§4.1).

## Branch / repo

- Repo `muzahirgdrive-droid/tfsc-unified-platform`, branch `claude/vigilant-babbage-cg07la`.
- Every finished step is committed + pushed. `git log --oneline` is the precise history.

## How to bootstrap a fresh container

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r apps/crm-api/requirements-dev.txt -r apps/agreements/requirements.txt
(cd apps/crm-api && python -m pytest -q)        # pilot CRM: 17 tests
(cd apps/agreements && python -m pytest -q)     # agreements: 30 tests + 42 subtests
(cd apps/crm-web && npm ci && npm run build)
```
See `docs/handoff/STATUS.md` for v2 test commands as they are added.

## Phase status (§28)

| Phase | Status | Notes |
|---|---|---|
| P0 Baseline | done | pilot 47 tests + 42 subtests green; baseline migration = pilot schema (diff 0); CI; ADRs |
| P1 Foundation | done | design system, app shell, world clock, OpenAPI TS client; Keycloak realm + SSO delivered in P7b |
| P2 Capture | done | My Work, Sheet View, and the XLSX/CSV server import job (`/api/imports`, resumable, per-row results, 50k rows, SHA-256 stored) |
| P3 Integrity | done | follow-ups, strong FU, leads, flags, people/teams; SLA escalation job delivered in P7a |
| P4 Meetings & Celebration | done | meetings UI, celebration overlay (SSE, confetti without worker for CSP), Wall of Fame |
| P5 Dashboards | done | dashboards + legacy workbook import with §20.5 reconciliation (acceptance 18; see runbook) |
| P6 Agreements v2 | done | backend both apps + CRM pre-flight/client profile UI; E2E acceptance 12-13 green with real LibreOffice |
| P7 Hardening & Scale | done | P7a + P7b + P7c (k6, SSE fan-out, bulk pool, docs/PERFORMANCE.md) |

## P0 checklist
- [x] Unpack both handoffs into monorepo (`apps/crm-api`, `apps/crm-web`, `apps/agreements`)
- [x] Reproduce pilot tests: CRM 17 passed; Agreements 30 passed + 42 subtests (= 47 + 42 ✔)
- [x] Pilot frontend builds (tsc + vite)
- [x] Alembic baseline matching pilot schema (`migrations/versions/0001_pilot_baseline.py`)
- [x] CI pipeline (`.github/workflows/ci.yml`)
- [x] ADRs for §4 stack decisions (`docs/adr/`)

## P1 checklist
- [x] Refine engine Python + TS, 283 shared vectors (`packages/refine`) — done early (P2 item)
- [x] v2 data model `tfsc_crm/db/models.py` + migration 0002 (partitions, RLS, crm_app role)
- [x] Migration 0003: §9 seeds + pilot data copy (tested in `tests_v2/test_migrations.py`)
- [x] Core: ids (UUIDv7), config, crypto (blind index + AES-GCM), pacific clock, db session (SET LOCAL ROLE + app.org_id)
- [x] Domain: roles/scope/capabilities, auth sessions, audit chain, outbox, notify, scoring ledger, duplicates engine,
      activities lifecycle, rollups, pipeline (strong FU → lead → sign-up, attribution, reassignment)
- [x] API layer (`tfsc_crm/api/*`, Pydantic models) + `tfsc_crm/main.py` app factory (auth, people/teams, settings, activities,
      refine/batch, precheck, follow-ups, strong FU, leads, flags, notifications)
- [x] Authz matrix tests (every OpenAPI op classified) + agent leak scan + RLS isolation test — `tests_v2/test_security.py`
- [x] Acceptance 1-10, 14, 15 as API tests (`tests_v2/test_api_capture.py`, `test_api_pipeline.py`) — 52 v2 tests green
- [x] Bridge contract package (`packages/bridge-contract`, Py + TS)
- [x] Keycloak realm export + OIDC (`infra/keycloak`, `security/oidc.py`) — done in P7b
- [x] design system + app shell + world clock (crm-web v2)
- [x] OpenAPI → TS client generation (`tools/export_openapi.py` → `apps/crm-api/openapi.json` → `npm run gen:api`; CI fails on drift)

## Next actions (do the first unchecked one)
1. [x] P5 dashboard API (metric catalog, leaderboard, layouts, presets, comparison) — `routes_dashboard.py`, `tests_v2/test_dashboard.py`
2. [x] crm-web v2 — `apps/crm-web` (pilot UI moved to `apps/crm-web-pilot`). Verified with screenshots
       (`apps/crm-web/e2e/screens.mjs` against `tools/seed_demo.py` data) + 21 Vitest tests.
3. [x] Workbook reconciliation + legacy historical import (§20.5/§25.3, acceptance 18): `domain/legacy_workbook.py` (pure scan +
       figures), `domain/legacy_import.py` (resumable job), `/api/admin/legacy-imports`, Settings → Legacy import,
       `tools/legacy_reconcile.py`, sanitized fixture `tests_v2/legacy_fixture.py`, runbook `docs/runbooks/legacy-workbook-import.md`
4. [x] XLSX/CSV import job: `domain/file_import.py`, `/api/imports`, Sheet View → Upload file → ImportDialog
5. [x] P7a: Security Center (`routes_security.py`, Security page tabs), one-click offboarding, approval-logged AES-ZIP exports
       (watermark, formula-safe, password shown once), red-flag SLA + overdue follow-up escalation, audit content verification +
       daily anchors, scheduler (`workers/scheduler.py`, idempotent `job_runs`), migration 0004, models-vs-migrations test
6. [x] P7b: OIDC/Keycloak SSO (+TOTP for Supervisor+, verified in a real browser against Keycloak 26), realm export,
       images from repo root (CRM runs `tfsc_crm.main:app`), `infra/compose` (verified up locally), Helm chart (lint +
       kubeconform clean; not run on a live cluster), Agreements Alembic baseline, restore drill (`tools/restore_drill.py`),
       THREAT-MODEL.md, runbooks (backups/PITR, cutover, legacy import), token-protected `/metrics`
7. [x] P7c: k6 profiles (`infra/load/crm.js`, `run.sh`), synthetic data (`gen_data.py`, 2.68 M activities), SSE fan-out
       harness (`sse_fanout.py`, 15,000 streams), fixes found by measuring (SSE disconnect bug + per-worker Redis
       subscription, paste deadlock, batch registry cache, one-query auth, bulk pool in compose/Helm);
       results + what was NOT measured (10k/50k profiles, 100 M rows) in `docs/PERFORMANCE.md`
8. [x] Playwright acceptance E2E (`apps/crm-web/e2e`, desktop + mobile, CRM + TFSC, axe on 11 screens, dashboard
       snapshots; CI job `e2e`), CI `security` job (pip-audit, npm audit, gitleaks, SBOMs) and coverage gates (85%),
       property tests (scoring ladder, ledger, lock timing), lifecycle tests; both §29 handoff ZIPs regenerated with
       `tools/make_app_handoffs.py` (docs in `docs/handoff/apps/`). Fixes found on the way are in SESSION-LOG.md.
9. [x] ALL PHASES DONE (P0-P7). The auto-resume scheduled task was disabled. What remains is owner input
       (`docs/handoff/apps/crm/08-OWNER-CHECKLIST.txt`) and the open items in `docs/handoff/apps/*/04-KNOWN-ISSUES.csv`.
       If asked to continue without new instructions, good next items: ESLint for crm-web (V2-D08), TFSC /reviews
       styling + inline approval reason (TFSC-V2-01), concurrent same-lead handoff test (legacy CRM-11), TFSC OIDC
       on the compose trial stack (TFSC-12).

### P8 — "complete everything" (owner asked 2026-10-20: "complete everything. make the complete product.")
Work through these in order; each: implement, test, commit + push, tick here, note in SESSION-LOG.md.
1. [x] Quick fixes: TFSC /reviews styling + inline approval reason (TFSC-V2-01); ESLint for crm-web in CI (V2-D08);
       concurrent same-lead Agreements handoff test (legacy CRM-11). Commits 22ae3b5, ff107e4. The CRM-11 test found a
       real race (two TFSC workers -> 500s/timeouts on parallel handoffs); fixed with a per-CRM-client advisory lock.
       (Known-issues CSVs are updated in item 9.)
2. [x] Round-robin auto-assignment per team, weighted by open-lead capacity, off by default (§14.4, V2-D06);
       90-day lead protection: supervisor prompted to release/renew, never auto-release with a meeting (§27).
       Commit 136d9cf: domain/assignment.py, domain/protection.py, migration 0005, scheduler job lead_protection
       (daily 05:00 PT), POST /api/leads/{id}/protection, team settings in People -> Teams, tests_v2/test_assignment_protection.py
3. [x] Keycloak email invitations (set password + MFA enrollment) and password recovery via SMTP (§4.2, §6),
       verified on the compose stack with a local mail sink; TFSC OIDC sign-in verified (TFSC-12). Commit a6e1c94:
       integrations/keycloak.py (sso.* outbox topics), realm provisioner client + SMTP_*, Mailpit (--profile trial),
       TFSC SSO fixes (identity link, Lax cookie, reset_required, back-channel URL). Re-run the trial: infra/README.md
       "invitation, recovery and TFSC SSO trial". Trial env lives only in the scratchpad (secrets; never committed).
4. [x] Files: S3-compatible object storage (MinIO) with presigned short-lived URLs + AV scan on upload (§4.2);
       audit anchors shipped to an object-lock bucket (V2-D04). Client file storage stays deferred (§13). Commit 084cdd7:
       storage.py (db|s3), security/antivirus.py (clamd INSTREAM), anchors to object-lock bucket + version-aware verify,
       migration 0006; compose objects (RustFS: MinIO publishes no images any more) + objects-init + clamav; Helm
       files.* + storage-init job + clamav. Local S3 tests: CRM_TEST_S3_ENDPOINT (RustFS container).
5. [x] Observability: structured JSON logs, OpenTelemetry traces (OTLP), Sentry (API + web) when configured,
       Prometheus + Grafana dashboards in compose (§4.2). telemetry.py in both apps (JSON logs with request/trace ids,
       route-template access lines, OTLP traces incl. a custom SQL span hook because the SQLAlchemy instrumentation does
       not support 2.1, query strings stripped from spans, scrubbed Sentry); metrics shared across uvicorn workers
       (CRM_METRICS_DIR); web Sentry lazy-loaded (src/lib/errors.ts); infra/observability (Prometheus, 6 alert rules,
       Grafana "TFSC CRM" dashboard); compose --profile observability (Prometheus, Grafana, Jaeger); Helm
       observability.* + optional ServiceMonitor. Sentry checked by unit tests only (no live project).
6. [x] Backups: pgBackRest (encrypted, PITR) in compose + automated restore test into an isolated database (§4.2).
       infra/compose/postgres (PostgreSQL 16 alpine + pgBackRest binary, tfsc-backup script), WAL archiving
       (archive_timeout 60), `backup` service (weekly full, daily diff, restore test after each: PITR to markers in a
       socket-only scratch server, pg_amcheck, migrations, audit chain), ops.backup_runs -> tfsc_crm/backups.py ->
       Security Center "Backups" card + crm_backup_* metrics + 3 alerts + Grafana row; off-site repo2 via
       backup-offsite.env (verified against RustFS over HTTPS); DR restore command; CI runs backup + restore test.
       Runbook rewritten with measured drills.
7. [x] Performance: batch activity INSERTs per paste; re-measure; larger dataset and more concurrent users than
       before, within this VM's limits (PERFORMANCE.md stays measured-only). Commit ee09a84 (fast path stages rows,
       one statement per table at the flush: 1,516 -> 19 statements; NANP verdict cached). PERFORMANCE.md "Round 2":
       3.98 M activities, every run from a fresh template copy, A/B x2 at 550 agents/6 s think, 800 agents at 20 s and 6 s.
8. [x] Kubernetes: deploy the Helm chart to a local cluster (kind/k3d) and smoke-test it. infra/helm/smoke/kind-smoke.sh
       (kind 1.35: install, 13 checks through the ingress and in the cluster, upgrade, checks again), CI job k8s-smoke.
       Found and fixed: hooks never ran on upgrade (YAML flow comma), named image users vs runAsNonRoot, RWX-only CRM
       volume, ClamAV signatures hidden by emptyDir. Clean run: install 50 s, upgrade 43 s, 13/13 twice.
9. [x] Regenerate both §29 handoff ZIPs + the continuation ZIP; update known issues / owner checklist; final report.
       docs/handoff/P8-REPORT.md; apps/*/04-KNOWN-ISSUES.csv, 04b, 08-OWNER-CHECKLIST.txt, crm/02-ARCHITECTURE.txt
       updated; ZIPs built with tools/make_app_handoffs.py (fresh previews) and tools/make_handoff_zip.sh; the
       auto-resume scheduled task ("TFSC auto-resume") disabled. P8 is complete; further work needs the owner
       (08-OWNER-CHECKLIST.txt).
10. [x] (asked after P8) A ZIP to deploy on one VPS: `tools/make_vps_bundle.sh` -> TFSC-VPS-Deploy-<date>-<commit>.zip
       with `tfsc.sh` and START-HERE.md (sources: deploy/vps/). Installed from the ZIP and walked end to end on a test
       host (test domains, Caddy local CA, mail catcher); four bugs found and fixed (SESSION-LOG, "After P8").

11. [x] (asked 2026-10-22) Agreements: governing state per company drives BAA/Services/NDA; company signature dated
       with the effective date; Mutual NDA; DocuSign-style e-signature with company compliance mailboxes (SMTP send,
       IMAP replies/bounces), Certificate of Completion, /verify. Owner to do: connect the three Google Workspace mailboxes
       (START-HERE step 5) and have counsel review LEGAL_REVIEW.md "Version 4.1".

## How to run the v2 web app locally
```
tools/dev_services.sh start
createdb -h 127.0.0.1 -p 54329 -U postgres crm_demo
cd apps/crm-api && CRM_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:54329/crm_demo alembic upgrade head
CRM_DATABASE_URL=... CRM_DATA_DIR=/tmp/demodata python tools/seed_demo.py   # synthetic demo users, password Demo-password-2026!
(cd ../crm-web && npm ci && npm run build)
CRM_DATABASE_URL=... CRM_DATA_DIR=/tmp/demodata CRM_MODE=local CRM_ORIGIN=http://127.0.0.1:8766 uvicorn tfsc_crm.main:app --port 8766
node ../crm-web/e2e/screens.mjs http://127.0.0.1:8766 /tmp/screens   # optional screenshots
```

## How to run v2 tests
`tools/dev_services.sh start` then `cd apps/crm-api && python -m pytest -q tests_v2` (uses CRM_TEST_DATABASE_URL,
default `postgresql+psycopg://postgres@127.0.0.1:54329/crm_test`).

## Decisions log (things decided without asking; §27 defaults apply)
- Private signature images are excluded from git (privacy rule §3); installs use `manage.py install-signatures`.
- Docker build context for CRM is `apps/` (Dockerfile at `apps/crm-api/Dockerfile`).
- `CRM_WEB_DIST` env var (or `../crm-web/dist`) locates the compiled SPA.
- business_date of live entries (manual/grid/import) = server Pacific date at commit; the typed Date cell is kept in `raw`.
  Only the Super Admin legacy workbook import uses the typed date (historical, no locks/notifications).
- Red refine issues block saving that row (acceptance 1); a red phone is not saved as research automatically.
- Duplicate-conflict entries (amber and red) are capped at 10 points until resolved (§12 wording applies to both).
- Audit chain writes are serialized per org with an advisory lock; activity creates are evidenced by the
  activity row + revisions (not audit_log) to avoid write contention at scale.
- Rollups are maintained synchronously in the save transaction (O(1) upsert per agent/day) + nightly reconcile,
  rather than via the outbox consumer — simpler and always consistent; outbox still drives SSE/TFSC delivery.
- Web: canvas-confetti runs without its blob: worker (CSP `script-src 'self'` stays strict).
- Web: dashboard widget tables use container queries (tiles are resizable), not viewport breakpoints.
- Web deps pinned exactly; prod `npm audit` clean (lodash 4.18.1, echarts 6.1.0). Glide Data Grid needs `legacy-peer-deps` (.npmrc).
- Legacy import: historical rows raise no duplicate flags and get no conflict cap (they predate v2 rules); first-seen
  phone facts are recorded and repeats are summarised in the reconciliation report. Strong FU / Sign Up cells become
  confirmed milestones decided by the importing Super Admin (no confirmation step existed in the workbook).
- Legacy import: contradictory rows (an outcome Yes while Call Attempt is not) and undated rows are NOT imported (review
  queue: dismiss and re-enter by hand); #REF! rows import from their raw cells and are listed for acknowledgement.
- Legacy import: rows are stamped 12:00 Pacific on their sheet date (the workbook has no times).
- "Entry" = a row with a provider name or phone, Excel blank semantics (a space is not blank) → 89,914 entries, as audited.
- Phone repeats: our reproducible key (10 digits, US 1 removed) gives 5,323 / 2,915 cross-sheet; the audit's 5,917 / 3,382
  definition is unknown. Not part of acceptance 18.
- File import job (non-legacy) uses live rules: server Pacific date, import lock window, duplicate flags; same file + sheet
  cannot be imported twice by the same owner (request_key = sha256 + sheet).
- `defusedxml` added so openpyxl parses uploaded workbooks defensively.
- Exports: Manager requests, Director approves; ZIP password random, stored encrypted, revealed once to the requester only;
  24 h expiry; 50k rows; requester's scope; meeting fields never exported; cells starting = + - @ tab CR get a leading '.
- Audit anchors are written to `audit_anchors` (app role: insert-only) and appended to `<CRM_DATA_DIR>/audit-anchors/*.jsonl`.
  Shipping that file to a write-once (object-lock) bucket is a deployment step; no remote sink is claimed.
- Offboarding moves active leads to the user's manager as audited reassignments with the departing owner keeping 100%
  attribution of past work; TFSC revocation goes through the outbox (retried until delivered).
- Scheduler runs in-process only in `CRM_MODE=local`; hosted deployments run `python -m tfsc_crm.workers.scheduler`.
- SSO: no auto-provisioning (CRM accounts are created by managers); first SSO sign-in links by verified email; an unused
  temporary local password is removed at first SSO sign-in. Supervisor+ need `amr` with an MFA method or `acr` in
  CRM_OIDC_MFA_ACR; Keycloak realm provides `amr: ["pwd","otp"]` via the AMR mapper.
- Containers: build context = repo root (packages/ needed); optional BuildKit secret `build_ca` for TLS-inspecting proxies.
  In this sandbox Docker Hub rate-limits pulls; base images were pulled from mirror.gcr.io and tagged locally.
- Service-to-service calls use internal URLs (CRM_TFSC_INTERNAL_URL, TFSC_CRM_INTERNAL_URL, CRM_OIDC_INTERNAL_URL)
  while sending the public Host header, so both apps keep strict Host allow-lists.
- `/metrics` exists only when CRM_METRICS_TOKEN is set; bearer-protected; exempt from the Host check (scraped by pod IP).
- Agreements now migrates with Alembic at start-up (`migrate.py`); pre-Alembic installs are stamped after create_all.
- Sign-in limits: per IP 1,000 attempts and 100 failures per 15 min (one office NAT = one address), per account 10 per
  15 min; SSO start/callback 1,000 per IP per 15 min. Load tests must clear the limiter between runs (they sign in ~600×).
- SSE: each API worker holds one Redis subscription per org and fans out in memory (not one Redis connection per
  browser); a stalled browser's queue (1,000 events) drops new events instead of growing; auth/backlog run off the loop.
- Batch saves (paste, file import) lock all their phone-registry rows up front in key order (no deadlocks between
  concurrent pastes sharing numbers) and the fast path reads those rows from a per-batch cache (1 round trip per row).
- Bulk pool: `/api/activities/batch`, `/api/imports*`, `/api/admin/legacy-imports*` are routed to separate `crm-bulk`
  processes (compose edge + Helm Ingress) so 1,000-row pastes do not slow interactive requests (see PERFORMANCE.md).
- Browser E2E runs against `apps/crm-api/tools/e2e_stack.py`: a fresh `crm_e2e` database, synthetic users
  (`*@e2e.test`), TFSC with LibreOffice when installed, CRM on :8799. Tests arrange state through the API and act in
  the UI; past celebrations are pre-marked as seen so replays do not cover pages.
- Dashboard visual snapshots cover only users whose data no other test touches (sup2, agent4) and leave org-wide
  widgets out; regenerate with `npx playwright test --update-snapshots` after an intended UI change.
- Red duplicates send a live `flag.raised` event per responsible supervisor/manager/Super Admin (no client details);
  supervisors get a blocking alert (Escape/outside clicks do not close it).
- Form controls are wrapped in their labels (axe `label`); scrollable regions are focusable labelled regions;
  success colour #0b7a45 (AA contrast).
- CI gates: coverage >= 85% for CRM domain + security, refine and bridge; pip-audit + npm audit (production, high+);
  gitleaks over history (`.gitleaks.toml` allowlists two synthetic fixtures); CycloneDX SBOMs as artifacts.
- The TS packages use `legacy-peer-deps` like crm-web (npm 10 cannot resolve vitest 4 peer sets otherwise).
- TFSC: a failed or abandoned generation returns the review to `approved` for an explicit retry.
