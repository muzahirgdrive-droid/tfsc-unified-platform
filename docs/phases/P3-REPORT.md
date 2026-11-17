# P3 report: TFS Agreements 5.3, the read-only feed for TFS Onboarding and Client Success (OCS)

Branch `claude/tfs-agreements-5.3-feed`, started from release 5.2 (`0735574`). Commits `57b767e` (the release),
`1c0a101` (a fix found by the PostgreSQL run) and `32267ca` (the tokens table was cut off on a phone; found on the
screenshots). Update ZIP: `TFSC-Agreements-Deploy-2026-11-17-32267ca.zip` (4.7 MB, 218 files).

## Failures and gaps first

- **Not done by me: the end-to-end check with OCS** (TASK.md proof 4: sign an envelope and see the client in OCS Intake
  within 60 seconds). There is no OCS installation in this environment. What I measured instead, on the rehearsal
  server: a newly signed client was in the feed **4.1 s** after its envelope showed "completed" in the first rehearsal
  and in the final one it was already in the feed when the envelope was first seen completed (that check runs every 3 s).
  The publisher runs every 15 s, so the wait is 0 to 15 s plus the time to build the picture. OCS pulls
  every 30 s. The owner's step: CONNECTING.md, then sign one envelope.
- **The public HTTPS entry could not be exercised end to end here**: no certificate can be issued inside this sandbox.
  The Caddyfile was checked instead with Caddy 2.10.2: `caddy adapt` with and without an OCS site file and an
  allow-list, and a live run of the same Caddyfile over HTTP: `/api/integration/v1/meta` answered **404** from outside,
  was passed through for an allowed address, and the existing CRM bridge path `/api/integrations/v1/crm/...` was passed
  through unchanged. Inside the rehearsal stack, `caddy validate` of the mounted Caddyfile said "Valid configuration".
- **Two of the new tests failed on PostgreSQL in the first full run** (both passed on SQLite):
  1. "No gaps under 20 concurrent signings": three publishers starting at the same moment each tried to build the
     indexes, and PostgreSQL refused one concurrent catalogue change ("tuple concurrently updated"). Fixed in `1c0a101`:
     one index builder at a time (a try-lock; the others skip that round) and the end-of-run bookkeeping written under
     the publisher's lock. Production runs one publisher, but it is now safe with several. Re-run 6 times on
     PostgreSQL and 3 times on SQLite: all passed.
  2. A test of mine left a database connection open, which blocked the next test's clean-up on PostgreSQL. Fixed.
- **Company keys** (for whoever looks after OCS): the feed's `company.key` values are TFS Agreements' own stable keys
  (`scribe`, `ehs`, `24-7`, and for companies added in the app their keys: `3e_medical_billing`, `billing_nest`,
  `house_of_rcm`, `medequity_solutions`, `quantum_medical_services`). The OCS blueprint's seed (`domains.json`,
  `agreements_company_keys`) lists the companies by **legal name** (`SCRIBE ALIGN LLC` ...). The feed sends both
  (`company.key` and `company.legal_name`, and `/catalog`), so nothing is lost; if OCS matches on the key it will
  raise "unknown company" questions in Intake until its mapping uses these keys or the legal name. Not changed here:
  the contract is frozen and these keys already exist in TFS Agreements.

## What was built

| Piece | Where |
|---|---|
| Contract, copied unchanged (with `released/`) | `packages/integration-contract/agreements-v1/` |
| Migration `0014_integration_feed`: `integration_feed`, `integration_clients`, `integration_tokens`, settings `integration_instance_id` (random UUID, written once) and `integration_watermark`. Additive, idempotent; downgrade removes only what it added | `apps/agreements/migrations/versions/0014_integration_feed.py` |
| Indexes on `updated` of `esign_envelopes`, `clients`, `client_agreements`, `client_contacts`: on PostgreSQL built by the worker with `CREATE INDEX CONCURRENTLY` outside any transaction (an invalid leftover is dropped and rebuilt); on SQLite (local edition) by the migration | `integration_v1.ensure_indexes` |
| Snapshot builder, field by field from the tables (04 §3.3); `pricing.summary` from `revised_content.fee_description`, the function that prints the price on the agreement; the chosen fee option from the signer's `Fee option for <service>` dropdown | `apps/agreements/integration_v1.py` |
| `snapshot_hash` and `state_digest` exactly as TASK.md defines them; nothing time-dependent in a snapshot but `generated_at` | same |
| Publisher: every 15 s in the worker's sweep lane, under a PostgreSQL advisory lock; `seq` = max + 1 inside the lock; publishes only when the hash changed; `client.withdrawn` when a client stops qualifying; daily full sweep; retention (30 days, newest per client kept; withdrawn 1 year) | same, one line in `esign_worker.py` |
| Routes, all GET under `/api/integration/v1`: `/meta`, `/changes`, `/clients/{ref}`, `/documents/{ref}`, `/catalog`; errors `{"error", "message"}` with 400/401/404/429 | same |
| Tokens: `tfsa_` + 256 random bits, shown once, stored as SHA-256, compared in constant time, revocable; `last_used`/`last_ip` (written at most once a minute); 30 failed attempts per address per 15 minutes, then 429; 401 when no token exists | same |
| Admin › Integrations (Super Admin): create (shown once, Copy), list with last use, revoke, newest change number, last full check, lag, "Publish everything again" (queues a full sweep; it can only add) | `integrations.js`, one menu line in `index.html`, one route line in `portal.js`, styles in `ui.css` |
| System check line "Integration feed (OCS)": last published, lag; only once a token exists | one line in `health.py` |
| Release number 5.3.0 | `portal.py`, `worker.py` |
| Deployment: Caddy refuses `/api/integration/*` from the internet (allow-list `TFSC_INTEGRATION_ALLOW`, empty = nobody) and imports `/etc/caddy/sites/*.caddy`; `edge` joins `tfsc-edge` with the sites folder read-only; `agreements` joins `tfsc-link`; `agreements.sh` creates both networks and `/opt/tfsc-edge/sites` before anything starts (install, update, rollback, start) | `deploy/agreements/` |
| Update notes for the owner | `deploy/agreements/README.md` (START-HERE.md in the ZIP) |
| Test-only requirement `jsonschema` (the app itself does not need it); CI installs it | `apps/agreements/requirements-test.txt`, `.github/workflows/ci.yml` |

### Decisions the task left open, and why

1. **The activity log is a fifth change source.** Marking an agreement as a test, Trash, restore and purge do not
   touch any `updated` column, so a watermark over the four tables alone would only see them in the nightly sweep. Every
   such action is already written to the activity log with the client, so the publisher also reads `audit.created`
   (ignoring views, downloads and exports). Still no hook in business code.
2. **Nothing runs until the first token exists**, so an installation that is never connected behaves exactly as 5.2
   (no publishing, no index builds, no System check line). A revoked token keeps the feed running, so OCS can be
   reconnected with a new one.
3. **The feed path is exempt from the host-name check** in `portal.py` (GET only). OCS on the same server reaches it as
   `http://agreements:8000`; the feed is authorised by its bearer token, never by a browser session, which is what the
   host check protects. The rest of the application keeps the check (tested).
4. **Test agreements stay out even when only their contract is marked**, not just their envelope.
5. **Agreements signed together keep the order of their envelope's documents** (BAA, then Services Agreement).
6. References: `agreement_ref` = `env:<envelope id>:<document id>` or `past:<id>` (04 §3.3); `document_ref` =
   `d-<document id>` (signed copy), `c-<envelope id>` (certificate), `f-<client file id>` (past agreement's copy). Only
   documents named in a currently published snapshot can be downloaded.
7. `pages` is given for signed copies and `null` for certificates and filed copies (allowed by the schema); counting them
   would mean opening every PDF on every check.

### The golden test

TASK.md asks that the fixture "produces `examples/changes-page.example.json`, apart from ids and times". Two facts make
byte equality impossible by design, so the test compares **structure** with the example (every property of the page,
the change, the snapshot, contacts, each agreement and document) and asserts the fixture's own **values**:
- the example's `pricing.summary` lines are short ("5% of net collections"), while the contract requires the wording of
  the agreement itself ("5% of total monthly net collections, as defined in Section 5.1; invoiced monthly in arrears.");
- TASK.md's fixture chooses **option B** and has a custom service; the example chose option A and has none.

## Tests

New: `apps/agreements/tests/test_integration_v1.py`, 15 tests: golden shape and values; price wording equals the
agreement; hashes and digest exactly as defined, and a re-sweep publishes nothing; test clients, test envelopes, test
contracts, unsigned/void/declined/expired/draft envelopes and undated past agreements never appear; marking the only
agreement as a test withdraws the client (and marking it real publishes it again); Trash and purge; no gaps and strict
order under 20 concurrent signings; tokens read-only, revocation, limits 0/201/-1/x refused with 400, 429 after repeated
failures, the private host name; only the Super Admin manages tokens and only the hash is stored; nothing runs without a
token; the System check line and Admin view; retention keeps each client's newest change; a real signing through the
signing pages reaches the feed with downloadable signed copies; `test_contract_is_additive`; the example and the hash
functions. No existing test was changed or removed.

| Run | Result |
|---|---|
| 5.2 (`0735574`), SQLite | 297 passed, 270 subtests passed |
| 5.2 (`0735574`), PostgreSQL 16 | 297 passed, 270 subtests passed |
| 5.3 (`1c0a101`), SQLite | 312 passed (297 + 15 new), 270 subtests passed |
| 5.3 (`1c0a101`), PostgreSQL 16 | 312 passed (297 + 15 new), 270 subtests passed |
| 5.3 (`32267ca`, final: only a phone layout fix to the Integrations screen since `1c0a101`), SQLite | 312 passed, 270 subtests passed |

PostgreSQL runs used a database named `tfsc_test` with UTF-8 encoding, as CI does (several 5.2 tests refuse any other
name, and a `SQL_ASCII` database makes the driver return bytes).

## Update rehearsal (5.2 → 5.3, final ZIP `32267ca`, run from scratch three times; figures below from the last run)

Docker on this machine, the deployed 5.2 ZIP (`TFSC-Agreements-Deploy-2026-11-12-0735574`) installed with its own
`agreements.sh install`, then updated exactly as the guide says (unpack, copy `.env`, `agreements.sh backup`,
`agreements.sh update`). Fictional data entered through the 5.2 web API: a client with a signed BAA and Services
Agreement (medical billing with two fee options, **option B chosen on the real signing page**, credentialing on the rate
card, a custom service), a second contact, a past agreement filed with its signed copy, and a second client whose
envelope was left out for signature across the update.

| Check | Result |
|---|---|
| `agreements.sh update` | exit 0; "Updated (database 0013_followups_margins -> 0014_integration_feed)"; version 5.3.0 |
| Every row of every table and every stored file, before vs after (140 rows and files) | All identical, except: `alembic_version`; the four worker heartbeat timestamps in `settings` (they change every few seconds); two new settings (`integration_instance_id`, `integration_watermark`) |
| With no token | `/api/integration/v1/meta` 401; feed tables empty; System check lines exactly as 5.2; Admin › Integrations empty |
| The signing link emailed before the update | Signed after the update; envelope completed |
| Token created | 2 clients published 6 s later (earlier rehearsals: 3 s and 16 s); the four indexes built in the background, all valid |
| New client signed after the update | Already in the feed when its envelope was first seen completed (checked every 3 s); first rehearsal: 4.1 s |
| Over the private network as OCS will read it | `http://agreements:8000/api/integration/v1/meta` from a container on `tfsc-link`: 200 |
| Idle feed | `head_seq` unchanged over 50 s |
| Rollback (first rehearsal) | `agreements.sh rollback`: 5.2 started on the 0014 database ("newer than this release" warning, as designed) and served; updating forward again kept the same `instance_id`, `head_seq` 3, checker 65 passed, 0 failed |

Sandbox-only arrangements, not shipped and not part of the ZIP: images were built through a small `docker` wrapper
that adds host networking, this sandbox's HTTPS proxy and Debian's HTTPS mirror (the proxy refuses plain HTTP); a
`compose.override.yaml` let the application send email to a local mail catcher (`TFSC_MAIL_PRIVATE_HOSTS=true`). The
installer, compose file, Dockerfile and application code ran as shipped.

## Conformance checker

`python tools/check_feed.py --base http://127.0.0.1:8765/api/integration/v1 --token <token> --documents 50` against the
rehearsal server after the final update (three published clients, every document downloaded):

```
Access
  pass  no token: 401
  pass  wrong token: 401
  pass  writing to /changes is refused (403)
  pass  writing to /meta is refused (403)
  pass  limit=0 is refused (400) or kept within 1..200 (got 400)
  pass  errors are {"error", "message"}
  pass  limit=201 is refused (400) or kept within 1..200 (got 400)
  pass  errors are {"error", "message"}
Meta
  pass  /meta matches meta.schema.json
Changes (the whole feed, from 0)
  pass  /changes?after=0 answers 200
  pass  page 1 matches changes.schema.json
  pass  page 1: same instance_id as /meta
  pass  seq 1 is greater than 0
  pass  seq 2 is greater than 1
  pass  seq 3 is greater than 2
  pass  the feed has changes (3 read in 1 pages)
  pass  no seq beyond head_seq
  pass  every snapshot_hash is the canonical hash
Published state
  pass  published_clients 3 = clients in the feed 3
  pass  state_digest = SHA-256 of sorted 'client_ref:snapshot_hash' lines joined by a newline
Clients
  pass  /clients/9bdf6331f193… answers 200
  pass  /clients/9bdf6331f193… matches client-snapshot.schema.json
  pass  /clients/9bdf6331f193… equals its latest change
  pass  /clients/bb747ceb5a49… answers 200
  pass  /clients/bb747ceb5a49… matches client-snapshot.schema.json
  pass  /clients/bb747ceb5a49… equals its latest change
  pass  /clients/89fd3269ab6e… answers 200
  pass  /clients/89fd3269ab6e… matches client-snapshot.schema.json
  pass  /clients/89fd3269ab6e… equals its latest change
  pass  an unknown client answers 404
Catalog
  pass  /catalog matches catalog.schema.json
Documents
  pass  Northside Services Agreement 2025.pdf: downloads
  pass  Northside Services Agreement 2025.pdf: size and SHA-256 as announced
  pass  Northside Services Agreement 2025.pdf: X-Content-SHA256 header
  pass  Business Associate Agreement - Northside Family Clinic, PLLC (completed).pdf: downloads
  pass  Business Associate Agreement - Northside Family Clinic, PLLC (completed).pdf: size and SHA-256 as announced
  pass  Business Associate Agreement - Northside Family Clinic, PLLC (completed).pdf: X-Content-SHA256 header
  pass  Certificate of Completion 6A957F99-8A04.pdf: downloads
  pass  Certificate of Completion 6A957F99-8A04.pdf: size and SHA-256 as announced
  pass  Certificate of Completion 6A957F99-8A04.pdf: X-Content-SHA256 header
  pass  Services Agreement - Northside Family Clinic, PLLC (completed).pdf: downloads
  pass  Services Agreement - Northside Family Clinic, PLLC (completed).pdf: size and SHA-256 as announced
  pass  Services Agreement - Northside Family Clinic, PLLC (completed).pdf: X-Content-SHA256 header
  pass  Certificate of Completion 6A957F99-8A04.pdf: downloads
  pass  Certificate of Completion 6A957F99-8A04.pdf: size and SHA-256 as announced
  pass  Certificate of Completion 6A957F99-8A04.pdf: X-Content-SHA256 header
  pass  Business Associate Agreement - Lakeside Pediatrics LLC (completed).pdf: downloads
  pass  Business Associate Agreement - Lakeside Pediatrics LLC (completed).pdf: size and SHA-256 as announced
  pass  Business Associate Agreement - Lakeside Pediatrics LLC (completed).pdf: X-Content-SHA256 header
  pass  Certificate of Completion 458ECD73-E4DB.pdf: downloads
  pass  Certificate of Completion 458ECD73-E4DB.pdf: size and SHA-256 as announced
  pass  Certificate of Completion 458ECD73-E4DB.pdf: X-Content-SHA256 header
  pass  Business Associate Agreement - Riverbend Family Practice LLC (completed).pdf: downloads
  pass  Business Associate Agreement - Riverbend Family Practice LLC (completed).pdf: size and SHA-256 as announced
  pass  Business Associate Agreement - Riverbend Family Practice LLC (completed).pdf: X-Content-SHA256 header
  pass  Certificate of Completion 29D84EBE-C8F7.pdf: downloads
  pass  Certificate of Completion 29D84EBE-C8F7.pdf: size and SHA-256 as announced
  pass  Certificate of Completion 29D84EBE-C8F7.pdf: X-Content-SHA256 header
  pass  Services Agreement - Riverbend Family Practice LLC (completed).pdf: downloads
  pass  Services Agreement - Riverbend Family Practice LLC (completed).pdf: size and SHA-256 as announced
  pass  Services Agreement - Riverbend Family Practice LLC (completed).pdf: X-Content-SHA256 header
  pass  Certificate of Completion 29D84EBE-C8F7.pdf: downloads
  pass  Certificate of Completion 29D84EBE-C8F7.pdf: size and SHA-256 as announced
  pass  Certificate of Completion 29D84EBE-C8F7.pdf: X-Content-SHA256 header
  pass  an unknown document answers 404

65 passed, 0 failed
```

## Screen

Admin › Integrations was checked in Chromium at desktop (1440 px) and phone (390 px) width on the rehearsal server, with
no console errors. The phone check found the tokens table cut off (Revoke unreachable); it now scrolls sideways, and the
Revoke button was confirmed reachable.

## Not changed

No existing function signature, table, column or screen other than the new Admin › Integrations entry. The CRM bridge
(`crm_bridge.py`, `crm_bridge_v2.py`, `/api/integrations/v1/crm/...`) is untouched. No secrets in git or in the ZIP (the
bundle tool's own check passed). Memory limits in `compose.yaml` are unchanged: lowering them before OCS shares the
server is the owner's step in the OCS blueprint (13 §2.1), after measuring real use.
