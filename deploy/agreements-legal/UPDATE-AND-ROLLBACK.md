# TFSC Agreements 5.4.0 (package 5.4.0-legal.2) on legal.24-7consultancy.cloud: update and rollback

For **this server only**: project `tfsc-agreements-legal`, web `legal-agreements`, worker `agreements-worker`, database
`tfsc` (superuser `crm`), Caddy managed separately on `alphabridge_edge`, no published host port. Do not use `upstream/`.

The update changes only `legal-agreements` and `agreements-worker` (and creates the network `tfsc-link` if it is
missing). It never touches PostgreSQL, backup, ClamAV, Caddy, the firewall, any volume, the production `.env`, the
rollback image or its tags, or old backups. No `down`, no prune, no database downgrade.

The new containers get **exactly** the environment the running ones have. The script copies every variable from the running web and worker
containers, plus the number of web processes, container names, limits, networks, aliases and mounts, into
`deploy/agreements-legal/state/compose.runtime.yaml`. That file is written on the server only (mode 600), is never
printed and is never part of a package. `DB_POOL_SIZE=5` and every other production value are therefore kept. Variables
production does not set stay unset. Nothing needs to be edited by hand.

## A. Before the update (read-only, about 10 minutes)

1. Optional but recommended: a snapshot of the VPS at your hosting provider.
2. Copy the ZIP to the server and unpack it **beside** the installation. Never unpack it into
   `/opt/tfsc-agreements-legal`, and never move or rename that folder (running containers may use folders inside it):
   ```
   sudo mkdir -p /opt/tfsc-agreements-legal-releases
   cd /opt/tfsc-agreements-legal-releases
   sudo unzip /root/TFSC-Agreements-Legal-5.4.0-legal.2-<date>-<commit>.zip
   cd TFSC-Agreements-Legal-5.4.0-legal.2-<date>-<commit>
   cat VERSION                                   # build 5.4.0-legal.2, and the commit
   ```
3. Check the package on the server:
   ```
   sudo deploy/agreements-legal/validate-release.sh
   ```
   It must end with `RELEASE CHECK PASSED`. It contacts nothing and changes nothing.
4. Run the preflight. It changes nothing; the only things it creates are temporary probe containers, removed again:
   ```
   sudo ./agreements.sh preflight
   ```
   It must end with `PREFLIGHT PASSED`. It shows:
   - Docker Compose (`5.6.0` on this server);
   - the five services running, the images, and that `tfsc-agreements:rollback-20261125` is `sha256:09eedd0d…` and
     exactly what runs now;
   - the 7 volumes and the networks;
   - PostgreSQL answering on database `tfsc` as `crm`, with the production settings, at revision `0014_integration_feed`;
   - that the backup container can make and check the encrypted copy, and that there is room for it and for the image;
   - the web version, asked **inside** its container;
   - the names (never the values) of the variables kept for web and worker;
   - the comparison: environment identical, same volumes, networks and aliases kept (`tfsc-link` joined: warning),
     **no published host port** (unchanged), same container names, memory limits and web processes, `DB_POOL_SIZE = 5`
     for web and worker, and the maximum database connections (unchanged);
   - OCS, read only: whether the OCS API is on `tfsc-link`. Today it is not: a warning (section D);
   - reachability now: Caddy's network → `tfsc-agreements-legal:8000` must answer `200`.

   If it says FAILED, nothing was changed: send the output to support. Do not edit the scripts.

## B. The update (10–20 minutes; the site keeps running until the switch, which takes seconds)

```
sudo ./agreements.sh update
```
Answer `y`. In order:
1. repeats the preflight; stops on any failure;
2. writes `state/compose.runtime.yaml` from the running web and worker (mode 600);
3. creates `tfsc-link` only if it does not exist;
4. encrypted copy of database `tfsc` in the running **backup** container: `/backups/pre-update-<UTC>.dump.enc`
   (pg_dump custom format → AES-256-CBC, PBKDF2, the backup passphrase). Verified by decrypting the whole copy into
   `pg_restore` (every block read, output discarded) and checking the table list for `alembic_version`, `users`,
   `clients`, `esign_envelopes`, `esign_recipients`, `esign_events`, `documents`. No readable copy is written. If this
   fails, nothing has changed;
5. builds **only** `tfsc-agreements:<date>-<commit>` (PostgreSQL is not built or pulled);
6. applies migration `0015_reminders` (one transaction) from the new image, in a one-off container with the web
   service's settings, while the current version keeps serving. If it fails, PostgreSQL undoes it and nothing is switched;
7. `docker compose up -d --no-deps --no-build legal-agreements agreements-worker`. No other service is touched;
8. checks, without any host port:
   - the web container is healthy **and** reports version 5.4.0 when asked inside the container;
   - Caddy's network reaches `tfsc-agreements-legal:8000`;
   - the feed is still reachable on `tfsc-link` if it was before;
   - the worker's email and reminder heartbeats are newer than its start;
9. if any check fails (a start-up error is recognised after about 1 minute; otherwise up to 5 minutes for web and 7 for
   the worker): switches web and worker back to `tfsc-agreements:rollback-20261125` automatically, with the same
   settings, and checks it the same way. The database stays at 0015, which 5.3 runs on. A log of the failed start goes
   to `state/`;
10. confirms PostgreSQL, backup and ClamAV are the same containers, never restarted.

Everything is logged in `deploy/agreements-legal/state/update-<UTC>.log` (no secret values).

## C. After the update

```
sudo ./agreements.sh status        # versions (asked inside the container), Caddy path, revision 0015_reminders, copies
```
Sign in at https://legal.24-7consultancy.cloud. An envelope out for signature shows the **Reminders** card; Admin ›
Reminders lists Standard, Gentle, Firm and Off. Keep `tfsc-agreements:rollback-20261125` and the new image (no prune).

## D. The separate OCS change (OCS stack; not part of this update)

Follow `OCS-TFSC-LINK.md`: in the OCS compose file, put the OCS API service on `tfsc-link`, keeping every network it has
now, then `docker compose up -d --no-deps <ocs-api-service>` in the OCS folder. That recreates the OCS API container only
(a few seconds). Do it after section B, because the update creates `tfsc-link` and puts Legal on it. Check: the OCS API
gets `401` from `http://tfsc-agreements-legal:8000/api/integration/v1/meta` without a token; `sudo ./agreements.sh
preflight` in the Legal folder shows the OCS container "on tfsc-link".

## E. Rollback

```
cd /opt/tfsc-agreements-legal-releases/TFSC-Agreements-Legal-5.4.0-legal.2-<date>-<commit>
sudo ./agreements.sh rollback
```
- It checks that `tfsc-agreements:rollback-20261125` exists and is `sha256:09eedd0d…`.
- It takes the settings from the running containers (or from `state/compose.runtime.yaml` if they are not running).
- It switches **only** `legal-agreements` and `agreements-worker`, and checks them as in B.8.
- It runs no migration and no downgrade. It does not touch PostgreSQL, backup, ClamAV, Caddy, volumes, files, contracts
  or signatures. The 5.4 image is kept.
- 5.3 runs on the 0015 database. Its log says "Database revision 0015_reminders is newer than this release; starting
  without migrating".
- Data written while 5.4 ran stays (5.3 ignores the new tables and columns).

To go forward again later: `sudo ./agreements.sh update` from the same folder (the copy and build are repeated; the
migration is already done).

## F. Restoring the pre-update copy (last resort; the owner decides)

This replaces database `tfsc` with the copy: **everything after the copy is lost**. Only with the application stopped
and only if support asks for it:
```
P=tfsc-agreements-legal
W=$(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=legal-agreements)
K=$(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=agreements-worker)
B=$(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=backup)
docker stop $W $K
docker exec $B ls -l /backups/                     # choose pre-update-YYYYMMDDTHHMMSSZ.dump.enc
docker exec $B sh -c 'set -o pipefail; openssl enc -d -aes-256-cbc -pbkdf2 -pass env:PGBACKREST_REPO1_CIPHER_PASS -in /backups/pre-update-YYYYMMDDTHHMMSSZ.dump.enc | pg_restore -h /var/run/postgresql -U crm -d tfsc --clean --if-exists'
```
then `sudo ./agreements.sh rollback` (the copy is at revision 0014, the 5.3 schema). Never run an Alembic downgrade.

## G. If something does not start

Nothing is deleted by any step. Collect `sudo ./agreements.sh status`, `sudo ./agreements.sh logs`, the files in
`deploy/agreements-legal/state/` (except `compose.runtime.yaml`, which holds secrets), and send them to support.
