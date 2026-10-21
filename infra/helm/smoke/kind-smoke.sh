#!/usr/bin/env bash
# Helm smoke test on a local kind cluster: install the chart with its real images and check that it works end to end.
#
#   infra/helm/smoke/kind-smoke.sh [up|test|upgrade|down|all]      (default: all = up, test, upgrade; down is separate)
#
# up:      kind cluster (one node), ingress-nginx, test-only PostgreSQL/Redis/RustFS (deps.yaml), secrets generated on
#          the spot (never written to the repository), the chart with values-kind.yaml (`helm upgrade --install --wait`)
# test:    through the ingress, over TLS checked against the generated certificate: health with the migrated revision,
#          first Super Admin setup, a live save, a 50-row paste (served by the bulk pods), a photo upload (ClamAV scan,
#          stored in the bucket) and read back, Agreements health; inside the cluster: /metrics with the token from an
#          allowed pod, and the NetworkPolicy blocking a pod that is not part of the platform; jobs, HPA, PDB present
# upgrade: `helm upgrade` again: the migration and storage-init hooks run again, pods roll, health is checked again
#
# Needs docker, kind, kubectl, helm, openssl, curl, and the images built from this repository:
#   docker build -f apps/crm-api/Dockerfile -t tfsc-crm:2 . && docker build -f apps/agreements/Dockerfile -t tfsc-agreements:4 .
# Behind a TLS-inspecting proxy, KIND_EXTRA_CA=/path/to/full-ca-bundle.crt becomes the node's trust store.
# On a cgroup v1 host the kubelet is allowed to start anyway (Kubernetes 1.35 refuses cgroup v1 by default).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../../.." && pwd)
CLUSTER=${CLUSTER:-tfsc-smoke}
NS=tfsc
REL=tfsc
INGRESS_NGINX=${INGRESS_NGINX:-controller-v1.13.3}
STATE=${STATE:-$here/.state}   # generated secrets and the test certificate (gitignored)
IMAGES=(tfsc-crm:2 tfsc-agreements:4 postgres:16-alpine redis:7-alpine rustfs/rustfs:1.0.1 clamav/clamav:1.4.6)
k() { kubectl --context "kind-$CLUSTER" "$@"; }
say() { printf '\n== %s\n' "$*"; }
rnd() { openssl rand -hex 24; }

up() {
  mkdir -p "$STATE" && chmod 700 "$STATE"
  if ! kind get clusters | grep -qx "$CLUSTER"; then
    say "cluster $CLUSTER"
    cp "$here/kind.yaml" "$STATE/kind.yaml"
    if [ -n "${KIND_EXTRA_CA:-}" ]; then
      printf '    extraMounts:\n      - { hostPath: %s, containerPath: /etc/ssl/certs/ca-certificates.crt, readOnly: true }\n' "$KIND_EXTRA_CA" >> "$STATE/kind.yaml"
    fi
    if docker info --format '{{.CgroupVersion}}' | grep -qx 1; then  # Kubernetes >= 1.35 refuses cgroup v1 unless told
      printf 'kubeadmConfigPatches:\n  - |\n    kind: KubeletConfiguration\n    failCgroupV1: false\n' >> "$STATE/kind.yaml"
    fi
    kind create cluster --name "$CLUSTER" --config "$STATE/kind.yaml" --wait 120s
  fi
  say "images (loaded from the local Docker, never pulled by the node)"
  for img in "${IMAGES[@]}"; do docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img"; done
  kind load docker-image --name "$CLUSTER" "${IMAGES[@]}"

  say "ingress-nginx ($INGRESS_NGINX)"
  k apply -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/$INGRESS_NGINX/deploy/static/provider/kind/deploy.yaml" >/dev/null
  k -n ingress-nginx rollout status deploy/ingress-nginx-controller --timeout=300s

  say "secrets and certificate"
  if [ ! -f "$STATE/secrets.env" ]; then
    cat > "$STATE/secrets.env" <<EOF
PGPW=$(rnd)
S3SK=$(rnd)
BRIDGE=$(rnd)
METRICS=$(rnd)
ADMINPW=Smoke-$(rnd)
FIELDKEY=$(openssl rand -base64 32)
EOF
    chmod 600 "$STATE/secrets.env"
  fi
  # shellcheck disable=SC1091
  . "$STATE/secrets.env"
  [ -f "$STATE/tls.crt" ] || openssl req -x509 -newkey rsa:2048 -nodes -days 3 -subj "/CN=crm.tfsc.test" \
    -addext "subjectAltName=DNS:crm.tfsc.test,DNS:agreements.tfsc.test,DNS:files.tfsc.test" -keyout "$STATE/tls.key" -out "$STATE/tls.crt" 2>/dev/null
  k create namespace tfsc-deps --dry-run=client -o yaml | k apply -f - >/dev/null
  k create namespace "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
  k -n tfsc-deps create secret generic deps --dry-run=client -o yaml --from-literal=POSTGRES_PASSWORD="$PGPW" \
    --from-literal=S3_ACCESS_KEY=tfsc-crm --from-literal=S3_SECRET_KEY="$S3SK" | k apply -f - >/dev/null
  k -n "$NS" create secret generic tfsc-crm --dry-run=client -o yaml \
    --from-literal=CRM_DATABASE_URL="postgresql+psycopg://crm:$PGPW@postgres.tfsc-deps.svc:5432/crm" \
    --from-literal=CRM_REDIS_URL="redis://redis.tfsc-deps.svc:6379/0" \
    --from-literal=CRM_PHONE_KEY_SECRET="$(rnd)" --from-literal=CRM_FIELD_KEY="$FIELDKEY" \
    --from-literal=CRM_TFSC_BRIDGE_SECRET="$BRIDGE" --from-literal=CRM_OIDC_CLIENT_SECRET="$(rnd)" \
    --from-literal=CRM_METRICS_TOKEN="$METRICS" --from-literal=CRM_S3_ACCESS_KEY=tfsc-crm --from-literal=CRM_S3_SECRET_KEY="$S3SK" | k apply -f - >/dev/null
  k -n "$NS" create secret generic tfsc-agreements --dry-run=client -o yaml \
    --from-literal=DATABASE_URL="postgresql+psycopg://tfsc:$PGPW@postgres.tfsc-deps.svc:5432/tfsc" \
    --from-literal=TFSC_CRM_SHARED_SECRET="$BRIDGE" --from-literal=TFSC_OIDC_CLIENT_SECRET="$(rnd)" | k apply -f - >/dev/null
  k -n "$NS" create secret tls tfsc-tls --cert "$STATE/tls.crt" --key "$STATE/tls.key" --dry-run=client -o yaml | k apply -f - >/dev/null

  say "test-only dependencies"
  k apply -f "$here/deps.yaml" >/dev/null
  for d in postgres redis rustfs; do k -n tfsc-deps rollout status "deploy/$d" --timeout=300s; done

  say "helm upgrade --install (hooks: migrate, storage-init; then every pod ready)"
  time helm --kube-context "kind-$CLUSTER" upgrade --install "$REL" "$root/infra/helm/tfsc-platform" -n "$NS" \
    -f "$here/values-kind.yaml" --wait --timeout 20m
  k -n "$NS" get pods -o wide
}

curl_() { curl -sS --cacert "$STATE/tls.crt" --resolve crm.tfsc.test:443:127.0.0.1 --resolve agreements.tfsc.test:443:127.0.0.1 "$@"; }
check() {  # check <description> <command...>: run, print PASS/FAIL, count failures
  if "${@:2}"; then echo "PASS  $1"; else echo "FAIL  $1"; failures=$((failures + 1)); fi
}

test_() {
  # shellcheck disable=SC1091
  . "$STATE/secrets.env"
  failures=0
  jar="$STATE/cookies"; rm -f "$jar"
  say "checks"
  health=$(curl_ https://crm.tfsc.test/api/health)
  head=$(basename "$(printf '%s\n' "$root"/apps/crm-api/migrations/versions/[0-9]*.py | sort | tail -n 1)" .py)
  check "CRM health through the ingress over verified TLS, database at $head" grep -q "\"database\":\"$head\"" <<<"$health"
  check "Agreements health through the ingress" bash -c "curl -sS --cacert '$STATE/tls.crt' --resolve agreements.tfsc.test:443:127.0.0.1 -o /dev/null -w '%{http_code}' https://agreements.tfsc.test/api/health | grep -qx 200"
  check "migration job completed" bash -c "kubectl --context kind-$CLUSTER -n $NS get jobs -o jsonpath='{range .items[*]}{.metadata.name}={.status.succeeded}{\"\\n\"}{end}' | grep -q 'crm-migrate-.*=1'"
  check "storage-init job completed (buckets, object lock checked)" bash -c "kubectl --context kind-$CLUSTER -n $NS get jobs -o jsonpath='{range .items[*]}{.metadata.name}={.status.succeeded}{\"\\n\"}{end}' | grep -q 'crm-storage-init-.*=1'"
  check "HPA and PodDisruptionBudget exist" bash -c "kubectl --context kind-$CLUSTER -n $NS get hpa/$REL-crm-api pdb/$REL-crm-api >/dev/null"

  key=$(k -n "$NS" exec "deploy/$REL-crm-api" -- python -c "from tfsc_crm.domain import bootstrap; print(bootstrap.read_setup_key() or '')" | tr -d '\r')
  if [ -n "$key" ]; then
    curl_ -c "$jar" -o "$STATE/setup.json" -H 'Content-Type: application/json' -H 'Origin: https://crm.tfsc.test' \
      -d "{\"setup_key\":\"$key\",\"name\":\"Smoke Admin\",\"email\":\"smoke-admin@tfsc.test\",\"password\":\"$ADMINPW\"}" https://crm.tfsc.test/api/auth/setup
  else  # already set up by an earlier run: sign in
    curl_ -c "$jar" -o "$STATE/setup.json" -H 'Content-Type: application/json' -H 'Origin: https://crm.tfsc.test' \
      -d "{\"email\":\"smoke-admin@tfsc.test\",\"password\":\"$ADMINPW\"}" https://crm.tfsc.test/api/auth/login
  fi
  csrf=$(sed -n 's/.*"csrf":"\([^"]*\)".*/\1/p' "$STATE/setup.json")
  uid=$(sed -n 's/.*"user":{"id":"\([^"]*\)".*/\1/p' "$STATE/setup.json")
  check "first Super Admin set up / signed in (session + CSRF token)" test -n "$csrf"
  H=(-b "$jar" -H "X-CSRF-Token: $csrf" -H 'Origin: https://crm.tfsc.test')
  code=$(curl_ "${H[@]}" -o "$STATE/save.json" -w '%{http_code}' -H 'Content-Type: application/json' \
    -d '{"name":"Smoke Test Clinic","phone":"(415) 555-0142","called":true}' https://crm.tfsc.test/api/activities)
  check "live save (POST /api/activities)" test "$code" = 200
  rows=$(for i in $(seq 1 50); do printf '{"name":"smoke clinic %d","phone":"(415) 555-%04d","called":"Yes"},' "$i" "$((1000 + i))"; done)
  code=$(curl_ "${H[@]}" -o "$STATE/batch.json" -w '%{http_code}' -H 'Content-Type: application/json' \
    -d "{\"request_key\":\"smoke-$(date +%s)\",\"source\":\"grid\",\"rows\":[${rows%,}]}" https://crm.tfsc.test/api/activities/batch)
  check "50-row paste saved (POST /api/activities/batch)" grep -q '"saved":50' "$STATE/batch.json"
  check "the paste was served by the bulk pool (ingress route)" bash -c \
    "kubectl --context kind-$CLUSTER -n $NS logs deploy/$REL-crm-bulk --since=10m | grep -q '\"route\": \"/api/activities/batch\"'"
  python3 -c "import struct, zlib, sys
def chunk(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d))
sys.stdout.buffer.write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 64, 64, 8, 2, 0, 0, 0))
                        + chunk(b'IDAT', zlib.compress(b''.join(b'\x00' + b'\x30\x80\xc0' * 64 for _ in range(64)))) + chunk(b'IEND', b''))" > "$STATE/one.png"
  code=$(curl_ "${H[@]}" -o "$STATE/photo.json" -w '%{http_code}' -F "file=@$STATE/one.png;type=image/png" "https://crm.tfsc.test/api/users/$uid/photo")
  check "photo upload: scanned by ClamAV and stored in the bucket" test "$code" = 200
  code=$(curl_ -b "$jar" -o /dev/null -w '%{http_code}' -L "https://crm.tfsc.test/api/users/$uid/photo")
  check "photo read back" test "$code" = 200

  probe() {  # probe <name> <labels> <url> <expect>: a one-off pod fetching a URL inside the cluster
    k -n "$NS" delete pod "$1" --ignore-not-found >/dev/null
    k -n "$NS" run "$1" --image tfsc-crm:2 --image-pull-policy Never --restart Never --labels "$2" --command -- python -c \
      "import sys,urllib.request as u
try: print(u.urlopen(u.Request('$3', headers={'Authorization': 'Bearer $METRICS'}), timeout=8).status)
except Exception as e: print(type(e).__name__)" >/dev/null
    k -n "$NS" wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$1" --timeout=90s >/dev/null || true
    out=$(k -n "$NS" logs "$1" 2>/dev/null | tail -n 1); k -n "$NS" delete pod "$1" --wait=false >/dev/null
    [[ "$out" =~ ^($4)$ ]] || { echo "      ($1 got: $out)"; return 1; }
  }
  check "/metrics with the token from a platform pod (NetworkPolicy allows)" probe np-allowed app.kubernetes.io/part-of=tfsc-platform "http://$REL-crm:8000/metrics" 200
  check "a pod outside the platform cannot reach the API (NetworkPolicy blocks)" probe np-blocked app=outsider "http://$REL-crm:8000/api/health" 'TimeoutError|URLError'
  echo
  if [ "$failures" != 0 ]; then echo "$failures CHECK(S) FAILED"; return 1; fi
  echo "ALL CHECKS PASSED"
}

upgrade() {
  say "helm upgrade (second revision: hooks run again, pods roll)"
  time helm --kube-context "kind-$CLUSTER" upgrade "$REL" "$root/infra/helm/tfsc-platform" -n "$NS" -f "$here/values-kind.yaml" \
    --set observability.logLevel=DEBUG --wait --timeout 15m
  helm --kube-context "kind-$CLUSTER" history "$REL" -n "$NS"
  curl_ https://crm.tfsc.test/api/health; echo
}

down() { kind delete cluster --name "$CLUSTER"; rm -rf "$STATE"; }

case "${1:-all}" in
  up) up ;; test) test_ ;; upgrade) upgrade ;; down) down ;;
  all) up && test_ && upgrade && test_ ;;
  *) echo "usage: $0 [up|test|upgrade|down|all]" >&2; exit 2 ;;
esac
