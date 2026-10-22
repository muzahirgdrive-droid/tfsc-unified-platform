#!/usr/bin/env bash
# TFSC Agreements on one Linux server (VPS): install, first sign-in, updates and day-to-day commands.
# Step-by-step guide: README.md next to this script (START-HERE.md in the deployment ZIP).
#   sudo ./agreements.sh install    asks for the web address, generates every secret, builds and starts everything
#   ./agreements.sh help            every command
# Everything runs in Docker (deploy/agreements/compose.yaml; Caddy obtains the TLS certificate). Settings and secrets
# live in deploy/agreements/.env (mode 600); data lives in Docker volumes and survives updates and restarts.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
while [ ! -f "$ROOT/deploy/agreements/compose.yaml" ]; do
  [ "$ROOT" = / ] && { echo "deploy/agreements/compose.yaml not found above $(dirname "${BASH_SOURCE[0]}")" >&2; exit 1; }
  ROOT=$(dirname "$ROOT")
done
COMPOSE_DIR=$ROOT/deploy/agreements
ENV_FILE=$COMPOSE_DIR/.env
YES=0

say() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
dc() { (cd "$COMPOSE_DIR" && docker compose --env-file .env "$@"); }
env_get() { sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | sed "s/^'\(.*\)'\$/\1/"; }
hexsecret() { head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n'; }

ask() {   # ask VAR "question" [default]: a value already in the environment wins (unattended installs)
  local var=$1 question=$2 default=${3-} value
  value=${!var-}
  if [ -z "$value" ]; then
    if [ -t 0 ]; then
      read -r -p "$question${default:+ [$default]}: " value
    fi
    value=${value:-$default}
  fi
  case "$value" in *\'*|*\"*|*\\*) die "$var must not contain quotes or backslashes";; esac
  printf -v "$var" '%s' "$value"
}

confirm() {   # confirm "question": yes with --yes; no when nobody can answer
  [ "$YES" = 1 ] && return 0
  [ -t 0 ] || return 1
  local answer; read -r -p "$1 [y/N]: " answer
  case "$answer" in y|Y|yes|YES) return 0;; *) return 1;; esac
}

need_docker() {
  if docker compose version >/dev/null 2>&1; then return; fi
  [ "$(id -u)" = 0 ] || die "Docker is not installed. Run: sudo $0 install"
  if confirm "Docker is not installed. Install it now with Docker's official script (get.docker.com)?"; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker >/dev/null 2>&1 || true
  else
    die "Install Docker Engine with the compose plugin (https://docs.docker.com/engine/install/), then run this again."
  fi
  docker compose version >/dev/null 2>&1 || die "docker compose is still not available."
}

check_host() {
  local arch mem_kb free_kb
  arch=$(uname -m)
  [ "$arch" = x86_64 ] || warn "This server is $arch; TFSC Agreements was tested on x86_64 (amd64) only."
  mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
  [ "$mem_kb" -ge 3700000 ] || warn "This server has $((mem_kb / 1024)) MB of memory; 4 GB or more is recommended (README.md, 'What you need')."
  free_kb=$(df -Pk /var/lib 2>/dev/null | awk 'NR==2 {print $4}')
  [ "${free_kb:-0}" -ge 20971520 ] || warn "Less than 20 GB free disk under /var/lib; images, the database and backups need room to grow."
}

write_env() {
  say "Settings"
  echo "TFSC Agreements uses one web address that you point at this server (a DNS A record),"
  echo "for example agreements.yourcompany.com."
  ask TFSC_DOMAIN "Web address for TFSC Agreements"
  [ -n "${TFSC_DOMAIN-}" ] || die "An address is required (set TFSC_DOMAIN=agreements.example.com for an unattended install)."
  TFSC_DOMAIN=${TFSC_DOMAIN#https://}; TFSC_DOMAIN=${TFSC_DOMAIN#http://}; TFSC_DOMAIN=${TFSC_DOMAIN%%/*}
  echo "  Agreements: https://$TFSC_DOMAIN"
  ask BACKUP_TZ "Time zone for the nightly backup" "America/Los_Angeles"
  [ -e "/usr/share/zoneinfo/$BACKUP_TZ" ] || [ ! -d /usr/share/zoneinfo ] || warn "Unknown time zone '$BACKUP_TZ'."
  umask 077
  cat > "$ENV_FILE" <<EOF
# Written by agreements.sh install on $(date -u +%Y-%m-%dT%H:%MZ). Keep a copy of this file in your password manager:
# BACKUP_PASSPHRASE is the only key to the encrypted backups. Never send it by email or commit it anywhere.
TFSC_DOMAIN='$TFSC_DOMAIN'
POSTGRES_PASSWORD='$(hexsecret)'
BACKUP_PASSPHRASE='$(hexsecret)'
BACKUP_HOUR=2
BACKUP_TZ='$BACKUP_TZ'
# Encrypts the company mailbox passwords used for e-signature email. Changing it means entering them again.
TFSC_SECRET_KEY='$(hexsecret)'
EOF
  chmod 600 "$ENV_FILE"
  umask 022
  echo "Settings and generated secrets written to $ENV_FILE"
}

ensure_env_keys() {   # settings that later releases need: generated once, kept from then on
  grep -q '^TFSC_SECRET_KEY=' "$ENV_FILE" && return 0
  say "Adding TFSC_SECRET_KEY (encrypts the company mailbox passwords) to $ENV_FILE"
  printf "# Added by agreements.sh on %s: encrypts the company mailbox passwords for e-signature email.\nTFSC_SECRET_KEY='%s'\n" "$(date -u +%Y-%m-%dT%H:%MZ)" "$(hexsecret)" >> "$ENV_FILE"
  echo "Save the updated $ENV_FILE in your password manager."
}

open_firewall() {
  command -v ufw >/dev/null 2>&1 || return 0
  ufw status 2>/dev/null | grep -q '^Status: active' || return 0
  say "Firewall (ufw): allowing SSH, HTTP and HTTPS"
  ufw allow OpenSSH >/dev/null || ufw allow 22/tcp >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
}

build_images() {
  say "Building the application images (first time: about 5-10 minutes)"
  local secret=()
  # Only behind a TLS-inspecting proxy: BUILD_CA=/path/to/proxy-ca.crt (used during the build, never stored in an image)
  if [ -n "${BUILD_CA-}" ]; then secret=(--secret "id=build_ca,src=$BUILD_CA"); fi
  docker build ${secret[@]+"${secret[@]}"} -t tfsc-postgres:16 "$ROOT/infra/compose/postgres"
  docker build ${secret[@]+"${secret[@]}"} -f "$ROOT/apps/agreements/Dockerfile" -t tfsc-agreements:4 "$ROOT"
}

wait_ready() {
  say "Waiting for TFSC Agreements to start"
  local port; port=$(env_get TFSC_PORT); port=${port:-8765}
  for _ in $(seq 1 120); do
    if curl -fsS -o /dev/null -H "Host: $(env_get TFSC_DOMAIN)" "http://127.0.0.1:$port/api/health" 2>/dev/null \
       && [ "$(dc ps agreements-worker --format '{{.State}}' 2>/dev/null)" = running ]; then
      echo "TFSC Agreements is up."
      return 0
    fi
    sleep 5
  done
  dc ps
  die "TFSC Agreements did not start within 10 minutes. See: ./agreements.sh logs"
}

check_public() {   # through Caddy with a real certificate: needs DNS pointing here and ports 80/443 open
  say "Checking the public address (HTTPS)"
  local url; url="https://$(env_get TFSC_DOMAIN)/api/health"
  if curl -fsS -m 20 -o /dev/null "$url" 2>/dev/null; then
    echo "  OK    $url"
  else
    echo "  NOT YET  $url"
    echo "The certificate is issued once DNS points at this server and ports 80 and 443 are open; Caddy keeps retrying."
    echo "Check again later with: ./agreements.sh status"
  fi
}

print_key() {
  local key
  dc exec -T agreements python manage.py setup-key >/dev/null 2>&1 || true
  key=$(dc exec -T agreements sh -c 'cat /data/setup-key.txt 2>/dev/null' || true)
  echo "Setup key: ${key:-(already used: the Super Admin exists)}"
}

cmd_install() {
  [ "$(id -u)" = 0 ] || die "Run the installer as root: sudo $0 install"
  check_host
  need_docker
  if [ -f "$ENV_FILE" ]; then
    say "Keeping the existing settings in $ENV_FILE"
    ensure_env_keys
  else
    write_env
  fi
  open_firewall
  build_images
  say "Starting TFSC Agreements"
  dc up -d
  wait_ready
  check_public
  say "Installed. Next (START-HERE.md, step 4 onwards)"
  echo "  1. Open https://$(env_get TFSC_DOMAIN)/login and create the Super Admin with the setup key below."
  echo "  2. Company mailboxes: connect each company's compliance mailbox (step 5)."
  echo "  3. Users & permissions: add your team (each gets a temporary password to change at first sign-in)."
  echo
  print_key
  echo
  echo "Save $ENV_FILE (it holds BACKUP_PASSPHRASE) in your password manager now."
}

cmd_status() {
  dc ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Status}}'
  check_public
  say "Last backups"
  dc exec -T backup tfsc-backup status 2>/dev/null | tail -n 15 || echo "(the backup service is not running)"
}

cmd_signatures() {
  local dir=${1-} name ext n=0
  [ -n "$dir" ] && [ -d "$dir" ] || die "Usage: $0 signatures /path/to/folder (with 'Naeem Abbas.png/.json' and 'Danish Ather.png/.json')"
  dc exec -T agreements sh -c 'umask 077 && mkdir -p /app/private-assets/signatures'
  for name in 'Naeem Abbas' 'Danish Ather'; do
    for ext in png json; do
      [ -f "$dir/$name.$ext" ] || continue
      dc exec -T agreements sh -c 'umask 077 && cat > "/app/private-assets/signatures/$1"' _ "$name.$ext" < "$dir/$name.$ext"
      n=$((n + 1))
    done
  done
  [ "$n" -gt 0 ] || die "No signature files found in $dir"
  dc exec -T agreements python manage.py install-signatures
  dc exec -T agreements sh -c 'rm -f /app/private-assets/signatures/*.png /app/private-assets/signatures/*.json'
  echo "Installed. Delete $dir after checking one generated agreement."
}

cmd_update() {
  [ "$(id -u)" = 0 ] || die "Run as root: sudo $0 update"
  [ -f "$ENV_FILE" ] || die "No settings in $ENV_FILE. Copy deploy/agreements/.env (and backup-offsite.env, if you use it) from the previous release folder first."
  ensure_env_keys
  build_images
  say "Restarting with the new version (database changes are applied automatically)"
  dc up -d
  wait_ready
  docker image prune -f >/dev/null
  echo "Updated."
}

cmd_help() {
  cat <<EOF
Usage: $0 <command>
  install            first installation (as root): settings, secrets, images, start, the one-time setup key
  status             what is running, the public HTTPS address, the latest backups
  setup-key          show the one-time setup key again (until the Super Admin exists)
  signatures DIR     install the authorized signature images for issued agreements
  backup             take a full backup now (encrypted), followed by the automatic restore test
  logs [service]     follow the logs (all services, or e.g. agreements, agreements-worker, edge)
  update             after unpacking a new release (as root): rebuild and restart, data is kept
  restart | stop | start
Settings: $ENV_FILE    Guide: README.md (START-HERE.md in the ZIP)
EOF
}

main() {
  local cmd=${1:-help} a args=()
  if [ $# -gt 0 ]; then shift; fi
  for a in "$@"; do
    if [ "$a" = --yes ]; then YES=1; else args+=("$a"); fi
  done
  set -- ${args[@]+"${args[@]}"}
  case "$cmd" in
    install) cmd_install ;;
    status) cmd_status ;;
    setup-key) print_key ;;
    signatures) cmd_signatures "${1-}" ;;
    backup) dc exec -T backup sh -c 'tfsc-backup backup full && tfsc-backup restore-test' ;;
    logs) dc logs -f --tail 200 "$@" ;;
    update) cmd_update ;;
    restart) dc restart ;;
    stop) dc stop ;;
    start) dc up -d ;;
    help|-h|--help) cmd_help ;;
    *) cmd_help; exit 2 ;;
  esac
}
main "$@"
