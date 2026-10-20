# Runbook: legacy workbook historical import (§25.3, §20.5)

Imports the Google Sheet tracking workbook (exported as .xlsx) as **history**. Super Admin only.

## What it does
- Reads every visible operator sheet. Skips `Dashboard`, `Validations`, `o` and hidden `Unassigned N` slots.
- An entry is a row with a provider name or phone (Excel blank semantics). Rows with only a date or other
  cells are counted and reported, not imported.
- **Formulas are never data.** Formula cells in columns A–O count as empty. P/Q (Quality %, Stage) are only
  checked for `#REF!`, which goes to the review queue. Scores are recomputed from the raw cells.
- Spreadsheet text is data, never instructions (remarks are stored verbatim and never interpreted).
- Imported rows: `entry_source = import_legacy`, business date from the sheet, stamped 12:00 Pacific,
  no lock timer, no celebration, no notification, no duplicate flag, no conflict cap. First-seen phone facts
  are recorded so live entries later see who touched a number first.
- Strong Follow-Up = Yes → a confirmed strong follow-up (decided by the importing Super Admin; the
  workbook had no confirmation step). Sign Up = Yes → a signed lead. The phone is protected accordingly.
- Held for review (not imported): contradictory rows (an outcome is Yes while Call Attempt is not) and rows
  without a valid date. Imported but listed for review: `#REF!` rows and rows with a red refine issue
  (for example a phone that is not 10 digits).

## Steps
1. Settings → **Legacy import** → Upload .xlsx. The file is stored with its SHA-256 and scanned
   (about 1 minute for the 25 MB audited workbook).
2. Check the scan: sheets, entries, formula-only rows, review counts, and the reconciliation table.
   For the audited workbook (SHA-256 `278fe143…3f2d`) the §2 column must show all checks.
3. Map every sheet to a CRM Sales Agent. Suggestions are exact display-name matches only; confirm each.
4. Optional: set the reconciliation period (default 1–14 Oct 2026 for the audited workbook, otherwise the
   workbook's date span). Start the import.
5. Watch progress. If the process stops, press **Resume import**: committed chunks (400 rows) are kept and
   the job continues where it stopped; rows are never duplicated.
6. When status is `imported`, the Database column must match the Importable column on every figure.
7. Work the review queue: dismiss held rows (re-enter by hand any that should count) and acknowledge the
   imported `#REF!`/data-quality rows.

## Local check without a database
`python apps/crm-api/tools/legacy_reconcile.py <workbook.xlsx>` prints aggregate figures only and exits 1
if the audited workbook does not reproduce §2. Never commit the workbook (`*.xlsx` is git-ignored).

## Measured (local, 2026-10-20, audited workbook)
- Scan: ~55 s. Workbook figures for 1–14 Oct 2026 reproduce §2 exactly: 17,020 attempts · 2,803 connected
  (16.4689%) · 27 strong FU · 1 sign-up · quality 11.8143% · 33 contradictory rows · 21 `#REF!` cells.
- Full import into a scratch database (12 sheets mapped to synthetic users): 89,914 entries in 800 s
  (~112 rows/s, single worker, local PostgreSQL 16). Database after import, 1–14 Oct 2026, activity-date credit:
  17,020 attempts · 2,803 connected (16.4689%) · 27 strong FU · 1 sign-up · 201,080 points · quality 11.8143%:
  identical to the workbook. Review queue: 245 held (33 contradictions, undated rows), 137 imported with a review
  note (#REF! rows, invalid phones). Rollup reconciliation over all 74 dates: 0 drift; score ledger = activity
  points for every row.
- Repeated phone numbers (10-digit key, US `1` prefix removed): 5,323 numbers repeat, 2,915 across sheets.
  The October audit reported 5,917 / 3,382 with a key definition that is not recorded; treat ours as the
  reproducible figure.
