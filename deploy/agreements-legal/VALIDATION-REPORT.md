# Validation report: TFSC Agreements 5.4 production package for legal.24-7consultancy.cloud

Everything below was **run** in a sandbox (Linux, Docker 29.9.3, Docker Compose 5.7.1, Chromium), not on the VPS.
Nothing was deployed, and the production server was not contacted. Where a check could not be run, it says so.
**This package is not "production-ready" until `validate-release.sh` and `agreements.sh preflight` pass on the VPS
itself** (section 3).

## 1. Checks and results

| Check | How | Result |
|---|---|---|
| Bash/sh syntax of every script | `bash -n` / `sh -n` on every `*.sh` and `tfsc-backup` (validate-release.sh step 1) | pass, all |
| ShellCheck | ShellCheck 0.11.0, severity warning, on `agreements.sh` and `validate-release.sh` | pass, 0 findings |
| Compose configuration | `docker compose config -q` and `config --format json` of the production compose with a placeholder `.env` | pass |
| Compose refuses direct use | `docker compose config` without `TFSC_AGREEMENTS_IMAGE` | refuses (as intended: only agreements.sh runs it) |
| YAML syntax | via `docker compose config` (PyYAML not installed in the sandbox: the separate YAML parse was NOT run) | pass (compose parse) |
| Service names | compose has exactly `legal-agreements`, `agreements-worker`, `postgres`, `backup`, `clamav`; the script uses `$WEB`/`$WORKER` from prod.conf; no `dc … agreements` | pass |
| Image names and rollback tag | postgres `tfsc-postgres:16`, backup `tfsc-postgres:20261110-c803dc9`, ClamAV `clamav/clamav:1.4.6`; app image set by agreements.sh; `tfsc-agreements:4`/`:rollback` not referred to; rollback `tfsc-agreements:rollback-20261125` verified by ID | pass |
| Volume names | all 7 external with the exact production names; web and worker mount `agreements-data:/data`, `contracts:/contracts` | pass |
| Networks | `alphabridge_edge`, `tfsc-link`, `tfsc-agreements-legal_default` external; web alias `tfsc-agreements-legal` on edge and link | pass |
| Proxy / ports | no proxy service; only `127.0.0.1:<health port>` published | pass |
| Production settings | PostgreSQL 100 / 256MB / 4GB / archive on; web 2 workers, pool 8+4; worker 1+1 | pass (compose and, in the simulation, `SHOW` on the server) |
| Upstream variables | every environment variable upstream gives `agreements`/`agreements-worker` exists in the production compose | pass |
| Migrations | 15 scanned; none has a drop/alter/rename/DELETE/TRUNCATE in `upgrade()`; reviewed in MIGRATION-REVIEW.md | pass |
| Update safety review | static scan + Docker event log of real runs (section 2): only web, worker, the one-off migration container and short probe containers created/removed | pass |
| Rollback safety review | real runs: manual and automatic; no migration, no downgrade, infrastructure untouched | pass |
| No PostgreSQL recreation | same container ID, start time and restart count before/after every run | pass |
| No backup/ClamAV restart | same | pass |
| No `docker volume prune` | not in the script (static scan); no volume create/destroy event in any run | pass |
| No `docker image prune` | not in the script; no image delete event; image list before ⊆ after in every run | pass |
| No secrets in the package | validate-release.sh scan (`.env`, keys, dumps, databases, signatures, setup keys, secret assignments with values) + a search for the simulation's throwaway values | pass (run on the final ZIP) |
| Application tests | earlier full suites: 352 passed on SQLite and on PostgreSQL 16; after the dialog fix, the suites that read the changed front-end files: 83 + 81 passed | pass |
| Dialog fix (browser) | Chromium, 16 checks (section 2.4) | 16/16 pass; the same test fails at step 1 on the old code |

## 2. Simulated production (Docker, fictional data)

A stand-in for the production server was built from the facts given:
- project `tfsc-agreements-legal`, web `legal-agreements` on `127.0.0.1:8766` and on `alphabridge_edge` as
  `tfsc-agreements-legal`, behind a **separate** Caddy container;
- the 5.3 image tagged `tfsc-agreements:20261117-32267ca` and `tfsc-agreements:rollback-20261125`, with
  `tfsc-agreements:4` deliberately pointing at another image;
- backup on `tfsc-postgres:20261110-c803dc9`, ClamAV 1.4.6, the 7 volumes with the production names;
- the production PostgreSQL and application settings, and `DATABASE_URL` with user `crm`;
- no `tfsc-link`;
- one setting the package does not know (`TFSC_MAIL_PRIVATE_HOSTS`).

Data: a signed client (BAA + Services Agreement), an envelope out for signature, an OCS token. Only the image IDs and
the installation path in `prod.conf` were set to the simulation's values.

1. **Preflight** with the package as shipped: **FAILED as it should**, on the unknown variable, for web and worker,
   naming it without its value. Everything else matched (34 web and 39 worker variables, mounts, networks, port,
   settings). With `compose.local.yaml` per the guide: PASSED.
2. **Update 5.3 → 5.4**: copy (verified: 45 tables with data), build, migration `0014_integration_feed → 0015_reminders`,
   switch, web 5.4.0 healthy, reachable through the separate Caddy, worker reporting; `tfsc-link` created. Afterwards:
   - every business row and stored file identical. The only differences were runtime bookkeeping: heartbeats, the feed
     publisher's timestamps, the token's last use, rate-limit counters, and the migration's revision and setting;
   - PostgreSQL, backup and ClamAV: same containers, not restarted. Volumes unchanged; no image removed;
   - OCS checker 39 passed, 0 failed; the feed was readable over `tfsc-link` as `tfsc-agreements-legal`;
   - a signing link emailed before the update was signed after it.
3. **Manual rollback**: back on 5.3 in 9 s, healthy and reachable; database stays at 0015 (5.3 logs "newer than this
   release; starting without migrating"); infrastructure, volumes and images untouched.
4. **Broken release** (web raises at start): migration run, switch, failure recognised, **automatic step back** to
   `tfsc-agreements:rollback-20261125`, healthy; log of the failed start kept. First run: detection took the full 5
   minutes, so early detection was added; second run: the whole attempt took 1 min 22 s. Infrastructure untouched.
5. **Final package**: update again: passed, OCS checker 43 passed, 0 failed, 0 business rows changed.
6. **Negative tests**:
   - `ROLLBACK_IMAGE=tfsc-agreements:4`: preflight FAILED (wrong image ID), rollback refused, release check FAILED;
   - the upstream script at the top of a release: release check FAILED with every whole-stack `up`/`stop`/`restart`,
     the upstream service name, the PostgreSQL build, the image prune, backup deletion and firewall commands;
   - a wrong backup passphrase: verification rejects the copy.
7. **Restore of a pre-update copy** into a throwaway database: complete, revision 0014, counts match. Scratch database
   dropped. All six copies made during testing are still there (none deleted).
8. **No `python3` on the host**: preflight ran its comparison inside the application image instead; same result.

Bugs found in my own production script by these runs, all fixed before the final package:
- verification of the copy failed on a good copy (`pg_restore --list` stops reading early). It now reads the whole
  copy, then the list;
- a broken release took 5 minutes to detect. Now about 1 minute;
- the checker treated joining `tfsc-link` as a failure. Now a warning; only leaving a network fails.

### 2.4 Dialog checks (Chromium, Users & permissions › Create user)

All 16 passed:
1. a click outside does not close it;
2. typed values are kept;
3. pressing inside a field and releasing outside does not close it;
4. × with changes asks the exact question;
5. Cancel keeps the dialog and all values;
6. Escape with changes asks, twice in a row, and never closes silently;
7. confirming via Escape closes;
8. confirming via × closes;
9. × without changes closes without asking;
10. Escape without changes closes without asking;
11. permission changes (the Clear button) count as changes;
12. an empty form is still blocked by validation;
13. Save shows the temporary sign-in details;
14. × after a successful save closes without asking;
15. the help dialog (nothing to fill in) still closes from outside;
16. no script errors.

A double click on Save created **one** user.

## 3. Unresolved: must be verified on the VPS (`agreements.sh preflight` checks most of it)

1. **The real production `compose.yaml` and `.env` were not available.** This package was written from the stated
   facts. The preflight compares the running containers with the new configuration and stops on any difference. Expect
   it to name settings production has that this package does not (as in the simulation); add them to
   `compose.local.yaml` as UPDATE-AND-ROLLBACK.md explains. Do not update until it passes.
2. **Database user**: you stated `crm`; upstream connects as `tfsc`. The compose uses `DATABASE_URL` from the production
   `.env` when it is set there, else the upstream URL. The preflight compares the value; a difference stops it.
3. **Location of the production `.env`**: detected from the running web container's Compose labels, else
   `/opt/tfsc-agreements-legal/.env` or `…/deploy/agreements/.env`; set `ENV_FILE` in prod.conf if the preflight cannot
   find it. Older Compose versions do not record the env-file label (the fallbacks then apply).
4. **Health port**: read from the running web container. If production publishes no 127.0.0.1 port at all, the
   preflight stops; tell support (the compose then needs `ports` removed in `compose.local.yaml`).
5. **Container names**: if production sets `container_name`, the preflight stops; add the same `container_name` in
   `compose.local.yaml`.
6. **OCS address**: this package gives the web service the alias `tfsc-agreements-legal` on `tfsc-link` (never
   `agreements`, which another stack on the server might use). OCS must read
   `http://tfsc-agreements-legal:8000/api/integration/v1`. Check OCS's setting. If the web service already had another
   alias on `tfsc-link`, the preflight reports it as "would be lost" and stops.
7. **Rollback image**: the preflight checks that `tfsc-agreements:rollback-20261125` is `sha256:09eedd0d…` and is
   exactly what runs. If not, nothing proceeds.
8. **PostgreSQL, backup and ClamAV definitions** in this compose may differ from production's in details the update never
   uses (the `initdb` path, `backup-offsite.env`, memory limits). The update never touches these services. Never run
   a whole-stack `docker compose up` with this file; the preflight lists their bind-mounted folders, which must not move.
9. **Building on the VPS** needs Debian and PyPI access (or `BUILD_CA=/path/to/ca.crt` behind an inspecting proxy) and
   about 5 GB free; the preflight checks the space.
10. **Brief interruption**: the switch recreates web and worker (seconds; signers are reconnected).
11. **Overdue reminders**: in the first sending window after the update, 5.4 may send reminders 5.3 would also have
    sent, limited to one automatic email per person per company per day (MIGRATION-REVIEW.md).
12. **OCS dialog fix**: `source/apps/ocs/web/src/design/ui.tsx` is not in this package; the OCS front end was not changed.
13. **Docker Compose version**: tested with Compose 5.7.1. The commands used (`up --no-deps --no-build`, `run --no-deps
    -T`, `config --format json`) exist in Compose v2; confirm with `docker compose version` (shown by the preflight).
