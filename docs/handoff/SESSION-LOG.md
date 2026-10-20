# Session log (human-readable chat history)

Raw transcripts are bundled in the handoff ZIP (`transcripts/*.jsonl`). This file is the
condensed, append-only history so a new account understands what was asked and decided.

## 2026-10-20 — Session 1 (Claude Code, cloud)

**Owner message:** attached `TFSC-SALES-CRM-V2-MASTER-COMMAND.md`, `Alpha-Bridge-Developer-Handoff.zip`,
`Sales - Office 2 - Tracking (3).xlsx`, `TFSC-Compliance-Handoff-2026-10-15.zip`,
`TFSC-CRM-Handoff-2026-10-15.zip` and asked:
"Please execute the command for the unified system and please work on auto resume when the usage
get restored from where you stopped due to usage limit exceeds. Also when the weekly limit or any
other usage limit where it would take indefinite or more than 48 hours to restore usage, please give
a compact ZIP file of what you have worked so far in a way where other claude code account could just
start doing all the work from where you stop with all the chat history as well."

**Work done:**
- P0: unpacked both handoffs into the monorepo; reproduced pilot tests (CRM 17 + Agreements 30 = 47,
  plus 42 subtests, all green); pilot frontend builds.
- Resume tooling: `docs/handoff/RESUME.md` (state), this log, `tools/make_handoff_zip.sh`
  (git bundle + snapshot + transcripts + START-HERE prompt).
- Auto-resume: a scheduled task "TFSC auto-resume" fires into this session every 3 hours (minute 48) and
  continues from RESUME.md if work stopped (e.g. usage limit). Disable it when all phases are done.
- P0 done; P1 foundation backend (PostgreSQL 16 schema, partitions, RLS, pilot data migration, Alembic up/down);
  refine engine Py+TS (283 shared vectors); bridge contract Py+TS.
- v2 API (FastAPI + Pydantic) covering capture, duplicates/flags, follow-ups, strong FU, leads, meetings (Google adapter),
  SSE/celebrations, Agreements v2 (both apps). Tests: CRM v2 67, pilot 17, TFSC 37 + 42 subtests, refine 300 + 285 TS,
  bridge 6 + 6 TS. E2E acceptance 12-13 passes across two running sites with real LibreOffice rendering.
- Hypothesis found a real idempotence bug in name casing; fixed in both ports.
- Interim handoff ZIP generated at 01:43Z and sent to the owner.

## 2026-10-20 — Session 1, continued (after a context compaction and an auto-resume firing)

**Owner messages:** none new. The scheduled "TFSC auto-resume" task fired once (03:48Z) and work continued from RESUME.md.

**Work done:**
- P5 dashboard API (`routes_dashboard.py`): metric catalog, leaderboard, team board, funnel, heatmap, breakdowns,
  supervisor comparison, validated layouts; rollup drift fix (points by activity date) + invariant test.
- crm-web v2 (`apps/crm-web`, pilot UI moved to `apps/crm-web-pilot`): React 19 + TanStack Router/Query + Tailwind v4
  design system, app shell (navy sidebar, command palette, world clock, notifications, mobile bottom nav, watermark),
  Overview dashboards (ECharts, react-grid-layout), My Work, Sheet View (Glide grid), Follow-ups, Strong FU, Leads
  (+ Agreements pre-flight), Meetings, Wall of Fame, Clients, Flags, People & Teams, Reports, Settings, Security, Me.
- API: `/api/auth/me` returns `{user, csrf}`; new `/api/config` (dialer, celebration seconds, agreements URL, timezone);
  SSE celebration backlog includes the agent name; OpenAPI exported + typed client generated, CI fails on drift.
- Verified visually with Playwright screenshots on a synthetic demo DB (`tools/seed_demo.py`); fixed issues found:
  deep links bounced through /login (auth state race), CSP blocked confetti's blob worker, cramped widget tables,
  low-contrast avatars on navy, heatmap axis overlap, "1 sales".
- 21 Vitest unit tests (sheet parsing, Pacific time/DST, toggles + rules, API errors, refine vectors via the app alias).
- Prod `npm audit` clean (lodash 4.18.1, echarts 6.1.0).
- All suites green: pilot 17, v2 82, refine+bridge 306, agreements 37 (+42 subtests), web 21.
- Legacy workbook import + §20.5 reconciliation (acceptance 18): pure scanner, resumable Super Admin job, review queue,
  Settings → Legacy import UI, CLI `tools/legacy_reconcile.py`, sanitized fixture tests. Verified locally on the real
  workbook (not committed): all §2 figures reproduce, and after a full import the database matches exactly
  (17,020 / 2,803 / 16.4689% / 27 / 1 / 11.8143%), 0 rollup drift. Import speed ~112 rows/s.
- XLSX/CSV server import job (`/api/imports`) with live rules, per-row results, resume and idempotent re-import;
  Sheet View → Upload file → import dialog. Paste batch save now shares the same per-row code.
- Tests: v2 100, pilot 17, web 21.
- P7a: Security Center (overview, audit browse + verify with content hashes and daily anchors, sessions with remote end,
  dead-letter retry), one-click offboarding, approval-logged encrypted exports, SLA escalations, scheduler with idempotent
  job runs, migration 0004 and a models-vs-migrations drift test. Tests: v2 121, pilot 17, refine+bridge 306, agreements 37.

## 2026-10-20 — Session 1, continued (after a worker restart; auto-resume fired at 06:48Z and 09:49Z)
- P7b: CRM OIDC sign-in (PKCE, JWKS-validated tokens, MFA for Supervisor+), Keycloak realm export, container images
  from the repo root, compose stack, Helm chart, Agreements Alembic baseline, restore drill, threat model, runbooks,
  `/metrics`. Verified on a local compose stack: all services healthy, real-browser SSO for an agent (password) and a
  supervisor (password + TOTP), unknown accounts refused, signed CRM ↔ TFSC call through internal URLs.
- Found and fixed while running the real stack: alembic and redis were dev-only dependencies of the CRM (the image
  could not migrate or use Redis); the bridge package could not find its schemas when installed (env override added).
- P7c: k6 load tests on one 4-vCPU VM with 2.68 M synthetic activities (550 agents, 50 supervisors, 3 pasters).
  With the bulk pool at 20 s think (~7x go-live save rate) every §24 threshold passed (paste p95 2.91 s, pre-dial 30 ms,
  reads 88 ms, writes 77 ms, 0 errors). At 6 s think (~23x) the API p95s still passed but paste p95 was 3.78 s.
  SSE: 15,000 concurrent streams all got the celebration, p95 2.80 s.
- Bugs found by measuring and fixed: SSE cleanup raised on every disconnect and used one Redis connection per browser
  (broke above ~1,000 clients); concurrent pastes sharing numbers deadlocked; pastes in the interactive processes
  caused the paste and tail-latency failures (now a separate bulk pool in compose and Helm); auth took 3–4 queries per request.
- Not measured, stated in docs/PERFORMANCE.md: 10k/50k user profiles, 100 M-row dataset, multi-host, soak, failover.
- Tests: v2 143, pilot 17, refine+bridge 306, agreements 40 (+42 subtests).
- After a worker restart (auto-resume fired at 12:49Z and 15:48Z): the work continued in the same session.
- Browser acceptance E2E (§26): Playwright suite for paste -> refine -> save (200 rows, 197 saved, 3 kept with
  reasons), data hunting + lock countdown, amber/red duplicates (supervisor's live blocking alert), strong FU ->
  lead -> meeting -> sale -> celebration in a second browser < 3 s, Proceed to Agreements -> TFSC review -> issued ->
  CRM client profile; axe on 11 screens; dashboard snapshots. 49 tests (desktop + mobile), green locally and in CI.
- Bugs found by those tests and fixed: no supervisor red alert existed (acceptance 7); mobile Sheet hid unsaved-row
  reasons; TFSC /reviews returned raw JSON 401 to a signed-out user and lost the handoff code at login; the lead
  dialog's overlay blocked the "Open Agreements" toast; My Work header counters stayed stale after an edit and the
  Edit button stayed after lock; 49 unlabelled form fields, low-contrast green, unreachable scroll regions, nested
  controls in the Leads list; TFSC reviews stuck in "queued" after a failed generation.
- Quality gates added: coverage (domain + security 89.6%, lifecycle 64% -> 92%, refine 98%, bridge 100%),
  property tests, 100-way conversion race, CI security job. Vulnerable runtime deps fixed (cryptography 50.0.2,
  pyzipper 0.4.0) and dev toolchain (vite 7.3.7, vitest 4.1.11): pip-audit and npm audit report 0 known issues.
- §29: both per-app handoff ZIPs regenerated (`tools/make_app_handoffs.py`): docs, API inventory (CRM 122 routes,
  TFSC 44), schema (CRM 72 tables incl. 41 with RLS; TFSC 19), environment names, service catalog, 34 previews,
  SHA-256 manifests; no workbook, signature image or .env inside (checked).
- Tests at the end: v2 153, pilot 17, refine 300 + bridge 12 (Python), agreements 43 (+42 subtests), web 21,
  refine TS 285, bridge TS 6, E2E 49.

