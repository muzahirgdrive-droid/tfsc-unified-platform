#!/usr/bin/env bash
# TFSC platform on one Linux server (VPS): install, first sign-in, updates and day-to-day commands.
# Step-by-step guide: README.md next to this script (START-HERE.md in the deployment ZIP).
#   sudo ./tfsc.sh install       asks for your domain and mail server, generates every secret, builds and starts everything
#   ./tfsc.sh help               every command
# Everything runs in Docker (infra/compose, profile `edge`: Caddy obtains the TLS certificates). Settings and secrets
# live in infra/compose/.env (mode 600); data lives in Docker volumes and survives updates and restarts.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
while [ ! -f "$ROOT/infra/compose/compose.yaml" ]; do
  [ "$ROOT" = / ] && { echo "infra/compose/compose.yaml not found above $(dirname "${BASH_SOURCE[0]}")" >&2; exit 1; }
  ROOT=$(dirname "$ROOT")
done
COMPOSE_DIR=$ROOT/infra/compose
ENV_FILE=$COMPOSE_DIR/.env
YES=0

say() { printf '\n== %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
dc() { (cd "$COMPOSE_DIR" && docker compose --env-file .env --profile edge "$@"); }
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
  case "$value" in *\'*|*\"*|*\\*) die "$var must not contain quotes or backslashes (use a different password or app key)";; esac
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
  [ "$arch" = x86_64 ] || warn "This server is $arch; the platform was tested on x86_64 (amd64) only."
  mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)
  [ "$mem_kb" -ge 7500000 ] || warn "This server has $((mem_kb / 1024)) MB of memory; 8 GB or more is recommended (README.md, 'Server size')."
  free_kb=$(df -Pk /var/lib 2>/dev/null | awk 'NR==2 {print $4}')
  [ "${free_kb:-0}" -ge 31457280 ] || warn "Less than 30 GB free disk under /var/lib; images, the database and backups need room to grow."
}

write_env() {
  say "Settings"
  echo "The platform uses four web addresses under one domain you own, for example crm.example.com,"
  echo "agreements.example.com, sso.example.com and files.example.com. Point all four at this server (DNS A records)."
  ask DOMAIN "Your domain (without crm./www.)"
  [ -n "${DOMAIN-}" ] || die "A domain is required (set DOMAIN=example.com for an unattended install)."
  DOMAIN=${DOMAIN#https://}; DOMAIN=${DOMAIN#http://}; DOMAIN=${DOMAIN%/}
  : "${CRM_DOMAIN:=crm.$DOMAIN}" "${TFSC_DOMAIN:=agreements.$DOMAIN}" "${SSO_DOMAIN:=sso.$DOMAIN}" "${FILES_DOMAIN:=files.$DOMAIN}"
  echo "  CRM:        https://$CRM_DOMAIN"
  echo "  Agreements: https://$TFSC_DOMAIN"
  echo "  Sign-in:    https://$SSO_DOMAIN"
  echo "  Downloads:  https://$FILES_DOMAIN"
  echo
  echo "Outgoing email (invitations, password resets) needs an SMTP account, for example from your email provider"
  echo "or a sending service. Leave the host empty to skip for now (you can add it later in the sign-in admin console)."
  ask SMTP_HOST "SMTP host" ""
  if [ -n "$SMTP_HOST" ]; then
    ask SMTP_PORT "SMTP port" 587
    ask SMTP_USER "SMTP user name"
    ask SMTP_PASSWORD "SMTP password or app key"
    ask SMTP_FROM "Send emails from (address)" "no-reply@$DOMAIN"
    ask SMTP_SECURITY "Connection security: starttls (port 587) or ssl (port 465)" "$([ "$SMTP_PORT" = 465 ] && echo ssl || echo starttls)"
  fi
  ask BACKUP_TZ "Time zone for the nightly backup" "America/Los_Angeles"
  [ -e "/usr/share/zoneinfo/$BACKUP_TZ" ] || [ ! -d /usr/share/zoneinfo ] || warn "Unknown time zone '$BACKUP_TZ'."

  local starttls=false ssl=false auth=false
  if [ -n "$SMTP_HOST" ]; then
    case "${SMTP_SECURITY:-starttls}" in ssl) ssl=true;; none) ;; *) starttls=true;; esac
    if [ -n "${SMTP_USER-}" ]; then auth=true; fi
  fi
  umask 077
  cat > "$ENV_FILE" <<EOF
# Written by tfsc.sh install on $(date -u +%Y-%m-%dT%H:%MZ). Keep a copy of this file in your password manager:
# BACKUP_PASSPHRASE is the only key to the encrypted backups. Never send it by email or commit it anywhere.
CRM_MODE=hosted
TFSC_MODE=hosted
CRM_DOMAIN='$CRM_DOMAIN'
TFSC_DOMAIN='$TFSC_DOMAIN'
SSO_DOMAIN='$SSO_DOMAIN'
FILES_DOMAIN='$FILES_DOMAIN'
CRM_ORIGIN='https://$CRM_DOMAIN'
TFSC_ORIGIN='https://$TFSC_DOMAIN'
SSO_PUBLIC_URL='https://$SSO_DOMAIN'
FILES_PUBLIC_URL='https://$FILES_DOMAIN'
PUBLISH_ADDR=127.0.0.1
POSTGRES_PASSWORD='$(hexsecret)'
BACKUP_PASSPHRASE='$(hexsecret)'
BACKUP_HOUR=2
BACKUP_TZ='$BACKUP_TZ'
CRM_PHONE_KEY_SECRET='$(hexsecret)'
CRM_FIELD_KEY='$(head -c 32 /dev/urandom | base64)'
BRIDGE_SECRET='$(hexsecret)'
CRM_OIDC_CLIENT_SECRET='$(hexsecret)'
TFSC_OIDC_CLIENT_SECRET='$(hexsecret)'
CRM_KEYCLOAK_CLIENT_SECRET='$(hexsecret)'
SMTP_HOST='${SMTP_HOST:-mailpit}'
SMTP_PORT='${SMTP_PORT:-1025}'
SMTP_FROM='${SMTP_FROM:-no-reply@$DOMAIN}'
SMTP_FROM_NAME='24-7 Consultancy'
SMTP_AUTH=$auth
SMTP_USER='${SMTP_USER-}'
SMTP_PASSWORD='${SMTP_PASSWORD-}'
SMTP_STARTTLS=$starttls
SMTP_SSL=$ssl
KC_ADMIN_USER=admin
KC_ADMIN_PASSWORD='$(hexsecret)'
KC_COMMAND=start
KC_REALM_SSL_REQUIRED=external
# 1 only until the first Super Admin is set up; \`./tfsc.sh finish-setup\` switches it off.
CRM_ALLOW_LOCAL_LOGIN=1
CRM_METRICS_TOKEN='$(hexsecret)'
S3_ACCESS_KEY=tfsc-crm
S3_SECRET_KEY='$(hexsecret)'
CRM_CLAMAV=clamav:3310
GRAFANA_ADMIN_PASSWORD='$(hexsecret)'
EOF
  chmod 600 "$ENV_FILE"
  umask 022
  echo "Settings and generated secrets written to $ENV_FILE"
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
  say "Building the application images (first time: about 5-15 minutes)"
  local secret=()
  # Only behind a TLS-inspecting proxy: BUILD_CA=/path/to/proxy-ca.crt (used during the build, never stored in an image)
  if [ -n "${BUILD_CA-}" ]; then secret=(--secret "id=build_ca,src=$BUILD_CA"); fi
  docker build ${secret[@]+"${secret[@]}"} -t tfsc-postgres:16 "$COMPOSE_DIR/postgres"
  docker build ${secret[@]+"${secret[@]}"} -f "$ROOT/apps/crm-api/Dockerfile" -t tfsc-crm:2 "$ROOT"
  docker build ${secret[@]+"${secret[@]}"} -f "$ROOT/apps/agreements/Dockerfile" -t tfsc-agreements:4 "$ROOT"
}

wait_ready() {
  say "Waiting for the services to start (Keycloak and the virus scanner take a few minutes the first time)"
  local crm_port tfsc_port
  crm_port=$(env_get CRM_PORT); crm_port=${crm_port:-8766}
  tfsc_port=$(env_get TFSC_PORT); tfsc_port=${tfsc_port:-8765}
  for _ in $(seq 1 120); do
    if [ "$(dc ps crm-api --format '{{.Health}}' 2>/dev/null)" = healthy ] \
       && curl -fsS -o /dev/null -H "Host: $(env_get CRM_DOMAIN)" "http://127.0.0.1:$crm_port/api/health" 2>/dev/null \
       && curl -fsS -o /dev/null -H "Host: $(env_get TFSC_DOMAIN)" "http://127.0.0.1:$tfsc_port/api/health" 2>/dev/null \
       && keycloak_ready; then
      echo "All services are up."
      return 0
    fi
    sleep 5
  done
  dc ps
  die "Services did not become ready in 10 minutes. See: ./tfsc.sh logs"
}

keycloak_ready() {
  local port; port=$(env_get SSO_PORT); port=${port:-8081}
  curl -fsS -o /dev/null "http://127.0.0.1:$port/realms/tfsc/.well-known/openid-configuration" 2>/dev/null
}

check_public() {   # through the edge with real certificates: needs DNS pointing here and ports 80/443 open
  say "Checking the public addresses (HTTPS)"
  local ok=1 name url
  for name in CRM_DOMAIN TFSC_DOMAIN SSO_DOMAIN; do
    url="https://$(env_get "$name")"
    case "$name" in SSO_DOMAIN) url="$url/realms/tfsc/.well-known/openid-configuration";; *) url="$url/api/health";; esac
    if curl -fsS -m 20 -o /dev/null "$url" 2>/dev/null; then echo "  OK    $url"; else echo "  NOT YET  $url"; ok=0; fi
  done
  if [ "$ok" = 0 ]; then
    echo "Certificates are issued once DNS points at this server and ports 80 and 443 are open; Caddy keeps retrying."
    echo "Check again later with: ./tfsc.sh status"
  fi
}

print_keys() {
  local crm tfsc
  crm=$(dc exec -T crm-api sh -c 'cat /data/setup-key.txt 2>/dev/null' || true)
  dc exec -T agreements python manage.py setup-key >/dev/null 2>&1 || true
  tfsc=$(dc exec -T agreements sh -c 'cat /data/setup-key.txt 2>/dev/null' || true)
  echo "CRM setup key:        ${crm:-(already used: the CRM Super Admin exists)}"
  echo "Agreements setup key: ${tfsc:-(already used: the Agreements Super Admin exists)}"
}

cmd_install() {
  [ "$(id -u)" = 0 ] || die "Run the installer as root: sudo $0 install"
  check_host
  need_docker
  if [ -f "$ENV_FILE" ]; then
    say "Keeping the existing settings in $ENV_FILE"
  else
    write_env
  fi
  open_firewall
  build_images
  say "Starting the platform"
  dc up -d
  wait_ready
  check_public
  say "Installed. Next: create the two Super Admin accounts (START-HERE.md, step 4)"
  echo "  1. Open https://$(env_get CRM_DOMAIN)/login and set up the CRM with the CRM setup key below."
  echo "  2. Open https://$(env_get TFSC_DOMAIN)/login and set up Agreements with the Agreements setup key."
  echo "  3. Run: sudo ./tfsc.sh link-admins"
  echo "  4. In the CRM: People > yourself > 'Send sign-in invitation'; follow the email."
  echo "  5. Run: sudo ./tfsc.sh finish-setup"
  echo
  print_keys
  echo
  echo "Save $ENV_FILE (it holds BACKUP_PASSPHRASE and the admin passwords) in your password manager now."
}

cmd_status() {
  dc ps --format 'table {{.Service}}\t{{.State}}\t{{.Health}}\t{{.Status}}'
  check_public
  say "Last backups"
  dc exec -T backup tfsc-backup status 2>/dev/null | tail -n 15 || echo "(the backup service is not running)"
}

cmd_link_admins() {   # the CRM Super Admin acts in Agreements as the Agreements Super Admin (client handoffs, grants)
  local crm_id
  crm_id=$(dc exec -T crm-api python -c '
from sqlalchemy import select
from tfsc_crm.db import models as m
from tfsc_crm.db.session import system_tx
with system_tx() as c:
    r = c.execute(select(m.users.c.id).where(m.users.c.crm_role == "super_admin", m.users.c.status == "active")
                  .order_by(m.users.c.created_at).limit(1)).first()
print(r[0].hex if r else "")
' | tail -n 1 | tr -d '\r')
  [ -n "$crm_id" ] || die "Set up the CRM Super Admin first: open https://$(env_get CRM_DOMAIN)/login and use the setup key."
  dc exec -T agreements python manage.py link-crm-admin "$crm_id"
}

cmd_finish_setup() {
  [ -f "$ENV_FILE" ] || die "Not installed yet: sudo $0 install"
  if [ "$(env_get CRM_ALLOW_LOCAL_LOGIN)" = 0 ]; then echo "Password sign-in is already off; everyone signs in with SSO."; return; fi
  echo "This turns off the CRM's built-in password sign-in; afterwards everyone, you included, signs in with SSO."
  echo "Before you continue: in the CRM open People, select yourself, click 'Send sign-in invitation', and finish the"
  echo "email (set a password and the authenticator app), so you can still sign in."
  confirm "Turn off password sign-in now?" || { echo "Nothing changed."; return; }
  sed -i 's/^CRM_ALLOW_LOCAL_LOGIN=.*/CRM_ALLOW_LOCAL_LOGIN=0/' "$ENV_FILE"
  dc up -d
  echo "Done: the CRM accepts SSO sign-in only."
}

cmd_signatures() {
  local dir=${1-} name ext n=0
  [ -n "$dir" ] && [ -d "$dir" ] || die "Usage: $0 signatures /path/to/folder (with 'Naeem Abbas.png/.json' and 'Danish Ather.png/.json')"
  dc exec -T agreements mkdir -p /app/private-assets/signatures
  for name in 'Naeem Abbas' 'Danish Ather'; do
    for ext in png json; do
      [ -f "$dir/$name.$ext" ] || continue
      dc exec -T agreements sh -c 'cat > "/app/private-assets/signatures/$1"' _ "$name.$ext" < "$dir/$name.$ext"
      n=$((n + 1))
    done
  done
  [ "$n" -gt 0 ] || die "No signature files found in $dir"
  dc exec -T agreements python manage.py install-signatures
  dc exec -T agreements rm -rf /app/private-assets/signatures
  echo "Delete the copies you uploaded to this server once you have checked an issued agreement."
}

cmd_update() {
  [ "$(id -u)" = 0 ] || die "Run as root: sudo $0 update"
  [ -f "$ENV_FILE" ] || die "No settings in $ENV_FILE. Copy infra/compose/.env (and backup-offsite.env, if you use it) from the previous release folder first."
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
  install            first installation (as root): settings, secrets, images, start, first-time setup keys
  status             what is running, the public HTTPS addresses, the latest backups
  setup-keys         show the one-time setup keys again (until each Super Admin exists)
  link-admins        link the CRM Super Admin to the Agreements Super Admin (after both are set up)
  finish-setup       turn off the CRM's bootstrap password sign-in after you can sign in with SSO
  signatures DIR     install the authorized signature images for issued agreements
  backup             take a full backup now (encrypted), followed by the automatic restore test
  logs [service]     follow the logs (all services, or e.g. crm-api, agreements, keycloak, edge)
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
    setup-keys) print_keys ;;
    link-admins) cmd_link_admins ;;
    finish-setup) cmd_finish_setup ;;
    signatures) cmd_signatures "${1-}" ;;
    backup) dc exec -T backup tfsc-backup backup full ;;
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
