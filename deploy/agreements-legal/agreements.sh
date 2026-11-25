#!/usr/bin/env bash
# TFSC Agreements - PRODUCTION script for legal.24-7consultancy.cloud (project tfsc-agreements-legal).
# Production adaptation of the upstream agreements.sh: see PRODUCTION-ADAPTATION.md. Facts: deploy/agreements-legal/prod.conf.
#
#   sudo ./agreements.sh preflight     read-only: everything an update relies on, compared with the running containers
#   sudo ./agreements.sh update        backup (encrypted, verified), build the new application image, migrate, switch
#                                      legal-agreements and agreements-worker only; back to the previous image if unhealthy
#   sudo ./agreements.sh rollback      back to ROLLBACK_IMAGE (web and worker only; the database is not downgraded)
#   ./agreements.sh help               every command
#
# Never touched by this script: PostgreSQL, backup and ClamAV containers, volumes, the Caddy configuration, the firewall,
# the production .env (read only) and old backups. No prune, no "down", no whole-stack "up".
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
while [ ! -f "$ROOT/deploy/agreements-legal/prod.conf" ]; do
  [ "$ROOT" = / ] && { echo "deploy/agreements-legal/prod.conf not found above $(dirname "${BASH_SOURCE[0]}")" >&2; exit 1; }
  ROOT=$(dirname "$ROOT")
done
LEGAL_DIR=$ROOT/deploy/agreements-legal
# shellcheck source=deploy/agreements-legal/prod.conf
. "$LEGAL_DIR/prod.conf"
STATE_DIR=$LEGAL_DIR/state
YES=0
WORK=''

say() { printf '\n== %s\n' "$*"; }
info() { printf '   %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
confirm() { [ "$YES" = 1 ] && return 0; [ -t 0 ] || return 1; local a; read -r -p "$1 [y/N]: " a; case "$a" in y|Y|yes|YES) return 0;; *) return 1;; esac; }
need_root() { [ "$(id -u)" = 0 ] || die "Run as root: sudo $0 $1"; }
work() { [ -n "$WORK" ] || { WORK=$(mktemp -d /tmp/tfsc-legal.XXXXXX); chmod 700 "$WORK"; trap 'rm -rf "$WORK"' EXIT; }; }

# ------------------------------------------------------------------ the running installation
cid() {   # container id of a service of this project (running or not)
  docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$1" | head -n 1
}
running_cid() { docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$1" | head -n 1; }
health_of() { docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$1" 2>/dev/null || echo missing; }
image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }

detect_env_file() {   # the production .env: prod.conf, then the running web container's Compose labels, then PROD_DIR
  [ -n "${ENV_FILE:-}" ] && { [ -f "$ENV_FILE" ] || die "ENV_FILE=$ENV_FILE (prod.conf) does not exist."; return; }
  local c f wd; c=$(cid "$WEB")
  if [ -n "$c" ]; then
    f=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.environment_file"}}' "$c" 2>/dev/null || true)
    f=${f%%,*}
    if [ -n "$f" ] && [ -f "$f" ]; then ENV_FILE=$f; return; fi
    wd=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c" 2>/dev/null || true)
    if [ -n "$wd" ] && [ -f "$wd/.env" ]; then ENV_FILE=$wd/.env; return; fi
  fi
  for f in "$PROD_DIR/deploy/agreements/.env" "$PROD_DIR/.env"; do [ -f "$f" ] && { ENV_FILE=$f; return; }; done
  die "The production .env was not found. Set ENV_FILE in $LEGAL_DIR/prod.conf (it is read, never changed)."
}

env_has() { grep -q "^$1=." "$ENV_FILE"; }
env_val() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'\$/\1/; s/^\"\(.*\)\"\$/\1/"; }   # non-secret keys only

health_port() {   # the 127.0.0.1 port the running web container publishes; kept as it is
  local c p; c=$(cid "$WEB")
  if [ -n "$c" ]; then
    p=$(docker inspect -f '{{range $k, $v := .HostConfig.PortBindings}}{{if eq $k "8000/tcp"}}{{range $v}}{{.HostPort}}{{end}}{{end}}{{end}}' "$c" 2>/dev/null || true)
    [ -n "$p" ] && { echo "$p"; return; }
  fi
  [ -f "$STATE_DIR/health-port" ] && { cat "$STATE_DIR/health-port"; return; }
  die "Could not read the web container's 127.0.0.1 port. Is $WEB running?"
}

active_image() {   # the image the web service runs now (name as the container records it)
  local c; c=$(cid "$WEB")
  if [ -n "$c" ]; then docker inspect -f '{{.Config.Image}}' "$c"; return; fi
  [ -f "$STATE_DIR/active-image" ] && { cat "$STATE_DIR/active-image"; return; }
  echo "$CURRENT_IMAGE"
}

dc() {   # docker compose for this project only, with a clean environment (the shell cannot change production settings)
  local files=(-f "$LEGAL_DIR/compose.yaml")
  [ -f "$LEGAL_DIR/compose.local.yaml" ] && files+=(-f "$LEGAL_DIR/compose.local.yaml")
  env -i PATH="$PATH" HOME="${HOME:-/root}" ${DOCKER_HOST:+DOCKER_HOST="$DOCKER_HOST"} ${DOCKER_CONFIG:+DOCKER_CONFIG="$DOCKER_CONFIG"} \
    TFSC_AGREEMENTS_IMAGE="${APP_IMAGE:-$(active_image)}" TFSC_HEALTH_PORT="${HEALTH_PORT:-$(health_port)}" \
    docker compose -p "$PROJECT" --project-directory "$LEGAL_DIR" "${files[@]}" --env-file "$ENV_FILE" "$@"
}

pyrun() {   # python3 on the server, or the running application image (it has Python) with no network
  if command -v python3 >/dev/null 2>&1; then (cd "$WORK" && python3 "$@"); return; fi
  docker run --rm --network none -u 0 -v "$WORK:/w" -w /w --entrypoint python "$(active_image)" "$@"
}

new_tag() {   # tfsc-agreements:<build date>-<commit> from the release's VERSION file
  local commit built
  commit=$(sed -n 's/^commit //p' "$ROOT/VERSION" 2>/dev/null | cut -c1-7)
  built=$(sed -n 's/^built //p' "$ROOT/VERSION" 2>/dev/null | cut -c1-10 | tr -d '-')
  [ -n "$commit" ] && [ -n "$built" ] || die "VERSION is missing or incomplete in $ROOT (it names the release commit)."
  echo "$APP_REPO:$built-$commit"
}
new_version() { sed -n "s/^VERSION='\([0-9.]*\)'.*/\1/p" "$ROOT/apps/agreements/portal.py" | head -n 1; }

web_health() {   # prints the version the web answers with on its 127.0.0.1 port
  curl -fsS -m 8 -H "Host: $DOMAIN" "http://127.0.0.1:$(health_port)/api/health" 2>/dev/null | sed -n 's/.*"version":"\([^"]*\)".*/\1/p'
}

probe() {   # probe NETWORK PATH: what the server's Caddy (alphabridge_edge) or OCS (tfsc-link) gets from the web service
  docker run --rm --network "$1" --entrypoint python "${APP_IMAGE:-$(active_image)}" -c "
import sys,urllib.request as u,urllib.error as e
try:
    r=u.urlopen(u.Request('http://$EDGE_ALIAS:8000$2',headers={'Host':'$DOMAIN'}),timeout=8); print(r.status)
except e.HTTPError as x: print(x.code)
except Exception as x: print('unreachable')" 2>/dev/null || echo unreachable
}

# ------------------------------------------------------------------ preflight (read-only)
PRE_FAIL=0
pf_ok() { printf '  ok    %s\n' "$*"; }
pf_warn() { printf '  WARN  %s\n' "$*"; }
pf_fail() { printf '  FAIL  %s\n' "$*"; PRE_FAIL=1; }

cmd_preflight() {
  need_root preflight
  work
  PRE_FAIL=0
  say "Preflight for $DOMAIN (project $PROJECT) - read-only, nothing is changed"
  docker compose version >/dev/null 2>&1 && pf_ok "docker compose $(docker compose version --short 2>/dev/null)" || pf_fail "docker compose is not available"
  local s c st
  for s in "$WEB" "$WORKER" "$DB" "$BACKUP" "$CLAMAV"; do
    c=$(running_cid "$s")
    if [ -z "$c" ]; then pf_fail "service $s: no running container in project $PROJECT"; continue; fi
    st=$(health_of "$c")
    case "$st" in running\ healthy|running\ no-healthcheck|running\ starting) pf_ok "service $s running ($st, $(docker inspect -f '{{.Name}}' "$c" | tr -d /))";;
      *) if [ "$s" = "$DB" ]; then pf_fail "service $s is $st"; else pf_warn "service $s is $st"; fi;; esac
  done
  # images
  local wc rid aid; wc=$(running_cid "$WEB")
  if [ -n "$wc" ]; then
    aid=$(docker inspect -f '{{.Image}}' "$wc"); info "web runs $(docker inspect -f '{{.Config.Image}}' "$wc") ($aid)"
    [ "$aid" = "$CURRENT_IMAGE_ID" ] && pf_ok "the running web image is the verified one (CURRENT_IMAGE_ID)" \
      || pf_warn "the running web image $aid is not CURRENT_IMAGE_ID in prod.conf (already updated, or prod.conf is out of date)"
    rid=$(image_id "$ROLLBACK_IMAGE")
    if [ -z "$rid" ]; then pf_fail "rollback image $ROLLBACK_IMAGE does not exist"
    elif [ "$rid" != "$ROLLBACK_IMAGE_ID" ]; then pf_fail "rollback image $ROLLBACK_IMAGE is $rid, not ROLLBACK_IMAGE_ID $ROLLBACK_IMAGE_ID"
    elif [ "$rid" != "$aid" ]; then pf_warn "rollback image $ROLLBACK_IMAGE is not the image running now ($aid); a rollback returns to $ROLLBACK_IMAGE"
    else pf_ok "rollback image $ROLLBACK_IMAGE exists and is exactly the running image"; fi
    [ "$(docker inspect -f '{{.Image}}' "$(running_cid "$WORKER")" 2>/dev/null)" = "$aid" ] && pf_ok "worker runs the same image as web" || pf_warn "worker runs a different image than web"
  fi
  local img; for img in "$POSTGRES_IMAGE" "$BACKUP_IMAGE" "$CLAMAV_IMAGE"; do
    [ -n "$(image_id "$img")" ] && pf_ok "image $img present" || pf_warn "image $img not present (only matters if that service is ever re-created)"
  done
  for s in "$DB" "$BACKUP" "$CLAMAV"; do
    c=$(running_cid "$s"); [ -n "$c" ] || continue
    local want; case "$s" in "$DB") want=$POSTGRES_IMAGE;; "$BACKUP") want=$BACKUP_IMAGE;; *) want=$CLAMAV_IMAGE;; esac
    [ "$(docker inspect -f '{{.Config.Image}}' "$c")" = "$want" ] && pf_ok "$s runs $want" || pf_warn "$s runs $(docker inspect -f '{{.Config.Image}}' "$c"), prod.conf says $want"
  done
  # volumes and networks
  local v n; for v in $VOLUMES; do docker volume inspect "$v" >/dev/null 2>&1 && pf_ok "volume $v" || pf_fail "volume $v does not exist"; done
  for n in "$DEFAULT_NETWORK" "$EDGE_NETWORK"; do docker network inspect "$n" >/dev/null 2>&1 && pf_ok "network $n" || pf_fail "network $n does not exist"; done
  docker network inspect "$LINK_NETWORK" >/dev/null 2>&1 && pf_ok "network $LINK_NETWORK" || pf_warn "network $LINK_NETWORK does not exist yet (the update creates it; nothing else changes)"
  if [ -n "$wc" ]; then
    docker inspect -f "{{json .NetworkSettings.Networks}}" "$wc" | grep -q "\"$EDGE_NETWORK\"" && pf_ok "web is on $EDGE_NETWORK" || pf_fail "web is not on $EDGE_NETWORK"
    docker inspect -f "{{with index .NetworkSettings.Networks \"$EDGE_NETWORK\"}}{{json .Aliases}}{{end}}" "$wc" | grep -q "\"$EDGE_ALIAS\"" \
      && pf_ok "web has the alias $EDGE_ALIAS on $EDGE_NETWORK" || pf_fail "web has no alias $EDGE_ALIAS on $EDGE_NETWORK"
  fi
  # the production .env (names only)
  detect_env_file; pf_ok "production settings: $ENV_FILE (read only)"
  local k; for k in TFSC_DOMAIN POSTGRES_PASSWORD BACKUP_PASSPHRASE TFSC_SECRET_KEY; do env_has "$k" && pf_ok "$k is set" || pf_fail "$k is not set in $ENV_FILE"; done
  [ "$(env_val TFSC_DOMAIN)" = "$DOMAIN" ] && pf_ok "TFSC_DOMAIN is $DOMAIN" || pf_fail "TFSC_DOMAIN in $ENV_FILE is not $DOMAIN"
  for k in TFSC_AGREEMENTS_IMAGE TFSC_HEALTH_PORT; do if env_has "$k"; then pf_warn "$k is set in $ENV_FILE; agreements.sh sets it itself (the .env value is ignored)"; fi; done
  # PostgreSQL
  local dbc; dbc=$(running_cid "$DB")
  if [ -n "$dbc" ]; then
    docker exec "$dbc" pg_isready -q -U "$DB_SUPERUSER" -d crm && pf_ok "PostgreSQL accepts connections" || pf_fail "PostgreSQL does not accept connections"
    local item got
    for item in $PG_EXPECT; do
      got=$(docker exec -u postgres "$dbc" psql -U "$DB_SUPERUSER" -d crm -Atc "SHOW ${item%%=*}" 2>/dev/null || true)
      [ "$got" = "${item#*=}" ] && pf_ok "PostgreSQL ${item%%=*} = $got" || pf_warn "PostgreSQL ${item%%=*} = ${got:-?} (expected ${item#*=})"
    done
    info "database revision: $(db_revision "$dbc")"
  fi
  # the backup container can make and check the encrypted copy
  local bc; bc=$(running_cid "$BACKUP")
  if [ -n "$bc" ]; then
    docker exec "$bc" sh -c 'command -v pg_dump && command -v pg_restore && command -v openssl && command -v sha256sum' >/dev/null 2>&1 \
      && pf_ok "backup container has pg_dump, pg_restore, openssl, sha256sum" || pf_fail "backup container lacks pg_dump, pg_restore, openssl or sha256sum"
    docker exec "$bc" sh -c "[ -n \"\${$BACKUP_PASS_ENV:-}\" ]" && pf_ok "backup container has the backup passphrase ($BACKUP_PASS_ENV, value not shown)" || pf_fail "backup container has no $BACKUP_PASS_ENV"
    docker exec "$bc" sh -c "psql -X -h '$DB_SOCKET' -U '$DB_SUPERUSER' -d '$DB_NAME' -Atc 'select 1'" >/dev/null 2>&1 && pf_ok "backup container reaches database $DB_NAME over the socket" || pf_fail "backup container cannot reach database $DB_NAME over $DB_SOCKET"
    docker exec "$bc" sh -c 'test -w /backups' && pf_ok "/backups is writable in the backup container" || pf_fail "/backups is not writable in the backup container"
    local size_kb free_kb
    size_kb=$(docker exec "$bc" sh -c "psql -X -h '$DB_SOCKET' -U '$DB_SUPERUSER' -d '$DB_NAME' -Atc \"select pg_database_size('$DB_NAME')/1024\"" 2>/dev/null | tr -d '[:space:]' || true)
    free_kb=$(docker exec "$bc" sh -c "df -Pk /backups | awk 'NR==2 {print \$4}'" 2>/dev/null | tr -d '[:space:]' || true)
    if [ -n "$size_kb" ] && [ -n "$free_kb" ]; then
      [ "$free_kb" -ge $((size_kb + 1048576)) ] && pf_ok "room for the copy: database $((size_kb / 1024)) MB, free $((free_kb / 1024)) MB" \
        || pf_fail "not enough room in /backups: database $((size_kb / 1024)) MB, free $((free_kb / 1024)) MB"
    fi
  fi
  local dfree; dfree=$(df -Pk "$(docker info -f '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)" 2>/dev/null | awk 'NR==2 {print $4}' || true)
  [ "${dfree:-0}" -ge 5242880 ] && pf_ok "room for the new image: $((dfree / 1048576)) GB free" || pf_fail "less than 5 GB free for Docker images"
  # bind mounts of the services this script never touches (their source folders must stay where they are)
  for s in "$DB" "$BACKUP" "$CLAMAV"; do
    c=$(running_cid "$s"); [ -n "$c" ] || continue
    docker inspect -f '{{range .Mounts}}{{if eq .Type "bind"}}{{.Source}} {{end}}{{end}}' "$c" | tr ' ' '\n' | sed '/^$/d' | while read -r b; do
      pf_warn "$s uses the folder $b: never move or delete it (the container needs it to start again)"; done
  done
  # the new configuration against the running containers (values compared, never printed)
  HEALTH_PORT=$(health_port); export HEALTH_PORT
  pf_ok "web health port 127.0.0.1:$HEALTH_PORT (kept)"
  APP_IMAGE=$(new_tag) dc config --format json > "$WORK/compose.json" 2> "$WORK/compose.err" \
    || { sed 's/^/    /' "$WORK/compose.err"; pf_fail "the new compose configuration does not render"; }
  mkdir -p "$WORK/running"
  for s in "$WEB" "$WORKER"; do
    c=$(running_cid "$s"); [ -n "$c" ] || continue
    docker inspect "$c" > "$WORK/running/$s.json"
    docker image inspect "$(docker inspect -f '{{.Image}}' "$c")" > "$WORK/running/$s.image.json"
  done
  if [ -s "$WORK/compose.json" ]; then
    say "New configuration compared with the running $WEB and $WORKER"
    cp "$LEGAL_DIR/prod.conf" "$LEGAL_DIR/legalcheck.py" "$WORK/"
    pyrun legalcheck.py compare prod.conf compose.json running || PRE_FAIL=1
    rm -f "$WORK/compose.json"
  fi
  # what Caddy and OCS reach now
  say "Reachability now (the update must keep it)"
  EDGE_BEFORE=$(probe "$EDGE_NETWORK" /api/health); info "Caddy network $EDGE_NETWORK -> http://$EDGE_ALIAS:8000/api/health: $EDGE_BEFORE"
  LINK_BEFORE=unreachable
  docker network inspect "$LINK_NETWORK" >/dev/null 2>&1 && LINK_BEFORE=$(probe "$LINK_NETWORK" /api/integration/v1/meta)
  info "OCS network $LINK_NETWORK -> http://$EDGE_ALIAS:8000/api/integration/v1/meta: $LINK_BEFORE (401 = reachable, token required)"
  [ "$EDGE_BEFORE" = 200 ] || pf_fail "Caddy's network does not reach the web service now; fix that before updating"
  echo
  if [ "$PRE_FAIL" = 0 ]; then echo "PREFLIGHT PASSED: nothing blocks an update (read the warnings above)."; else echo "PREFLIGHT FAILED: nothing was changed. Resolve the FAIL lines first."; fi
  return "$PRE_FAIL"
}

db_revision() { docker exec -u postgres "$1" psql -U "$DB_SUPERUSER" -d "$DB_NAME" -Atc 'SELECT version_num FROM alembic_version' 2>/dev/null | tr -d '[:space:]'; }

# ------------------------------------------------------------------ encrypted, verified copy of the database
cmd_predump() {   # in the running backup container; encrypted while written, verified by reading it back; never pruned
  need_root predump
  local bc name sum list tables
  bc=$(running_cid "$BACKUP"); [ -n "$bc" ] || die "The backup service is not running; no copy was made and nothing was changed."
  name="pre-update-$(date -u +%Y%m%dT%H%M%SZ).dump.enc"
  say "Encrypted copy of database $DB_NAME (backup container, AES-256-CBC with PBKDF2, the backup passphrase)"
  docker exec -e NAME="$name" -e PASS_ENV="$BACKUP_PASS_ENV" -e DBN="$DB_NAME" -e DBU="$DB_SUPERUSER" -e SOCK="$DB_SOCKET" "$bc" sh -c '
    set -eu; set -o pipefail; umask 077; f="/backups/$NAME"
    pg_dump -h "$SOCK" -U "$DBU" -d "$DBN" -Fc | openssl enc -aes-256-cbc -pbkdf2 -salt -pass "env:$PASS_ENV" -out "$f.part"
    mv "$f.part" "$f"' || { docker exec "$bc" sh -c "rm -f '/backups/$name.part'" || true; die "The copy could not be made. Nothing was changed; the current version keeps running."; }
  say "Verifying the copy: decrypted in memory, every block read by pg_restore, then its table list (no readable copy is written)"
  # 1. the whole copy decrypts (openssl checks the padding at the end) and pg_restore reads every data block to /dev/null
  docker exec -e NAME="$name" -e PASS_ENV="$BACKUP_PASS_ENV" "$bc" sh -c '
    set -eu; set -o pipefail
    openssl enc -d -aes-256-cbc -pbkdf2 -pass "env:$PASS_ENV" -in "/backups/$NAME" | pg_restore -f /dev/null' \
    || die "The copy /backups/$name could not be decrypted and read completely. Nothing was changed; do not update until this works."
  # 2. its table of contents (pg_restore stops reading after it, so only pg_restore's own result counts here)
  list=$(docker exec -e NAME="$name" -e PASS_ENV="$BACKUP_PASS_ENV" "$bc" sh -c '
    openssl enc -d -aes-256-cbc -pbkdf2 -pass "env:$PASS_ENV" -in "/backups/$NAME" 2>/dev/null | pg_restore --list') \
    || die "The table list of /backups/$name could not be read. Nothing was changed; do not update until this works."
  tables=$(printf '%s\n' "$list" | grep -c ' TABLE DATA ' || true)
  for t in alembic_version users clients esign_envelopes esign_recipients esign_events documents; do
    printf '%s\n' "$list" | grep -q " TABLE DATA public $t " || die "The copy has no data for table $t. Nothing was changed; check the copy before updating."
  done
  sum=$(docker exec "$bc" sha256sum "/backups/$name" | cut -d' ' -f1)
  PREDUMP_NAME=$name
  info "/backups/$name (in volume ${PROJECT}_backups), $(docker exec "$bc" sh -c "du -k '/backups/$name' | cut -f1") KB, sha256 $sum"
  info "verified: $tables tables with data, including users, clients and the e-signature tables"
  mkdir -p "$STATE_DIR"; printf '%s %s %s\n' "$(date -u +%FT%TZ)" "$name" "$sum" >> "$STATE_DIR/pre-update-copies.txt"
  info "Restore by hand if ever needed: UPDATE-AND-ROLLBACK.md, 'Restoring the pre-update copy'. Old copies are never removed by this script."
}

# ------------------------------------------------------------------ switching the application
switch_to() {   # switch_to IMAGE: web and worker only, never their dependencies, never a build or a pull
  APP_IMAGE=$1 dc up -d --no-deps --no-build "$WEB" "$WORKER"
  mkdir -p "$STATE_DIR"; echo "$1" > "$STATE_DIR/active-image"
}

infra_fingerprint() {   # id and start time of the containers an update must not touch
  local s c; for s in "$DB" "$BACKUP" "$CLAMAV"; do c=$(cid "$s"); printf '%s %s %s\n' "$s" "$c" "$( [ -n "$c" ] && docker inspect -f '{{.State.StartedAt}}' "$c")"; done
}

wait_healthy() {   # wait_healthy EXPECTED_VERSION: web answers with it, Caddy's network reaches it, the worker is working
  local want=$1 start w wc ver st since hb edge link
  say "Checking the web service (up to ${WEB_HEALTH_SECONDS}s)"
  start=$(date +%s)
  while :; do
    wc=$(running_cid "$WEB"); st=$( [ -n "$wc" ] && health_of "$wc" || echo missing)
    ver=$(web_health || true)
    if [ "$st" = "running healthy" ] && { [ -z "$want" ] || [ "$ver" = "$want" ]; }; then info "web healthy, version $ver"; break; fi
    case "$st" in exited*|dead*|missing|"running unhealthy") info "web is ${st#running }"; return 1;; esac
    # a version that cannot start is recognised after a minute from its log, not after the full wait (less downtime)
    if [ $(( $(date +%s) - start )) -ge 60 ] && [ -n "$wc" ] && docker logs --since "$(docker inspect -f '{{.State.StartedAt}}' "$wc")" "$wc" 2>&1 | tail -n 60 \
        | grep -qE 'Traceback|ModuleNotFoundError|ImportError|SyntaxError|Error loading ASGI app'; then
      info "the web service reports an error while starting"; return 1
    fi
    [ $(( $(date +%s) - start )) -ge "$WEB_HEALTH_SECONDS" ] && { info "web not healthy in time ($st, version ${ver:-none})"; return 1; }
    sleep 5
  done
  edge=$(probe "$EDGE_NETWORK" /api/health)
  [ "$edge" = 200 ] || { info "Caddy's network $EDGE_NETWORK gets '$edge' from http://$EDGE_ALIAS:8000"; return 1; }
  info "reachable for Caddy on $EDGE_NETWORK as $EDGE_ALIAS"
  if [ "${LINK_BEFORE:-unreachable}" != unreachable ]; then
    link=$(probe "$LINK_NETWORK" /api/integration/v1/meta)
    [ "$link" != unreachable ] || { info "OCS network $LINK_NETWORK no longer reaches the integration feed"; return 1; }
    info "integration feed reachable for OCS on $LINK_NETWORK ($link without a token, as before)"
  fi
  say "Checking the worker (up to ${WORKER_HEALTH_SECONDS}s): it must report email and reminders work since it started"
  start=$(date +%s)
  while :; do
    w=$(running_cid "$WORKER"); st=$( [ -n "$w" ] && health_of "$w" || echo missing)
    case "$st" in exited*|dead*|missing) info "worker is $st"; return 1;; esac
    if [ -n "$w" ]; then
      since=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$w")" +%s 2>/dev/null || echo 0)
      hb=$(docker exec "$w" python -c "import os;print(int(min(os.path.getmtime('/data/.heartbeat-mail'),os.path.getmtime('/data/.heartbeat-sweep'))))" 2>/dev/null || echo 0)
      if [ "$hb" -gt "$since" ] && [ "$st" != "running unhealthy" ]; then info "worker running and reporting (heartbeats since it started)"; return 0; fi
    fi
    [ $(( $(date +%s) - start )) -ge "$WORKER_HEALTH_SECONDS" ] && { info "worker not reporting in time ($st)"; return 1; }
    sleep 5
  done
}

auto_rollback() {
  local log; log=$STATE_DIR/failed-update-$(date -u +%Y%m%dT%H%M%SZ).log
  warn "The new version is not healthy. Going back to $ROLLBACK_IMAGE (web and worker only; the database and files stay as they are)."
  (umask 077; APP_IMAGE=$NEW_IMAGE dc logs --no-color --tail 200 "$WEB" "$WORKER" > "$log" 2>&1 || true)
  switch_to "$ROLLBACK_IMAGE"
  if wait_healthy ""; then
    die "Back on the previous version ($ROLLBACK_IMAGE): it is running, no data was lost. The database keeps the new tables and columns (the previous version ignores them; never downgrade). Log of the failed start: $log"
  fi
  die "The previous version did not report healthy either. Nothing was deleted. See UPDATE-AND-ROLLBACK.md, 'If the rollback does not start'. Log: $log"
}

cmd_update() {
  need_root update
  trap '' HUP
  mkdir -p "$STATE_DIR"; exec 9>"$STATE_DIR/.lock"; flock -n 9 || die "Another agreements.sh update or rollback is running."
  exec > >(tee -a "$STATE_DIR/update-$(date -u +%Y%m%dT%H%M%SZ).log") 2>&1
  NEW_IMAGE=$(new_tag)
  say "Update to $(new_version) ($NEW_IMAGE) on $DOMAIN"
  case " $FORBIDDEN_IMAGES $ROLLBACK_IMAGE $CURRENT_IMAGE " in *" $NEW_IMAGE "*) die "The new image tag $NEW_IMAGE collides with a protected tag.";; esac
  # 1-2 and everything else that must hold before anything changes
  cmd_preflight || die "Preflight failed: nothing was changed."
  [ "$(docker inspect -f '{{.Image}}' "$(running_cid "$WEB")")" != "$(image_id "$ROLLBACK_IMAGE")" ] && [ "${ALLOW_OTHER_RUNNING:-0}" != 1 ] && \
    die "The running image is not $ROLLBACK_IMAGE, so a rollback would not return to what runs now. Nothing was changed. (prod.conf: set ROLLBACK_IMAGE to the running image's tag.)"
  local before rev0 rev1 dbc; dbc=$(running_cid "$DB"); before=$(infra_fingerprint); rev0=$(db_revision "$dbc")
  confirm "Update $DOMAIN to $(new_version)? (backup, build, migrate, switch web and worker)" || die "Nothing was changed."
  # tfsc-link: created only when missing (OCS reads the feed over it); no other network is created or changed
  docker network inspect "$LINK_NETWORK" >/dev/null 2>&1 || { docker network create "$LINK_NETWORK" >/dev/null && info "created network $LINK_NETWORK"; }
  # 3-4 encrypted copy, verified
  cmd_predump
  # 5-6 only the application image is built; PostgreSQL is never built or replaced
  say "Building $NEW_IMAGE (the site keeps running on the current version)"
  local secret=(); [ -n "${BUILD_CA:-}" ] && secret=(--secret "id=build_ca,src=$BUILD_CA")
  docker build ${secret[@]+"${secret[@]}"} -f "$ROOT/apps/agreements/Dockerfile" -t "$NEW_IMAGE" "$ROOT" || die "The build failed. Nothing was switched; the current version keeps running."
  # 7 migration, in a one-off container of the new image with the web service's configuration (it joins no alias)
  say "Applying database changes (one transaction; the current version keeps running)"
  APP_IMAGE=$NEW_IMAGE dc run --rm --no-deps -T -e DB_STATEMENT_TIMEOUT_MS=0 -e DB_LOCK_TIMEOUT_MS=60000 -e DB_POOL_SIZE=2 -e DB_POOL_OVERFLOW=0 "$WEB" \
    python -c "import database; database.init_db(); print('The database is ready for the new version.')" \
    || die "The database changes could not be applied. PostgreSQL undid them, nothing was switched, the current version keeps running."
  rev1=$(db_revision "$dbc"); info "database revision $rev0 -> $rev1"
  # 8 switch web and worker only
  say "Switching $WEB and $WORKER to $NEW_IMAGE"
  switch_to "$NEW_IMAGE"
  # 10-12 health; back to the verified previous image when unhealthy
  wait_healthy "$(new_version)" || auto_rollback
  [ "$(infra_fingerprint)" = "$before" ] && info "PostgreSQL, backup and ClamAV: same containers, not restarted" \
    || warn "PostgreSQL, backup or ClamAV changed during the update (this script does not touch them); check: docker ps"
  printf '%s %s %s -> %s\n' "$(date -u +%FT%TZ)" "$NEW_IMAGE" "$rev0" "$rev1" >> "$STATE_DIR/history.txt"
  say "Updated to $(new_version). Database $rev0 -> $rev1. Pre-update copy: /backups/$PREDUMP_NAME"
  info "Rollback, if needed: sudo $0 rollback   (returns to $ROLLBACK_IMAGE; keep it and $NEW_IMAGE)"
  info "Next update: set ROLLBACK_IMAGE in prod.conf to a tag of $NEW_IMAGE (PRODUCTION-ADAPTATION.md, 'After a successful update')."
}

cmd_rollback() {
  need_root rollback
  mkdir -p "$STATE_DIR"; exec 9>"$STATE_DIR/.lock"; flock -n 9 || die "Another agreements.sh update or rollback is running."
  detect_env_file
  local rid; rid=$(image_id "$ROLLBACK_IMAGE")
  [ -n "$rid" ] || die "The rollback image $ROLLBACK_IMAGE does not exist. Nothing was changed."
  [ "$rid" = "$ROLLBACK_IMAGE_ID" ] || die "$ROLLBACK_IMAGE is $rid, not the verified $ROLLBACK_IMAGE_ID. Nothing was changed."
  NEW_IMAGE=$(active_image)
  confirm "Switch $WEB and $WORKER from $NEW_IMAGE back to $ROLLBACK_IMAGE? The database is NOT downgraded; files are kept." || die "Nothing was changed."
  LINK_BEFORE=unreachable; docker network inspect "$LINK_NETWORK" >/dev/null 2>&1 && LINK_BEFORE=$(probe "$LINK_NETWORK" /api/integration/v1/meta)
  local before; before=$(infra_fingerprint)
  switch_to "$ROLLBACK_IMAGE"
  wait_healthy "" || die "$ROLLBACK_IMAGE did not report healthy. Nothing was deleted. See UPDATE-AND-ROLLBACK.md."
  [ "$(infra_fingerprint)" = "$before" ] && info "PostgreSQL, backup and ClamAV: same containers, not restarted"
  printf '%s rollback %s -> %s\n' "$(date -u +%FT%TZ)" "$NEW_IMAGE" "$ROLLBACK_IMAGE" >> "$STATE_DIR/history.txt"
  say "Back on $ROLLBACK_IMAGE. The database keeps the newer tables and columns (never downgrade it). $NEW_IMAGE is kept."
}

cmd_status() {
  detect_env_file
  say "Containers of $PROJECT"
  docker ps -a --filter "label=com.docker.compose.project=$PROJECT" --format 'table {{.Label "com.docker.compose.service"}}\t{{.Image}}\t{{.Status}}'
  say "Application"
  info "web answers: version $(web_health || echo '-') on 127.0.0.1:$(health_port)"
  info "public: $(curl -fsS -m 15 -o /dev/null -w '%{http_code}' "https://$DOMAIN/api/health" 2>/dev/null || echo 'not reachable from this server')"
  local dbc; dbc=$(running_cid "$DB"); [ -n "$dbc" ] && info "database revision: $(db_revision "$dbc")"
  say "Encrypted pre-update copies (never removed by this script)"
  local bc; bc=$(running_cid "$BACKUP"); [ -n "$bc" ] && docker exec "$bc" sh -c 'ls -l /backups/pre-update-* 2>/dev/null || echo "   none"' | sed 's/^/   /'
  say "Last backups"
  [ -n "$bc" ] && docker exec "$bc" tfsc-backup status 2>/dev/null | tail -n 15 || true
}

cmd_signatures() {   # as upstream, on the production web service
  local dir=${1-} name ext n=0 c
  [ -n "$dir" ] && [ -d "$dir" ] || die "Usage: $0 signatures /path/to/folder"
  c=$(running_cid "$WEB"); [ -n "$c" ] || die "$WEB is not running."
  docker exec "$c" sh -c 'umask 077 && mkdir -p /app/private-assets/signatures'
  for name in 'Naeem Abbas' 'Danish Ather'; do
    for ext in png json; do
      [ -f "$dir/$name.$ext" ] || continue
      docker exec -i "$c" sh -c 'umask 077 && cat > "/app/private-assets/signatures/$1"' _ "$name.$ext" < "$dir/$name.$ext"; n=$((n + 1))
    done
  done
  [ "$n" -gt 0 ] || die "No signature files found in $dir"
  docker exec "$c" python manage.py install-signatures
  docker exec "$c" sh -c 'rm -f /app/private-assets/signatures/*.png /app/private-assets/signatures/*.json'
}

cmd_help() {
  cat <<EOF
Usage: $0 <command> [--yes]     (production: $DOMAIN, project $PROJECT)
  preflight          read-only checks before an update (as root)
  update             encrypted verified copy, build, migrate, switch $WEB and $WORKER; automatic step back if unhealthy
  rollback           switch $WEB and $WORKER back to $ROLLBACK_IMAGE (database not downgraded)
  predump            only the encrypted, verified copy of the database
  status             containers, versions, database revision, pre-update copies, backups
  logs [service]     follow logs ($WEB and $WORKER by default)
  restart-app        restart $WEB and $WORKER only (same image)
  setup-key          show the one-time setup key (until the Super Admin exists)
  signatures DIR     install the authorized signature images
Not available here on purpose: install, stop/start of the whole stack, prune. PostgreSQL, backup, ClamAV and Caddy are
managed separately. Guide: deploy/agreements-legal/UPDATE-AND-ROLLBACK.md
EOF
}

main() {
  local cmd=${1:-help} a args=()
  [ $# -gt 0 ] && shift
  for a in "$@"; do if [ "$a" = --yes ]; then YES=1; else args+=("$a"); fi; done
  set -- ${args[@]+"${args[@]}"}
  case "$cmd" in
    preflight) cmd_preflight ;;
    update) detect_env_file; cmd_update ;;
    rollback) cmd_rollback ;;
    predump) detect_env_file; cmd_predump ;;
    status) cmd_status ;;
    logs) detect_env_file; if [ $# -gt 0 ]; then dc logs -f --tail 200 "$@"; else dc logs -f --tail 200 "$WEB" "$WORKER"; fi ;;
    restart-app) need_root restart-app; detect_env_file; dc restart "$WEB" "$WORKER" ;;
    setup-key) c=$(running_cid "$WEB"); [ -n "$c" ] || die "$WEB is not running."; docker exec "$c" python manage.py setup-key >/dev/null 2>&1 || true
               echo "Setup key: $(docker exec "$c" sh -c 'cat /data/setup-key.txt 2>/dev/null' || echo '(already used)')" ;;
    signatures) need_root signatures; cmd_signatures "${1-}" ;;
    install|start|stop|down|restart) die "'$cmd' is not available in production (it would act on the whole stack). Use: update, rollback, restart-app." ;;
    help|-h|--help) cmd_help ;;
    *) cmd_help; exit 2 ;;
  esac
}
main "$@"
