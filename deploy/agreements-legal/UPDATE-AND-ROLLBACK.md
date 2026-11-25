# TFSC Agreements 5.4 on legal.24-7consultancy.cloud: update and rollback

This package is the production adaptation of release 5.4 for **this server only** (project `tfsc-agreements-legal`, web
service `legal-agreements`, Caddy managed separately). Do not use the upstream instructions in `upstream/`.

The update changes only `legal-agreements` and `agreements-worker`. It never touches PostgreSQL, the backup service,
ClamAV, any volume, Caddy, the firewall or the production `.env`. It removes nothing: no prune, no `down`, no old backup.
Nothing has been run on the server yet. Every step below is for the owner or their administrator to run.

## Before the update (about 10 minutes, nothing changes)

1. Take a normal server snapshot at your hosting provider if you have that option (independent of everything below).
2. Copy the ZIP to the server and unpack it **next to** the running installation. Never into it, never moving it: the
   PostgreSQL, backup and ClamAV containers may use folders inside `/opt/tfsc-agreements-legal` and need them to start.
   ```
   sudo mkdir -p /opt/tfsc-agreements-legal-releases && cd /opt/tfsc-agreements-legal-releases
   sudo unzip /path/to/TFSC-Agreements-Legal-5.4-*.zip
   cd TFSC-Agreements-Legal-5.4-*
   ```
3. Check the package on the server (read-only):
   ```
   sudo deploy/agreements-legal/validate-release.sh
   ```
   It must end with `RELEASE CHECK PASSED`.
4. Run the preflight (read-only; compares the running containers with the new configuration, never prints secrets):
   ```
   sudo ./agreements.sh preflight
   ```
   It must end with `PREFLIGHT PASSED`. Read every `WARN` line. The most likely `FAIL` lines, and what to do:
   - *environment variable X is set on the running container but not in the new configuration*: production passes a
     setting this package does not know. Create `deploy/agreements-legal/compose.local.yaml` (server-only, never part of
     a release) with that variable for the service named in the message, taking the value from your production `.env`:
     ```
     services:
       legal-agreements:
         environment:
           X: ${X}
     ```
     then run the preflight again. Do not copy secret values into the file; refer to the `.env` with `${X}`.
   - *container name / network alias / published port / mounts differ*: the running service is defined differently
     from what this package assumes. Put the running definition in `compose.local.yaml` (for example `container_name:`),
     or stop and send the preflight output to support.
   - *the production .env was not found*: set `ENV_FILE=` in `deploy/agreements-legal/prod.conf` to its path.
   - *rollback image ... is not ROLLBACK_IMAGE_ID*: do not continue; the rollback image is not the verified one.

## The update (10–20 minutes; the site keeps running until the switch, which takes seconds)

```
sudo ./agreements.sh update
```
In order, it:
1. repeats the preflight and checks that `tfsc-agreements:rollback-20261125` exists and is exactly the image running now
   (`sha256:09eedd0d…`), and that PostgreSQL runs and is healthy;
2. creates the network `tfsc-link` only if it is missing (nothing else is created or changed);
3. writes an encrypted copy of database `tfsc` in the running **backup** container to
   `/backups/pre-update-<UTC time>.dump.enc` (pg_dump custom format, AES-256-CBC with PBKDF2 and the backup passphrase,
   the same scheme the upstream script uses), then **verifies** it: decrypts it in memory into `pg_restore --list` and
   checks that the tables `alembic_version`, `users`, `clients`, `esign_envelopes`, `esign_recipients`, `esign_events`
   and `documents` have data. No readable copy is written anywhere. If the copy fails, nothing changes;
4. builds the application image `tfsc-agreements:<date>-<commit>` (only this image; PostgreSQL is never built);
5. applies the database change (0015) from the new image in a one-off container, as one transaction, while the current
   version keeps serving. If it fails, PostgreSQL undoes it and nothing is switched;
6. switches only `legal-agreements` and `agreements-worker` to the new image
   (`docker compose up -d --no-deps --no-build legal-agreements agreements-worker`);
7. waits for the web service to be healthy **and** to answer as version 5.4.0 on its 127.0.0.1 port, to be reachable on
   `alphabridge_edge` as `tfsc-agreements-legal` (the path Caddy uses), and the integration feed to stay reachable on
   `tfsc-link` if it was before; then for the worker to report email and reminder work since it started;
8. if any of that fails within 5 (web) or 7 (worker) minutes: switches both back to `tfsc-agreements:rollback-20261125`
   automatically, checks that version is healthy, and keeps a log of the failed start in `deploy/agreements-legal/state/`.
   The database keeps the 0015 additions (5.3 ignores them); nothing is downgraded or deleted;
9. confirms that the PostgreSQL, backup and ClamAV containers are the same containers, never restarted.

The whole run is logged in `deploy/agreements-legal/state/update-<time>.log` (no secrets are printed).

## After the update

- Sign in at https://legal.24-7consultancy.cloud, open an envelope out for signature: the new **Reminders** card shows.
  Admin › Reminders shows the four rules. `sudo ./agreements.sh status` shows versions, revision `0015_reminders` and the
  pre-update copy.
- OCS: if OCS reads the feed over `tfsc-link`, its address is `http://tfsc-agreements-legal:8000/api/integration/v1`
  (this package gives the web service that name on `tfsc-link`; see "Unresolved" in VALIDATION-REPORT.md).
- Keep `tfsc-agreements:rollback-20261125` and the new image. Do not prune images.
- For the next release: set `CURRENT_IMAGE`, `CURRENT_IMAGE_ID`, `ROLLBACK_IMAGE` and `ROLLBACK_IMAGE_ID` in `prod.conf` to the
  image that runs then (tag it first, for example `docker tag tfsc-agreements:<date>-<commit> tfsc-agreements:rollback-<date>`),
  and add `0015_reminders` reviewed (already in `KNOWN_MIGRATIONS`).

## Rollback (by hand, any time after the update)

```
sudo ./agreements.sh rollback
```
It checks that `tfsc-agreements:rollback-20261125` exists and is the verified image (`sha256:09eedd0d…`), switches
**only** `legal-agreements` and `agreements-worker` to it, and checks it is healthy and reachable for Caddy. It does not
run any migration or downgrade, does not touch PostgreSQL, backup or ClamAV, and deletes no file, contract, signature or
upload. 5.3 runs on the 0015 database (it logs that the database is newer and starts). The 5.4 image is kept, so you can
run `sudo ./agreements.sh update` from this folder again later (the copy and the build are repeated; the migration is
already done and is skipped).

What a rollback does not undo: data written while 5.4 ran stays (new reminder rules, do-not-remind entries, the new
columns' values); emails 5.4 already sent were sent. 5.3 ignores all of it.

## Restoring the pre-update copy (last resort, owner's decision only)

This replaces the whole `tfsc` database with the copy taken before the update: **everything done after the copy is
lost** (signatures, clients, emails recorded). Only if support asks for it, with the application stopped:

```
P=tfsc-agreements-legal
docker compose -p $P ls                                   # the project must be listed
docker stop $(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=legal-agreements) \
            $(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=agreements-worker)
B=$(docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service=backup)
docker exec $B ls -l /backups/                            # choose the pre-update-....dump.enc
docker exec $B sh -c 'set -o pipefail; openssl enc -d -aes-256-cbc -pbkdf2 -pass env:PGBACKREST_REPO1_CIPHER_PASS -in /backups/pre-update-YYYYMMDDTHHMMSSZ.dump.enc | pg_restore -h /var/run/postgresql -U crm -d tfsc --clean --if-exists'
```
then start the application version that matches the restored copy (5.3: `sudo ./agreements.sh rollback`). Never run an
Alembic downgrade instead.

## If the rollback does not start

Nothing has been deleted. Collect, without changing anything:
`sudo ./agreements.sh status`, `sudo ./agreements.sh logs`, the files in `deploy/agreements-legal/state/`, and
`docker inspect` of the `legal-agreements` container, and send them to support. PostgreSQL and the data are untouched.
