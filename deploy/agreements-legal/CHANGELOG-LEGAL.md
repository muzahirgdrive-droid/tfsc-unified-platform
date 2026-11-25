# Change log: TFSC Agreements 5.4, production package for legal.24-7consultancy.cloud

Package `TFSC-Agreements-Legal-5.4.0-<date>-<commit>.zip`. It contains the full 5.4 release plus the production
adaptation. It replaces the earlier 5.4 ZIP (`TFSC-Agreements-Deploy-2026-11-25-97e2d02.zip`), which carried only the
upstream deployment files and must not be used on this server.

## 1. Production adaptation (new; nothing like it was in any earlier package)

- **`agreements.sh` (top of the package) is now the production script.** The upstream script's problems on this server,
  and what replaced each, are in PRODUCTION-ADAPTATION.md §3. In short:
  - it uses the web service `legal-agreements` everywhere;
  - it never acts on PostgreSQL, backup or ClamAV (`docker exec` on the running containers only);
  - every `up` is `--no-deps --no-build legal-agreements agreements-worker`;
  - it never builds PostgreSQL, never prunes, never runs `down`, never deletes backups;
  - the rollback image is `tfsc-agreements:rollback-20261125`, verified by image ID;
  - the encrypted pre-update copy is made in the backup container and verified (the whole copy decrypted and read by
    `pg_restore`, then the table list checked for the key tables);
  - automatic step back to the verified image when the new version is unhealthy;
  - `install`, `start`, `stop`, `restart` and `down` are refused; `restart-app` restarts web and worker only.
- **`preflight`** (new, read-only) compares every environment variable (values compared, never printed), mount,
  network, alias, published port and container name of the running web and worker with what the new configuration
  would create. It stops on any loss or change, and checks images, volumes, networks, PostgreSQL settings, the backup
  container's tools and room.
- **`deploy/agreements-legal/compose.yaml`** (new, production): project `tfsc-agreements-legal`; services
  `legal-agreements`, `agreements-worker`, `postgres`, `backup`, `clamav`. Everything else follows from production:
  - networks: external `alphabridge_edge` (alias `tfsc-agreements-legal`), `tfsc-link` and the project default network;
  - volumes: all seven external, with the exact production names;
  - images: backup `tfsc-postgres:20261110-c803dc9`, PostgreSQL `tfsc-postgres:16`, never built; `pull_policy: never`;
  - PostgreSQL 100 connections / 256MB / 4GB / WAL archiving;
  - web 2 workers, pool 8 + 4; worker 1 + 1 processes;
  - no proxy; health port `127.0.0.1` only (the running one is kept);
  - `DATABASE_URL` from the production `.env` if set there;
  - `compose.local.yaml` for server-only settings.
- **`prod.conf`** (new): the production facts, no secrets.
- **`validate-release.sh` + `legalcheck.py`** (new): the reusable release check (PRODUCTION-ADAPTATION.md §4).
- **`tools/make_agreements_legal_bundle.sh`** (new, repository only): builds this package and refuses to unless the
  release check passes. Upstream deployment files go to `upstream/` under names nothing picks up by accident.
- Documents (new): PRODUCTION-ADAPTATION.md, UPDATE-AND-ROLLBACK.md (= START-HERE.md), MIGRATION-REVIEW.md, this file,
  VALIDATION-REPORT.md.

## 2. Application fix: dialogs no longer lose what was typed (Add user / invite a person, and every other dialog)

Cause: the shared dialog code (`apps/agreements/app.js`) closed every dialog when a click landed outside its box. A text
selection that started in a field and ended beside the dialog produced such a click, and so did a stray click on the
dimmed page. What was typed was lost. Escape and × closed without asking.

Fix (front end only; no API or database change), in the shared code so every dialog gets it:
- a click outside never closes a dialog that has fields; dialogs with nothing to fill in (help, preview) close only when
  the press also started outside;
- the fields' state is remembered when any dialog opens. ×, Close/Cancel and Escape ask "You have unsaved changes. Are
  you sure you want to discard them?" when something changed. Cancel keeps the dialog open with everything as typed; OK
  closes. Without changes they close at once. Escape is handled by the page, so the browser never closes a changed
  dialog on its own (also on a second Escape);
- after a user is saved (temporary sign-in details shown), closing asks nothing (`portal.js`);
- validation, saving and the protection against double submission are unchanged (a double click on Save created one
  user in the test);
- `reminders.js`: the Cancel button of "Stop reminders for a person" takes part in the same check.

The OCS front end (`source/apps/ocs/web/src/design/ui.tsx`) is **not part of this package** (it belongs to the separate
OCS system) and was not changed. The same fix would be needed there separately.

## 3. Release 5.4 itself (unchanged from the 5.4 ZIP of November 25)

Repeated emails fixed (an email handed to the mail server is never resent by itself; "Needs a look"); reminder rules,
company defaults, per-person and per-envelope switches, the do-not-remind list, the Reminder centre, the signer's stop
link, safety guards; migration 0015 (MIGRATION-REVIEW.md). The OCS integration feed and its contract are unchanged.

## 4. Modified files

Since the 5.4 ZIP you received (`97e2d02`):
```
M apps/agreements/app.js            shared dialog behaviour (section 2)
M apps/agreements/portal.js         user dialog: nothing to discard after a successful save
M apps/agreements/reminders.js      Cancel button joins the dialog check
A agreements.sh (package top)       production script (copy of deploy/agreements-legal/agreements.sh)
A deploy/agreements-legal/*         agreements.sh, compose.yaml, prod.conf, legalcheck.py, validate-release.sh, documents
A tools/make_agreements_legal_bundle.sh   (repository only, not in the package)
moved in the package: deploy/agreements/{compose.yaml, agreements.sh, Caddyfile, README.md, backup-offsite.env.example}
      -> upstream/{compose.upstream.yaml, agreements.upstream.sh, Caddyfile.upstream, START-HERE.upstream.md, ...}
```
Since 5.3 as installed (`32267ca`), the application files changed by 5.4 are: `app.js`, `esign.js`, `esign.py`,
`esign_db.py`, `health.py`, `index.html`, `mailer.py`, `portal.js`, `portal.py`, `ui.css`, `worker.py`; new: `reminders.py`,
`reminders.js`, `migrations/versions/0015_reminders.py`.
