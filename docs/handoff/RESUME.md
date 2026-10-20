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
| P0 Baseline | in progress | see checklist below |
| P1 Foundation | not started | |
| P2 Capture | not started | |
| P3 Integrity | not started | |
| P4 Meetings & Celebration | not started | |
| P5 Dashboards | not started | |
| P6 Agreements v2 | not started | |
| P7 Hardening & Scale | not started | |

## P0 checklist
- [x] Unpack both handoffs into monorepo (`apps/crm-api`, `apps/crm-web`, `apps/agreements`)
- [x] Reproduce pilot tests: CRM 17 passed; Agreements 30 passed + 42 subtests (= 47 + 42 ✔)
- [x] Pilot frontend builds (tsc + vite)
- [ ] Alembic baseline matching pilot schema
- [ ] CI pipeline (.github/workflows)
- [ ] ADRs for §4 stack decisions

## Next actions (do the first unchecked one)
See the P0 checklist above, then P1 in §28 of the master command.

## Decisions log (things decided without asking; §27 defaults apply)
- Private signature images are excluded from git (privacy rule §3); installs use `manage.py install-signatures`.
- Docker build context for CRM is `apps/` (Dockerfile at `apps/crm-api/Dockerfile`).
- `CRM_WEB_DIST` env var (or `../crm-web/dist`) locates the compiled SPA.
