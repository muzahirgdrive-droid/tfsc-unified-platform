# Production adaptation: TFSC Agreements on legal.24-7consultancy.cloud

**Permanent document.** It records how this server differs from the upstream single-server layout and what every
future release must keep. These requirements stay in force for every release unless the owner explicitly authorizes a
change. The machine-readable form of the same facts is `prod.conf`; `validate-release.sh` checks a release against them.

## 1. The server

| | Production | Upstream release assumes |
|---|---|---|
| Address | https://legal.24-7consultancy.cloud | its own domain |
| Installation folder | `/opt/tfsc-agreements-legal` (must not be moved or renamed: running containers may bind-mount folders in it) | `/opt/tfsc-agreements`, renamed on every update |
| Compose project | `tfsc-agreements-legal` | `tfsc-agreements` |
| Web service | `legal-agreements` | `agreements` |
| Worker | `agreements-worker` | same |
| Database / backup / scanner services | `postgres`, `backup`, `clamav` | same names |
| Database | `tfsc`; superuser `crm` (sockets) | same |
| Proxy | the server's own Caddy, managed separately, reaching `tfsc-agreements-legal:8000` on network `alphabridge_edge` | a bundled Caddy (`edge`) on ports 80/443 |
| OCS feed network | `tfsc-link` (external; created if missing; private) | same, but the web service is reached as `agreements` |
| Volumes | external, exact names `tfsc-agreements-legal_{postgres-data,backups,pg-socket,restore-scratch,clamav-db,agreements-data,contracts}` | created by Compose |
| Application image | `tfsc-agreements:<date>-<commit>`; rollback `tfsc-agreements:rollback-<date>` | `tfsc-agreements:4` and `:rollback` |
| PostgreSQL image | `tfsc-postgres:16` (never rebuilt by an update) | rebuilt on every update |
| Backup image | `tfsc-postgres:20261110-c803dc9` | `tfsc-postgres:16` |
| ClamAV image | `clamav/clamav:1.4.6` | same |
| PostgreSQL settings | `max_connections=100`, `shared_buffers=256MB`, `effective_cache_size=4GB`, `archive_mode=on` (pgBackRest) | 200 / 3GB / 9GB |
| Application settings | `WEB_WORKERS=2`, `WEB_THREADS=8`, `DB_POOL_SIZE=8`, `DB_POOL_OVERFLOW=4`, `TFSC_GENERATION_WORKERS=1`, `TFSC_COMPLETION_WORKERS=1` | sized for 16 GB |
| Health port | the web service's existing `127.0.0.1:<port>` (read from the running container; never published wider) | `127.0.0.1:8765` |

**On this server `tfsc-agreements:4` and `tfsc-agreements:rollback` may point at other images.** Production never uses
them. The verified rollback image for the 5.3 → 5.4 update is `tfsc-agreements:rollback-20261125`
(`sha256:09eedd0d477a5e6245601cc5596d793d6a61654e4b062dc03a6fb2b208749c8b`, the image of `tfsc-agreements:20261117-32267ca`).

## 2. What may never happen (on update, rollback or any other command)

Delete or recreate a volume; `docker volume prune`, `docker image prune`, `docker system prune`; `docker compose down`;
a whole-stack `docker compose up -d`, `restart`, `stop` or `start`; `--remove-orphans`; reset or initialize the
database; drop a table or column; delete records; change an encryption key or a password; build, pull or recreate
PostgreSQL, backup or ClamAV; change the Caddy configuration or start a bundled proxy; change the firewall; delete old
backups or pre-update copies; downgrade the database automatically; print or package a secret.

## 3. How the adaptation is built (keep this structure)

```
agreements.sh                      production script (copy of deploy/agreements-legal/agreements.sh)
deploy/agreements-legal/
  prod.conf                        production facts, no secrets
  compose.yaml                     production compose (external volumes/networks, no proxy, no build)
  compose.local.yaml               (server only, optional, never shipped) settings found only on the server
  legalcheck.py                    compares the new config with the running containers; checks a release
  validate-release.sh              the release check (run it on every new release, on any machine)
  UPDATE-AND-ROLLBACK.md, MIGRATION-REVIEW.md, CHANGELOG-LEGAL.md, VALIDATION-REPORT.md
deploy/agreements/initdb/          upstream, unchanged (first start of an empty database only)
upstream/                          upstream deployment files, renamed (compose.upstream.yaml, agreements.upstream.sh, ...)
apps/, packages/, infra/           the release, unchanged
```
Upstream files are never edited for production; production files never live inside upstream folders. The package is
built with `tools/make_agreements_legal_bundle.sh` from the repository branch, which refuses to build unless
`validate-release.sh` passes.

### What the production script does differently from upstream, and why

| Upstream `agreements.sh` | Production `agreements.sh` | Why |
|---|---|---|
| service `agreements` in `exec`, `run`, `logs`, `print_key`, `signatures`, `wait_ready`, `migrate_first`, `rollback` | `$WEB` = `legal-agreements` everywhere; infrastructure reached with `docker exec` on the running container found by Compose labels | the production service name |
| `docker compose up -d` (whole stack) in install, update, rollback, start | `up -d --no-deps --no-build legal-agreements agreements-worker` only | never recreate PostgreSQL, backup, ClamAV |
| builds `tfsc-postgres:16` on every update | builds only the application image | PostgreSQL must not be rebuilt |
| rollback tag `tfsc-agreements:rollback` = whatever ran as `:4` | `ROLLBACK_IMAGE` from prod.conf, verified by image ID before any change | `:4`/`:rollback` are other images here |
| pre-update copy with `pg_dump` in the postgres container, deletes all but the 2 newest copies | copy in the backup container, verified (`openssl -d` → `pg_restore --list`, key tables present), **nothing deleted** | never delete restore points |
| `docker image prune -f` after an update | none | keep every image for rollback |
| `restart`/`stop`/`start`/`install` act on the whole stack, open the firewall, start Caddy | not available (`restart-app` restarts web and worker only) | |
| `ensure_env_keys`/`tune_env` append to `.env` | `.env` is only read; its path is detected from the running container's Compose labels | production settings stay as they are |
| health: web answers + worker not unhealthy | web healthy **and** answering with the new version, reachable on `alphabridge_edge` as `tfsc-agreements-legal`, the feed still reachable on `tfsc-link`; worker heartbeats newer than its start | a stale heartbeat file or a lost Caddy alias would otherwise pass |
| no comparison with what runs | `preflight`: every environment variable (values compared, not shown), mount, network, alias, port and container name of the running web and worker must stay the same | the production compose was never part of a release |

## 4. Adapting the next release (checklist)

1. Unpack the new upstream release ZIP (or check out its commit) and copy `deploy/agreements-legal/` from the last
   production package, or from the repository branch, which already has it.
2. Run `deploy/agreements-legal/validate-release.sh <release-folder>` on any machine with Docker. It reports:
   wrong service names or the upstream `agreements` service name in the script; forbidden or old rollback tags;
   whole-stack operations; prune/down/volume removal; a PostgreSQL build; volume or network changes; a proxy service;
   ports beyond 127.0.0.1; changed production images or settings; **environment variables or volumes the upstream
   compose added that production lacks**; new migrations (not in `KNOWN_MIGRATIONS`) and destructive operations in
   them; files that must never ship.
3. For each upstream change it reports: add the new variable/volume/mount to `deploy/agreements-legal/compose.yaml`
   (production values, not upstream sizing), review new migrations into `MIGRATION-REVIEW.md` and add them to
   `KNOWN_MIGRATIONS`, port any new upstream script behaviour into the production script without whole-stack commands.
4. Update `prod.conf` image facts (current image, its ID, the rollback tag and its ID) from the server:
   `docker inspect -f '{{.Config.Image}} {{.Image}}' $(docker ps -q --filter label=com.docker.compose.service=legal-agreements)`.
5. Build the package with `tools/make_agreements_legal_bundle.sh`; on the server run `validate-release.sh`, then
   `agreements.sh preflight`, then `agreements.sh update`.

### After a successful update

The image that now runs becomes the next rollback point: `docker tag <new image> tfsc-agreements:rollback-<date>`, then
record it and its ID in `prod.conf` (`CURRENT_IMAGE*`, `ROLLBACK_IMAGE*`) for the next release. Keep the previous rollback
image until the next update has been running without problems.

## 5. Things only the server can confirm (checked by `preflight`, listed in VALIDATION-REPORT.md)

The production compose and `.env` were never part of a release. This package was written from the facts above.
`agreements.sh preflight` compares everything that matters with the running containers before anything changes, and
stops on any difference that would alter environment variables, mounts, networks, aliases, ports or container names.
