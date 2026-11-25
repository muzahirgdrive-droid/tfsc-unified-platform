#!/usr/bin/env bash
# Build the PRODUCTION package of TFSC Agreements for legal.24-7consultancy.cloud from the committed tree.
#   tools/make_agreements_legal_bundle.sh [out-dir]   -> <out-dir>/TFSC-Agreements-Legal-<version>-<date>-<commit>.zip
#
# Layout of the package:
#   agreements.sh                     the PRODUCTION script (same file as deploy/agreements-legal/agreements.sh)
#   START-HERE.md, PRODUCTION-ADAPTATION.md
#   deploy/agreements-legal/          production compose, prod.conf, checks, guides, reviews
#   deploy/agreements/initdb/         upstream database bootstrap (referenced, first start of an empty database only)
#   upstream/                         the upstream deployment files, renamed so nobody runs them by accident
#   apps/agreements, packages/bridge-contract, infra/compose/postgres, .dockerignore   the release itself (as upstream)
# Nothing uncommitted gets in (git archive). The package is refused unless deploy/agreements-legal/validate-release.sh passes.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
out=${1:-handoff-out}; mkdir -p "$out"; out=$(cd "$out" && pwd)
sha=$(git rev-parse --short HEAD)
version=$(sed -n "s/^VERSION='\([0-9.]*\)'.*/\1/p" apps/agreements/portal.py | head -n 1)
build=$(sed -n 's/^PACKAGE_BUILD=\([^ ]*\).*/\1/p' deploy/agreements-legal/prod.conf)
name=TFSC-Agreements-Legal-${build:-$version}-$(date -u +%Y-%m-%d)-$sha
[ -z "$(git status --porcelain -- apps/agreements packages/bridge-contract infra/compose/postgres deploy/agreements deploy/agreements-legal .dockerignore)" ] \
  || echo "note: uncommitted changes are NOT included (built from $sha)" >&2
work=$(mktemp -d); trap 'rm -rf "$work"' EXIT
git archive --format=tar --prefix="$name/" HEAD -- \
  apps/agreements packages/bridge-contract infra/compose/postgres deploy/agreements deploy/agreements-legal .dockerignore \
  ':(exclude)apps/agreements/tests' ':(exclude)apps/agreements/private-assets' \
  ':(exclude)packages/bridge-contract/python/tests' ':(exclude)packages/bridge-contract/ts' \
  | tar -x -C "$work"
d=$work/$name
# upstream deployment files: kept for comparison, renamed so docker compose and an operator cannot pick them up
mkdir -p "$d/upstream"
mv "$d/deploy/agreements/compose.yaml" "$d/upstream/compose.upstream.yaml"
mv "$d/deploy/agreements/agreements.sh" "$d/upstream/agreements.upstream.sh"; chmod 644 "$d/upstream/agreements.upstream.sh"
mv "$d/deploy/agreements/Caddyfile" "$d/upstream/Caddyfile.upstream"
mv "$d/deploy/agreements/README.md" "$d/upstream/START-HERE.upstream.md"
mv "$d/deploy/agreements/backup-offsite.env.example" "$d/upstream/backup-offsite.env.example"
cat > "$d/upstream/README.md" <<'EOF'
# Upstream deployment files (reference only - DO NOT RUN ON legal.24-7consultancy.cloud)

These are the release's own deployment files for a fresh, single-purpose server: they start their own Caddy on ports
80/443, create their own volumes (project `tfsc-agreements`) and restart the whole stack. On the production server use
`../agreements.sh` and `../deploy/agreements-legal/` only. They are kept so the next release can be compared with them
(`deploy/agreements-legal/validate-release.sh` does that).
EOF
cp "$d/deploy/agreements-legal/agreements.sh" "$d/agreements.sh"; chmod 755 "$d/agreements.sh"
cp "$d/deploy/agreements-legal/PRODUCTION-ADAPTATION.md" "$d/PRODUCTION-ADAPTATION.md"
cp "$d/deploy/agreements-legal/UPDATE-AND-ROLLBACK.md" "$d/START-HERE.md"
printf 'commit %s\nbuilt %s\nversion %s\nbuild %s\nvariant legal.24-7consultancy.cloud (production adaptation)\n' "$(git rev-parse HEAD)" "$(date -u +%Y-%m-%dT%H:%MZ)" "$version" "${build:-$version}" > "$d/VERSION"
bad=$(cd "$work" && find . \( -name '*.xlsx' -o -name '.env' -o -name 'backup-offsite.env' -o -name 'compose.local.yaml' -o -name 'users.json' \
  -o -path '*private-assets/signatures/*' -o -name 'setup-key.txt' -o -path '*/.state/*' -o -path '*/agreements-legal/state/*' \
  -o -name '*.dump' -o -name '*.dump.enc' -o -name '*.sql' -o -name '*.db' -o -name '*.pem' -o -name '*.key' \) -print)
[ -z "$bad" ] || { echo "refusing to build: files that must never ship:" >&2; echo "$bad" >&2; exit 1; }
echo "== validating the package before zipping"
"$d/deploy/agreements-legal/validate-release.sh" "$d" > "$out/$name.validation.txt" 2>&1 \
  || { tail -n 30 "$out/$name.validation.txt" >&2; echo "refusing to build: validate-release.sh failed (full output: $out/$name.validation.txt)" >&2; exit 1; }
tail -n 3 "$out/$name.validation.txt"
rm -f "$out/$name.zip"
(cd "$work" && zip -qr -X "$out/$name.zip" "$name")
echo "$out/$name.zip ($(du -h "$out/$name.zip" | cut -f1), $(unzip -Z1 "$out/$name.zip" | grep -vc '/$') files)"
