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
| P1 Foundation | done except Keycloak | design system + app shell + world clock + OpenAPI TS client shipped in `apps/crm-web` (v2); Keycloak realm remains (P7) |
| P2 Capture | done except import job | My Work quick entry + Sheet View (Glide grid, paste/header detect, Refine/Refine & Save, encrypted draft) shipped; XLSX import job pending |
| P3 Integrity | done except SLA job | follow-ups, strong FU, leads, flags, people/teams UI shipped; SLA escalation job pending (P7) |
| P4 Meetings & Celebration | done | meetings UI, celebration overlay (SSE, confetti without worker for CSP), Wall of Fame |
| P5 Dashboards | done except workbook reconciliation | dashboard API (`routes_dashboard.py`) + Overview widgets (ECharts, react-grid-layout) + Reports; reconciliation importer pending |
| P6 Agreements v2 | done | backend both apps + CRM pre-flight/client profile UI; E2E acceptance 12-13 green with real LibreOffice |
| P7 Hardening & Scale | not started | |

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
- [ ] Keycloak realm export + OIDC (`infra/keycloak`) — moved to P7
- [x] design system + app shell + world clock (crm-web v2)
- [x] OpenAPI → TS client generation (`tools/export_openapi.py` → `apps/crm-api/openapi.json` → `npm run gen:api`; CI fails on drift)

## Next actions (do the first unchecked one)
1. [x] P5 dashboard API (metric catalog, leaderboard, layouts, presets, comparison) — `routes_dashboard.py`, `tests_v2/test_dashboard.py`
2. [x] crm-web v2 — `apps/crm-web` (pilot UI moved to `apps/crm-web-pilot`). Verified with screenshots
       (`apps/crm-web/e2e/screens.mjs` against `tools/seed_demo.py` data) + 21 Vitest tests.
3. [ ] Workbook reconciliation importer (§20.5, acceptance 18) with a SANITIZED fixture (never commit the real xlsx)
4. [ ] XLSX/CSV import job (resumable, all sheets, 50k rows) + legacy workbook historical import (Super Admin)
5. [ ] P7: security center, exports (watermarked, approval), offboarding, flag SLA escalation job, audit anchor, Keycloak realm,
       k6 load tests + docs/PERFORMANCE.md, backups/runbooks, compose/Helm (Dockerfile must run `tfsc_crm.main:app`),
       agreements Alembic baseline
6. [ ] Playwright E2E acceptance flows (beyond screenshots); regenerate both _HANDOFF zips (§29)

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
