#!/usr/bin/env bash
# Build the single-server (VPS) deployment ZIP from the committed tree: what the server needs to build and run the
# platform with Docker, the installer at the top (tfsc.sh) and the guide (START-HERE.md). Nothing uncommitted can get
# in (git archive), and the archive is checked for files that must never ship (.env, workbooks, signature images).
#   tools/make_vps_bundle.sh [out-dir]      -> <out-dir>/TFSC-VPS-Deploy-<date>-<commit>.zip (default handoff-out/)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
out=${1:-handoff-out}
mkdir -p "$out"
out=$(cd "$out" && pwd)
sha=$(git rev-parse --short HEAD)
name=TFSC-VPS-Deploy-$(date -u +%Y-%m-%d)-$sha
[ -z "$(git status --porcelain -- apps packages infra deploy .dockerignore)" ] || echo "note: uncommitted changes are NOT included (built from $sha)" >&2

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git archive --format=tar --prefix="$name/" HEAD -- \
  apps/crm-api apps/crm-web apps/agreements packages infra/compose infra/keycloak/realm-tfsc.json infra/observability \
  docs/runbooks deploy/vps .dockerignore \
  ':(exclude)apps/crm-api/tests' ':(exclude)apps/crm-api/tests_v2' ':(exclude)apps/agreements/tests' \
  ':(exclude)apps/agreements/private-assets' ':(exclude)apps/crm-web/e2e' ':(exclude)apps/crm-web/test' ':(exclude)packages/*/python/tests' ':(exclude)packages/*/ts/test' \
  | tar -x -C "$work"
mv "$work/$name/deploy/vps/README.md" "$work/$name/START-HERE.md"
mv "$work/$name/deploy/vps/tfsc.sh" "$work/$name/tfsc.sh"
rmdir "$work/$name/deploy/vps" "$work/$name/deploy"
printf 'commit %s\nbuilt %s\n' "$(git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%MZ)" > "$work/$name/VERSION"

bad=$(cd "$work" && find . \( -name '*.xlsx' -o -name '.env' -o -name 'backup-offsite.env' -o -name 'users.json' \
  -o -path '*private-assets/signatures/*' -o -name 'setup-key.txt' -o -path '*/.state/*' \) -print)
[ -z "$bad" ] || { echo "refusing to build: files that must never ship:" >&2; echo "$bad" >&2; exit 1; }

rm -f "$out/$name.zip"
(cd "$work" && zip -qr -X "$out/$name.zip" "$name")
echo "$out/$name.zip ($(du -h "$out/$name.zip" | cut -f1), $(unzip -Z1 "$out/$name.zip" | grep -vc '/$') files)"
