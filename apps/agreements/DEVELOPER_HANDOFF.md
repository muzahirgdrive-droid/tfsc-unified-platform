# Version 5 (October 24, 2026): company signatures, premium portal, reports and activity log

The plan, mapping each request to a design: `ARCHITECTURE-V5.md`.

| Module | What it adds |
|---|---|
| `companies.py`, `admin.js` | Company settings (Super Admin): `company_profiles` (signatory, title, signature PNG in the database); `clean_signature` makes the paper transparent and trims to the ink; fallback to an image installed with `agreements.sh signatures`. Workspace settings (portal name, copyright holder, Help email, trust line) and `/api/public/workspace`. Email trust: SPF, DKIM, DMARC and BIMI read over DNS-over-HTTPS with the record to publish. |
| `server.py`, `document_engine.py`, `signature_store.py`, `worker.py` | `build_documents(data, contract_id, company)`: the company's signature, signatory and title on the execution block; the image sits on the rule at a fixed maximum size (`image_xml(..., baseline=...)`). |
| `esign.py` | Completion rule in `advance()`: signers decide; viewers wait only with `settings.ordered`; `settle()` (run by `sweep`) completes envelopes held only by viewers. No default expiry (`expire_days` 0). `/links` turns recipient links off and on. Field suggestions (`/suggestions`), the client library for the wizard (`/library`), more audit events (viewed, downloads, copied, template saved). |
| `esign_pdf.py` | `suggest()`: execution anchors on generated agreements, label detection on uploads, initials on every page in a free spot; DocuSign-style signature and initials stamps. |
| `insights.py`, `reports.js`, `home.js` | `/api/home` (counts and figures by scope), `/api/insights`, report datasets with chosen columns, Excel (openpyxl; cells written as text) and CSV export (formula-like cells neutralised), `saved_reports`, the merged activity log (audit table plus envelope events). Scope: `team(actor)`. |
| `database.py` | `audit.ip` and `audit.user_agent` from the `request_meta` context variable set by the middleware. |
| `mailer.py`, `entities.py` | Per-company email layout in the website colours (`color`, `accent`, `dark`, `display`). |
| `index.html`, `ui.css`, `portal.js`, `esign.js`, `clients.js`, `login.html`, `verify.html` | New app shell with a hash router (`#/home`, `#/agreements/<box>`, `#/envelope/<id>`, `#/send`, `#/clients/<id>`, `#/create`, `#/reports/<tab>`, `#/activity`, `#/admin/<page>`), the envelope wizard (documents, recipient cards, signing order, fields, options) and the field tagger; copyright and Help on every page. |
| `sign.html`, `sign.js`, `sign.css`, `assets/fonts/` | Signing page: 16 signature styles (OFL fonts, licences beside them, files unmodified), separate initials style, draw or upload each. Inter (OFL) subset for the interface. |
| `migrations/versions/0004_premium_portal.py` | New tables, audit columns and indexes; removes the old automatic 30-day expiry (a chosen expiry is kept). Additive only. |
| `migrations/versions/0005_signature_size.py` | `company_profiles.signature_scale` (ADD COLUMN only). Empty means the company default (`entities.py` `signature_scale`: 0.6 Scribe Align and EHS, 0.85 24-7). `document_engine` multiplies the signature box (`signature_store.MAX_WIDTH`/`MAX_HEIGHT`) by it; Company settings has the slider (30–100 %). |

Tests: `tests/test_premium_portal.py` (company settings and who may change them, the signature on generated
agreements, the completion rule, expiry and links, placement, Home, reports in Excel and CSV, the activity log, the
migration's data step). With `TEST_DATABASE_URL` set they also run on PostgreSQL.

# Electronic signature (version 4.1, October 22, 2026)

DocuSign-style signing inside TFSC Agreements (hosted edition: signers must reach the public address).

| Module | What it does |
|---|---|
| `esign_db.py` | Tables: envelopes, documents (original and completed PDF), recipients, fields, hash-chained events, signing tokens and sessions; company mailboxes, mail outbox, inbound mail and its attachments (Alembic `0002_esign`). |
| `esign.py` | Envelope lifecycle (draft, sent, completing, completed, declined, voided, expired, templates), routing order, the signer API under `/sign/r/<recipient>/`, completion, reminders and expiry (`sweep`), sender API under `/api/esign/`, `/verify`. |
| `esign_pdf.py` | PDF inspection, page images (poppler `pdftoppm`), automatic placement on generated agreements (the two-column signature table), stamping completed fields (rotation-aware), the Certificate of Completion. |
| `mailer.py` | Per-company mailbox settings (secrets sealed by `secretbox.py`), Google app password or service-account XOAUTH2, transactional outbox and SMTP sending with retries, email templates, IMAP polling (read-only) for replies and bounce notices, ClamAV scanning of reply attachments. |
| `esign_worker.py` | The background loop in `worker.py`: send email, complete envelopes, reminders and expiry, read inboxes. |
| `esign.js`, `esign.css` | Sender screens: Signatures folders, envelope details, field editor, Company mailboxes. |
| `sign.html`, `sign.js`, `sign.css` | The recipient's signing page (no account). `verify.html`, `verify.js`: fingerprint check of a completed PDF. |

Design rules: every email is queued in the same transaction as the change that causes it; envelope changes lock the
envelope row (PostgreSQL `FOR UPDATE`, SQLite `BEGIN IMMEDIATE`), so parallel signers complete exactly once; signing
links are random tokens stored only as SHA-256 hashes and removed from stored email copies once sent; a signing
session cookie is scoped to one recipient's path; signature images are decoded and re-encoded server-side; everything
read from an inbox is data and is shown as text. Tests: `tests/test_esign.py` (lifecycle, permissions, failures,
mail, PDF) with a fake SMTP server in `tests/esign_support.py`; they pass on SQLite and PostgreSQL
(`TEST_DATABASE_URL=postgresql+psycopg://.../tfsc_test`).

## Version 4.2 (October 23, 2026): branding, client profiles and the remaining DocuSign features

| Module | What it adds |
|---|---|
| `assets/brand/*.png`, `entities.py` | Each company's logo (cropped from its letterhead) and colours (`logo`, `color`, `accent`). |
| `mailer.py` | Branded email layout; the logo and a document icon are embedded inline (multipart/related, `cid:`). Email kinds `host`, `removed`, notification `reassigned`. |
| `esign.py` | Roles `in_person` (hosted by `host_id`; `/api/esign/envelopes/<id>/host/<recipient>`); field kinds `first_name`, `last_name`, `number`, `date`, `radio`, `attachment`, `note`; field options `tooltip`, `validation`, `max_length`, `readonly`/`prefill`, `group`/`choice`, `condition` (same recipient; cycles refused); `visibility()` applies conditions at Finish; Finish later (`/api/save`, `recipients.progress`); Assign to someone else (`/api/reassign`, status `reassigned`, setting `allow_reassign`); signer attachments (`/api/attachment`, ClamAV, image to PDF, `documents.source='attachment'`); `correct_layout` (recipients and fields of people who have not finished); uploads in Word/RTF/text/image converted to PDF; list filters and the `expiring` folder. |
| `esign_pdf.py` | `to_pdf` (LibreOffice for office files, reportlab for images), radio stamping, logo on the certificate. |
| `clients.py`, `clients.js`, `clients.css` | Client profiles: details (`clients.profile`), `client_contacts`, `client_files` (type-checked, scanned), the document library, contacts address book (`/api/contacts`), sending a client file for signature. Recipients of sent envelopes become contacts. |
| `esign_bulk.py` | Bulk send from a template (`esign_bulk` batches, `envelopes.bulk_id`), reports and the CSV export (formula-like cells neutralised). |
| `migrations/versions/0003_docusign_parity.py` | The new tables and columns. Only `ADD COLUMN` on existing tables, so SQLite never rebuilds a table that has rows pointing at it. |

`database.init_db()` begins SQLite migrations with `BEGIN IMMEDIATE`, so an upgrade is all-or-nothing and only one
process migrates at a time; `migrate.baseline_metadata()` creates a pre-Alembic install's missing tables without
columns that later migrations add. Tests: `tests/test_docusign_parity.py` (every feature and the matching refusals) and
`tests/test_migrations.py::test_upgrade_with_signed_envelopes_in_place`.

# Alpha Bridge Compliance

Version 3.0.1: all companies now use automatic PAGE and NUMPAGES fields, displayed as Page X of Y. Verified every page of all six example PDFs and refreshed total-page caches in the DOCX files.

## Version 3, October 8, 2026

Launch `Start Alpha Bridge Compliance.cmd`. The installed folder keeps its
legacy name to preserve user paths; the ZIP root is AlphaBridgeCompliance.

**Interface:** Scribe Align website navy #004B79, cyan #00AEEF, Outfit typography
and light geometry: https://scribealign.com/. Fonts and OFL license are bundled
under assets/fonts for offline use. premium.css and studio.js implement the
guided steps; app.js still manages validated draft and service state.

**Companies:** entities.py owns names, signatories, addresses transcribed from
the supplied letterheads, template hashes and safe body bounds. Company images
and their original OOXML media bytes are preserved. The generated 24-7 header's
stray text '0' is removed. EHS retains A4 paper size; others use Letter.

**Content:** revised_content.py is the active builder. templates/baa.json contains
the supplied BAA text with source DOCX and hash, inline bold runs and only
client/date/company substitution. Georgia law is retained independently of the
Services Agreement's state setting. Its compliance subtitle is source text,
not software certification. templates/service-scopes.json restores verbatim
Section 1 paragraphs from the original billing/credentialing, scribe and virtual
assistant DOCX files. Other service scopes, common responsibilities and terms
remain from the previous version pending further source files. Similar wording
is not silently merged; exact duplicate responsibilities are shared by role.
Sections 1, 2, 3 separate services, responsibilities and fees. Corrected internal
cross-references follow the new numbering. No AI calls occur at runtime.

**Signatures:** signature_store.py validates PNG/JPEG inputs. .data/signatures
stores images by signatory; Naeem's asset is shared by Scribe Align and EHS.
Original image bytes are retained. JSON crop metadata removes blank margins
through DOCX crop properties. Replacement uploads clear that metadata. The image
is anchored on the company signing line. Client and date lines remain blank
unless the form supplies a client name/title. Images provide no copy prevention,
tamper protection, identity verification or cryptographic signing.

**Package:** includes authorized company signature images and layout metadata.
Excludes client drafts, client exports, logs, test data and runtime caches.
Preserve existing .data/clients and output folders during upgrades. No third-party
Python packages or npm build are required at runtime; Word desktop is required.

**Validation:** 13 unit tests pass, including all service models, source BAA exact
text, four supplied scopes, company isolation and WordPress/custom fees. Six
DOCX/PDF pairs generated across all entities, with original media checked
byte-for-byte. All PDF pages rendered through Poppler and visually reviewed;
both supplied signatures align on their lines. Browser checks cover entity
selection and independent website prices. Use `python -m unittest discover -s
tests -v`. Native Word export supplies common pagination for both file formats.

## Earlier implementation notes

The following version 1 notes are historical; the details above supersede them.

# Scribe Align Contract Studio

Local Windows application, version 1.0.0. Prepared October 6, 2026.

## Run

Extract the complete ZIP to a reasonably short writable path, for example
`C:\ScribeAlignContractStudio`. Double-click `Start Contract Studio.cmd`.
Use `Stop Contract Studio.cmd` to stop the loopback service.

Requirements: Python 3.10 or later and an installed, activated Microsoft Word
desktop application on Windows. The application itself uses Python's standard
library only. There is no npm build, pip installation, cloud service or API key.
The launcher first checks the Codex bundled Python path and then Python on PATH.
If necessary, adjust the interpreter candidates in `Start.ps1`.

For development, from this folder:

```powershell
python server.py --port 8765
```

Open `http://127.0.0.1:8765`. Optional flags are `--data-dir PATH` and
`--output-root PATH`. Opening `index.html` as a file does not start the backend.
The default destination is `Client Contracts` beside the application folder.

## Implementation

| File | Purpose |
| --- | --- |
| index.html, styles.css, app.js | Browser form, saved clients, service selection, prices, preview and export results |
| server.py | Loopback HTTP API, validation, draft persistence, folder creation and generation transaction |
| contract_content.py | Fixed BAA and service-specific legal clause builders; all 18 service lines |
| document_engine.py | Patches the retained DOCX package, formatting, tables and execution page |
| export_word.ps1 | Opens generated DOCX read-only in hidden Word, updates fields and exports PDF |
| assets/letterhead.docx | Original supplied Scribe Align letterhead, retained unchanged |
| Start.ps1, Stop.ps1 | Start/reuse and stop the specific application instance |
| pick_folder.ps1 | Native Windows destination folder picker |
| LEGAL_REVIEW.md | Legal source references, drafting assumptions and counsel review decisions |
| README.txt | End-user guide |
| examples/ | Fictional sample BAA and combined billing/credentialing agreements in Word and PDF |
| tests/ | Standard-library automated tests |

The frontend calls `/api/config`, `/api/clients`, `/api/clients/{id}`,
`/api/preview`, `/api/generate` and `/api/pick-folder`. Mutating requests require
the per-process CSRF token injected into the HTML. Host and Origin checks limit
requests to the local application. Generated file download URLs are session
references; the underlying files remain in their destination after restart.

Successful generation saves a revision JSON record, including inputs and output
hashes, and saves the client draft. Each generation creates a new identifier and
does not overwrite previous agreement revisions. A generation is published only
after every selected DOCX and PDF has been produced successfully.

## Verification performed

- Browser form successfully generated a standalone credentialing Services
  Agreement and BAA together, with four downloadable files and client folders.
- Final sample exports successfully generated through Microsoft Word: BAA six
  pages; combined medical billing and credentialing Services Agreement seven pages.
- Inspected rendered sample pages, preserved artwork, signature continuity and
  footer page counts. The last substantive clause accompanies the execution page
  to avoid a short tail page followed by a detached signature page.
- Checked retained header/media bytes against the supplied template and checked
  PDF text bounds and page numbers.
- Eight automated tests passed, covering all 18 catalog services and 33 available
  service/pricing combinations, independent dates, fixed BAA language, installment
  arithmetic, standalone scope isolation, custom services, invalid inputs,
  filename collision handling and cleanup after failed PDF conversion.
- API checks passed for text preview, saved-client retrieval and CSRF rejection.
- JavaScript syntax check passed; Start and Stop launchers exercised on this PC.

Run the portable tests from this folder:

```powershell
python -m unittest discover -s tests -v
```

Tests do not require Word or create real client agreements. A real export smoke
test requires Word. On another Windows PC, verify Word activation, fonts, folder
permissions and the native folder picker. Very large custom texts and every
possible service combination have not been individually rendered.

## Important design decisions

Only SCRIBE ALIGN LLC is enabled. Its signatory is Naeem Abbas, Chief Executive
Officer. The other entity choices are disabled. A later entity implementation
must supply its own approved letterhead, address, signatory and legal defaults.

The BAA does not change with selected services or pricing. Effective dates can be
separate. Execution dates remain blank for actual signing. On-demand projects use
one invoice or two milestone invoices; month-to-month renewal does not repeat a
completed project charge. A later standalone service engagement preserves prior
unrelated agreements.

The supplied artwork extends to the bottom page edge. Existing page numbers are
above it. That placement is preserved because putting numbers below it would
require moving or cropping the artwork. Body margins are deliberately enlarged
to keep text out of the original header and footer.

Word drives PDF pagination. DOCX package parts are patched narrowly rather than
resaved by Word, preserving original artwork and relationships. Footer field
caches are refreshed after the export. Word updates fields again when opened.
`TEMPLATE_SHA256` prevents accidental use of an unvalidated replacement letterhead.
If changing the template, update the hash only after inspecting a fresh render.

## Remaining deployment and adoption work

1. Have qualified counsel approve the reusable legal clauses, pricing structures,
   commercial risk allocation and jurisdiction choices. The app does not certify
   HIPAA compliance. See LEGAL_REVIEW.md for the specific decisions and sources.
2. Confirm the company address, notice email and chosen output/data directories.
   Specify engagement facts in service parameters; the app cannot infer provider
   names, payer lists, work schedules or project acceptance criteria.
3. Test the app on each target Windows installation with activated desktop Word.
   The application is intended for one local operator. Word COM automation is
   not designed here as a multi-user hosted server.
4. Establish normal Windows access controls and backups for `.data`, contract
   folders and revision records. Storage is plain local files, not an encrypted
   medical-record system. Do not enter PHI into this contract form.
5. If hosting is required later, implement authentication, authorization,
   encrypted persistence, audit controls, tenant isolation and a suitable document
   conversion service before exposing any endpoint beyond loopback.

Electronic signature and email were added in version 4.1 (see "Electronic signature" at the top). There is no
scheduled legal update feed or automatic PDF regeneration after a user manually edits the Word output.

## Package contents and privacy

This ZIP contains application source, the supplied company letterhead, fictional
examples, tests and documentation. It excludes saved client drafts, logs,
temporary renders, working scripts and the original sample client agreements.
No credentials are needed or included.
