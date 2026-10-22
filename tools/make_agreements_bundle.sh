#!/usr/bin/env bash
# Build the TFSC Agreements-only deployment ZIP (no CRM, SSO or file storage) from the committed tree: what one server
# needs to build and run Agreements with Docker, the installer at the top (agreements.sh) and the guide (START-HERE.md).
# Nothing uncommitted can get in (git archive), and the archive is checked for files that must never ship.
#   tools/make_agreements_bundle.sh [out-dir]   -> <out-dir>/TFSC-Agreements-Deploy-<date>-<commit>.zip (default handoff-out/)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
out=${1:-handoff-out}
mkdir -p "$out"
out=$(cd "$out" && pwd)
sha=$(git rev-parse --short HEAD)
name=TFSC-Agreements-Deploy-$(date -u +%Y-%m-%d)-$sha
[ -z "$(git status --porcelain -- apps/agreements packages/bridge-contract infra/compose/postgres deploy/agreements .dockerignore)" ] \
  || echo "note: uncommitted changes are NOT included (built from $sha)" >&2

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
git archive --format=tar --prefix="$name/" HEAD -- \
  apps/agreements packages/bridge-contract infra/compose/postgres deploy/agreements .dockerignore \
  ':(exclude)apps/agreements/tests' ':(exclude)apps/agreements/private-assets' \
  ':(exclude)packages/bridge-contract/python/tests' ':(exclude)packages/bridge-contract/ts' \
  | tar -x -C "$work"
mv "$work/$name/deploy/agreements/README.md" "$work/$name/START-HERE.md"
mv "$work/$name/deploy/agreements/agreements.sh" "$work/$name/agreements.sh"
printf 'commit %s\nbuilt %s\n' "$(git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%MZ)" > "$work/$name/VERSION"

bad=$(cd "$work" && find . \( -name '*.xlsx' -o -name '.env' -o -name 'backup-offsite.env' -o -name 'users.json' \
  -o -path '*private-assets/signatures/*' -o -name 'setup-key.txt' -o -path '*/.state/*' \) -print)
[ -z "$bad" ] || { echo "refusing to build: files that must never ship:" >&2; echo "$bad" >&2; exit 1; }

rm -f "$out/$name.zip"
(cd "$work" && zip -qr -X "$out/$name.zip" "$name")
echo "$out/$name.zip ($(du -h "$out/$name.zip" | cut -f1), $(unzip -Z1 "$out/$name.zip" | grep -vc '/$') files)"
