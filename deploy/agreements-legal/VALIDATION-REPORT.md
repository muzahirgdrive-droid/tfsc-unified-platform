# Validation report: package 5.4.0-legal.2 (TFSC Agreements 5.4.0) for legal.24-7consultancy.cloud

**Nothing here was run on your VPS.** Every result below comes from a sandbox: Linux, Docker Engine 29.9.3, Docker
Compose **v5.6.0** (the official release binary, SHA-256 checked against the release's checksum list), Chromium. The sandbox
contains a simulated production built from the facts you gave. A sandbox result is evidence, not production validation.
Section 3 lists what only your VPS can confirm.

## 1. The simulated production (sandbox)

Built from your facts of the second review:
- **Containers:** project `tfsc-agreements-legal`; `legal-agreements` and `agreements-worker` on
  `tfsc-agreements:20261117-32267ca` (the 5.3 build), also tagged `tfsc-agreements:rollback-20261125`. Web has **no
  published host port**.
- **Networks:** web is on `alphabridge_edge` with alias `tfsc-agreements-legal`, behind a **separate** Caddy container
  that proxies to that alias.
- **Database:** PostgreSQL 16 (`tfsc-postgres:16`) with database `tfsc` and **no `crm` database**; superuser and
  application user `crm`; `max_connections=100`, `shared_buffers=256MB`, `effective_cache_size=4GB`, `archive_mode=on`;
  revision `0014_integration_feed`.
- **Other services:** backup on `tfsc-postgres:20261110-c803dc9`, ClamAV `clamav/clamav:1.4.6`.
- **Volumes:** the 7 volumes with the production names.
- **Settings:** `DB_POOL_SIZE=5` on web and worker, `DB_POOL_OVERFLOW` not set. Some upstream variables are absent, and
  one extra variable is present (`TFSC_MAIL_PRIVATE_HOSTS`, needed for the sandbox's test mail server).
- **OCS:** a simulated OCS API with `OCS_AGREEMENTS_URL=http://tfsc-agreements-legal:8000/api/integration/v1`, **not** on
  `tfsc-link`; and no `tfsc-link` network at the start.
- **Data:** fictional users, clients, contracts, generated documents, a past agreement with its file, two envelopes
  (one completed with a signature, one out for signature), an OCS token.

Only `PROD_DIR` and the two image IDs in `prod.conf` were set to the sandbox's values (the sandbox's 5.3 build has a
different ID than yours).

Differences from your server that the sandbox cannot remove:
- the backup container's own loop was idle, because its scheduled backups need the `crm` maintenance database, which
  the sandbox deliberately lacked;
- the build went through the sandbox's HTTPS proxy;
- the Caddy was plain HTTP.

## 2. Results (sandbox)

| # | Requirement | What was run | Result |
|---|---|---|---|
| 1 | Shell syntax, ShellCheck | `bash -n`/`sh -n` on every script; ShellCheck 0.11.0 (severity warning) on `agreements.sh` and `validate-release.sh`; the repository's `ruff check .` | pass, 0 findings |
| 2 | Compose files with v5.6.0 | `docker compose config -q` and `config --format json` of the production compose (placeholder `.env`) and of the upstream compose; the production compose refuses to run without `agreements.sh` | pass |
| 3 | Health check without a published port | preflight, update, both rollbacks and status asked the version **inside** the web container (`docker exec`), and probed Caddy's path on `alphabridge_edge`; web had no host port before or after | pass |
| 4 | Database preflight on `tfsc` as `crm` | `pg_isready -d tfsc`, `select 1` and `SHOW` on `tfsc` as `crm`, revision `0014_integration_feed`; no `crm` database existed | pass |
| 5 | Pool settings kept | new containers: environment identical to the old ones (23 web, 28 worker variables; values compared, not shown); `DB_POOL_SIZE` 5 → 5 on both; `DB_POOL_OVERFLOW` still unset (checked inside the process); web processes 2 → 2; maximum connections 2×(5+5) + 5×(5+5) = 70, unchanged | pass |
| 6 | Web and worker upgrade | `agreements.sh update` (16.5 s with the build cached): encrypted copy verified (45 tables with data), build, migration 0014 → 0015, switch, web 5.4.0 healthy, Caddy path, worker heartbeats | pass |
| 7 | Automatic rollback, no downgrade | broken release, **from revision 0014**: migration applied, start-up error recognised after about 1 minute, back to `tfsc-agreements:rollback-20261125` with the same settings, 5.3 running on 0015 ("newer than this release; starting without migrating"); no downgrade. Repeated once from 0015 | pass |
| 8 | PostgreSQL, backup, ClamAV unchanged | same container IDs, start times and restart counts before and after every update, rollback and the OCS change; Docker events: only web, worker, the one-off migration container and short-lived probe containers created or removed | pass |
| 9 | Caddy through `alphabridge_edge` | probe inside `alphabridge_edge`: 200; the separate Caddy served 5.4.0, then 5.3.0 after rollback | pass |
| 10 | OCS through `tfsc-link` once attached | before: the OCS API could not resolve `tfsc-agreements-legal`. After the change in OCS-TFSC-LINK.md (OCS compose: `default` + `tfsc-link`; `up -d --no-deps ocs-api`): 401 without a token, 200 with it; still 401 after the Legal rollback; the Legal containers were not touched by the OCS change | pass |
| 11 | Migration 0015 safe, additive, from 0014 | reviewed (MIGRATION-REVIEW.md); applied 0014 → 0015 on `tfsc` while 5.3 served; the pre-update copy (0014) restored completely into a throwaway database (then dropped) | pass |
| 12 | Data intact | right after the update: users, clients, contracts, documents, past agreements, client files, envelopes, recipients, signatures, fields, events, audit log, and every file in `/contracts` and `/data` identical (row hashes, using the columns that existed before for tables that gained columns); 0 rows changed outside runtime bookkeeping. The OCS checker: 39 passed, 0 failed. A signing link emailed before the update was signed after it | pass |
| – | Prune, down, volume removal, PostgreSQL rebuild | none in the script (static check); in every run: no volume or image removed, no PostgreSQL build, the rollback tag unchanged | pass |
| – | Secrets | values never printed (names only); `compose.runtime.yaml` mode 600 and excluded from packages; release check on the final package: no `.env`, keys, dumps or secret values | pass |
| – | Dialog fix (from legal.1, unchanged) | Chromium, 16 checks | 16/16 |

Problems found by these tests and fixed before this package (all in the production scripts, none in the application):
1. Compose records a variable left unset as a bare name. Reading it as empty would have given a rolled-back container
   `DB_POOL_OVERFLOW=''`, and the application cannot start with that.
2. The comparison counted "left unset" as "added".
3. The repository linter flagged the checker.

## 3. Only your VPS can confirm (in this order)

1. `sudo deploy/agreements-legal/validate-release.sh`: `RELEASE CHECK PASSED`.
2. `sudo ./agreements.sh preflight`: `PREFLIGHT PASSED`. In particular:
   - it shows `docker compose 5.6.0`;
   - `rollback image tfsc-agreements:rollback-20261125 exists and is exactly the running image` (`sha256:09eedd0d…`);
   - database `tfsc` answers as `crm`, at revision `0014_integration_feed`;
   - the backup container has `pg_dump`, `pg_restore`, `openssl`, `sha256sum`, the passphrase, the socket, and room;
   - web and worker environments identical; no published host port; `DB_POOL_SIZE = 5` on both;
   - Caddy's network → 200.
3. Whether the production `.env` is found automatically. The preflight prints its path. If not, set `ENV_FILE` in
   `prod.conf`; the file is only read.
4. The build on the VPS needs Debian and PyPI over HTTPS (or `BUILD_CA`) and at least 5 GB free. The preflight checks
   the space.
5. After the update: the public site https://legal.24-7consultancy.cloud answers as 5.4.0 (`./agreements.sh status`).
6. The separate OCS change (OCS-TFSC-LINK.md), then OCS reads the feed.

## 4. Remaining manual actions

| Action | Who / where | When |
|---|---|---|
| Unpack beside `/opt/tfsc-agreements-legal`; `validate-release.sh`; `agreements.sh preflight` | you, on the VPS | before the update |
| `sudo ./agreements.sh update` (answer `y`) | you, on the VPS | when the preflight passes |
| Check the site, Reminders card, Admin › Reminders, `./agreements.sh status` | you | after the update |
| **OCS change**: OCS API on `tfsc-link` in the OCS compose file, then `docker compose up -d --no-deps <ocs-api-service>` in the OCS folder (recreates the OCS API container only; a few seconds) | you, in the OCS installation | after the Legal update |
| Next release: tag the image that then runs as the new rollback image and record it in `prod.conf` (PRODUCTION-ADAPTATION.md §4) | you / support | before the next update |
| Keep `tfsc-agreements:rollback-20261125` and the new image; do not prune | you | always |
