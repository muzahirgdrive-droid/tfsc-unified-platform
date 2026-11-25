#!/usr/bin/env bash
# Checks an unpacked TFSC Agreements release for legal.24-7consultancy.cloud BEFORE it goes near the server.
# Read-only; runs on any Linux machine with bash, python3 and (for the compose checks) Docker with the compose plugin.
#
#   deploy/agreements-legal/validate-release.sh [RELEASE_DIR]      (default: the release this script is in)
#
# Checks: shell syntax of every script (and ShellCheck when installed); YAML/compose rendering of the production and the
# upstream compose files with placeholder values; production service, image, volume and network names; rollback tag;
# whole-stack operations, prune, down, PostgreSQL rebuilds, firewall or Caddy commands in the production script; new or
# destructive migrations; environment variables upstream added that production lacks; files that must never ship.
# Exit 0: no failure (read the warnings). Exit 1: at least one failure. Nothing on any server is contacted or changed.
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REL=$(cd "${1:-$HERE/../..}" && pwd)
LEGAL=$REL/deploy/agreements-legal
[ -f "$LEGAL/prod.conf" ] || { echo "no deploy/agreements-legal/prod.conf in $REL" >&2; exit 2; }
CONF=$LEGAL/prod.conf
FAILS=0
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
pass() { printf '  ok    %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; FAILS=$((FAILS + 1)); }
note() { printf '  WARN  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }

step "1. Shell syntax ($REL)"
n=0
while IFS= read -r -d '' f; do
  n=$((n + 1))
  case "$(head -n 1 "$f")" in
    *bash*) bash -n "$f" 2>"$TMP/err" && pass "bash -n ${f#"$REL"/}" || { fail "bash -n ${f#"$REL"/}: $(head -n 2 "$TMP/err")"; } ;;
    *) sh -n "$f" 2>"$TMP/err" && pass "sh -n ${f#"$REL"/}" || { fail "sh -n ${f#"$REL"/}: $(head -n 2 "$TMP/err")"; } ;;
  esac
done < <(find "$REL" -path "$REL/upstream" -prune -o -type f \( -name '*.sh' -o -name 'tfsc-backup' \) -print0)
[ "$n" -gt 0 ] || fail "no shell scripts found"
for f in "$REL"/upstream/*.sh; do [ -f "$f" ] && { bash -n "$f" && pass "bash -n ${f#"$REL"/} (upstream reference)" || fail "bash -n ${f#"$REL"/}"; }; done
if command -v shellcheck >/dev/null 2>&1; then
  for f in "$REL/agreements.sh" "$LEGAL/agreements.sh" "$LEGAL/validate-release.sh"; do
    [ -f "$f" ] || continue
    shellcheck -x -S warning "$f" >"$TMP/sc" 2>&1 && pass "shellcheck ${f#"$REL"/}" || fail "shellcheck ${f#"$REL"/}: $(grep -c 'SC[0-9]' "$TMP/sc") finding(s)"
  done
else
  note "shellcheck not installed: ShellCheck NOT RUN"
fi
if [ -f "$REL/agreements.sh" ] && [ -f "$LEGAL/agreements.sh" ]; then
  cmp -s "$REL/agreements.sh" "$LEGAL/agreements.sh" && pass "agreements.sh at the top is the production script" || fail "agreements.sh at the top differs from deploy/agreements-legal/agreements.sh"
fi

step "2. YAML and compose rendering (placeholder values only)"
cat > "$TMP/validation.env" <<'EOF'
TFSC_DOMAIN=legal.24-7consultancy.cloud
POSTGRES_PASSWORD=validation-only-not-a-secret
BACKUP_PASSPHRASE=validation-only-not-a-secret
TFSC_SECRET_KEY=validation-only-not-a-secret
EOF
if python3 -c 'import yaml' 2>/dev/null; then
  while IFS= read -r -d '' f; do
    python3 -c 'import sys,yaml; list(yaml.safe_load_all(open(sys.argv[1])))' "$f" 2>"$TMP/err" && pass "YAML ${f#"$REL"/}" || fail "YAML ${f#"$REL"/}: $(tail -n 1 "$TMP/err")"
  done < <(find "$REL" -type f \( -name '*.yaml' -o -name '*.yml' \) -not -path '*/node_modules/*' -print0)
else
  note "PyYAML not installed: YAML is checked by docker compose config only"
fi
PROD_JSON='' UP_JSON=''
if docker compose version >/dev/null 2>&1; then
  if env -i PATH="$PATH" HOME="${HOME:-/root}" TFSC_AGREEMENTS_IMAGE=tfsc-agreements:validation \
      docker compose -p tfsc-agreements-legal --project-directory "$LEGAL" -f "$LEGAL/compose.yaml" --env-file "$TMP/validation.env" config --format json > "$TMP/prod.json" 2>"$TMP/err"; then
    pass "production compose renders (docker compose config)"; PROD_JSON=$TMP/prod.json
    env -i PATH="$PATH" HOME="${HOME:-/root}" TFSC_AGREEMENTS_IMAGE=tfsc-agreements:validation \
      docker compose -p tfsc-agreements-legal --project-directory "$LEGAL" -f "$LEGAL/compose.yaml" --env-file "$TMP/validation.env" config -q && pass "docker compose config -q" || fail "docker compose config -q"
    env -i PATH="$PATH" HOME="${HOME:-/root}" docker compose -p x --project-directory "$LEGAL" -f "$LEGAL/compose.yaml" --env-file "$TMP/validation.env" config -q >/dev/null 2>&1 \
      && fail "the production compose renders without TFSC_AGREEMENTS_IMAGE (it must refuse to)" || pass "the production compose refuses to run without agreements.sh (TFSC_AGREEMENTS_IMAGE required)"
  else
    fail "production compose does not render: $(head -n 3 "$TMP/err")"
  fi
  for up in "$REL/upstream/compose.upstream.yaml" "$REL/deploy/agreements/compose.yaml"; do
    [ -f "$up" ] || continue
    if env -i PATH="$PATH" HOME="${HOME:-/root}" docker compose -p upstream-check --project-directory "$(dirname "$up")" -f "$up" --env-file "$TMP/validation.env" config --format json > "$TMP/up.json" 2>"$TMP/err"; then
      pass "upstream compose renders (${up#"$REL"/}, for comparison only)"; UP_JSON=$TMP/up.json
    else
      note "upstream compose ${up#"$REL"/} does not render: $(head -n 1 "$TMP/err")"
    fi
    break
  done
else
  note "docker compose not available: compose rendering and compose checks NOT RUN"
fi
for f in "$REL"/compose.yaml "$REL"/docker-compose.yml "$REL"/docker-compose.yaml "$REL"/upstream/compose.yaml; do
  [ -f "$f" ] && fail "${f#"$REL"/} would be picked up by docker compose by accident (production uses deploy/agreements-legal/compose.yaml only)"
done

step "3. Production requirements, migrations, files that must never ship"
python3 "$LEGAL/legalcheck.py" release "$CONF" "$REL" ${PROD_JSON:+"$PROD_JSON"} ${UP_JSON:+"$UP_JSON"} || FAILS=$((FAILS + 1))

printf '\n'
if [ "$FAILS" = 0 ]; then echo "RELEASE CHECK PASSED (read every WARN line)."; else echo "RELEASE CHECK FAILED: $FAILS failing step(s). Do not take this release to the server."; fi
[ "$FAILS" = 0 ]
