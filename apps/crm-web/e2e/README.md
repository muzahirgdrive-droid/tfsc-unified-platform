# Browser acceptance tests (§26)

Playwright, desktop (1280×860) and mobile (Pixel 7) projects. The suite runs serially against one seeded stack that
`playwright.config.ts` starts through `../crm-api/tools/e2e_stack.py`:

- a fresh `crm_e2e` PostgreSQL database with synthetic users (`*@e2e.test`, password in `helpers.ts`)
- TFSC Agreements (portal, document worker, SQLite, LibreOffice renderer) when `soffice` is installed
- the CRM on http://127.0.0.1:8799, serving the built web app (`npm run build` first)

| Spec | Covers |
|---|---|
| `sheet.spec.ts` | Acceptance 1: paste 200 messy rows → Refine preview → Refine & Save saves 197, keeps 3 red rows with reasons (grid on desktop, cards on mobile) |
| `lock.spec.ts` | Acceptance 2–3: data hunting (no timer, 0 points); Called = No starts the timer without an attempt; Called = Yes later counts it and keeps the deadline; locked after 60 min (browser clock) |
| `duplicates.spec.ts` | Acceptance 6–7: amber notice without the other agent's name + supervisor flag; protected-lead warning before and after saving, live blocking red alert in the supervisor's open browser |
| `pipeline.spec.ts` | Acceptance 9 + 11: strong follow-up → confirm → convert (first connector) → meeting → signed up → celebration in another open browser in < 3 s, names only |
| `agreements.spec.ts` | Acceptance 12: Proceed to Agreements → TFSC sign-in returns to the handoff → submit → independent approval → issued by the worker → CRM client profile via the signed webhook |
| `login.spec.ts` | Password sign-in; axe (serious + critical) on login and 10 main screens |
| `dashboards.spec.ts` | Visual snapshots of two dashboards whose data only the seed writes; dates, clocks, charts and org-wide widgets masked |

```bash
cd apps/crm-web && npm ci && npm run build
npx playwright install chromium        # or PW_CHROMIUM=/path/to/chrome
npm run e2e                            # E2E_ADMIN_DATABASE_URL / E2E_PYTHON if not the local dev defaults
npx playwright test --update-snapshots # after an intended dashboard change; review the new PNGs before committing
```

Locally an already-running stack on :8799 is reused. Restart it for a clean database. Repeated runs within 15 minutes can
hit the per-account sign-in limit in `login.spec.ts`; that limit is working as designed.
