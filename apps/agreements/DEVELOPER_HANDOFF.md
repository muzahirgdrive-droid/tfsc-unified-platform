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

There is no e-signature integration, email sending, scheduled legal update feed or
automatic PDF regeneration after a user manually edits the Word output. Those
features should be treated as separate future work.

## Package contents and privacy

This ZIP contains application source, the supplied company letterhead, fictional
examples, tests and documentation. It excludes saved client drafts, logs,
temporary renders, working scripts and the original sample client agreements.
No credentials are needed or included.
