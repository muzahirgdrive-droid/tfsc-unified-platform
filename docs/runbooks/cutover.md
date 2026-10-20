# Runbook: cutover from the Google Sheet + pilot to v2 (§25.5)

1. **T-7 days**: deploy v2 (compose or Helm), import Keycloak realm, create users in the CRM (People & Teams) and in
   Keycloak (or connect the corporate directory), enrol TOTP for every Supervisor+. Rehearse with a copy of the workbook:
   Settings → Legacy import, check every reconciliation figure, then delete the rehearsal database.
2. **T-1 day**: announce the freeze window; confirm backups and a passing restore drill.
3. **T0 (freeze)**: make the Google Sheet read-only for everyone (Share → Viewer). Export it to .xlsx and record its SHA-256.
4. Run the pilot → v2 migration (`alembic upgrade head` on the pilot database copy; migration 0003 copies pilot data).
5. Settings → Legacy import → upload the export, map each sheet to its person, start. Wait for `imported`; the
   Database column must equal Importable on every figure (for the audited workbook also the §2 column).
6. Work the review queue (contradictions, undated rows, #REF! rows).
7. **Smoke tests**: one agent signs in by SSO and saves a row; a supervisor signs in with TOTP, confirms a strong
   follow-up, converts, marks a sale → celebration visible in a second browser; Agreements handoff → review → issue →
   client profile updates; Security Center shows no dead letters.
8. **Go-live**: announce; keep the Sheet read-only for 90 days for reference.
9. **Rollback** (within the first day, only if a smoke test fails and cannot be fixed): point users back to the read-only
   Sheet + pilot, restore the pre-cutover database backup, and re-run from step 3 after the fix. Entries made in v2 during
   the failed window are exported (approved export) before the restore so nothing is lost.
