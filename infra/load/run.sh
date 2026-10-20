#!/usr/bin/env bash
# Run one k6 profile against a running CRM and keep the summary + CPU samples (infra/load/results/<name>-*).
# Usage: infra/load/run.sh <name> <base-url> AGENTS=550 SUPS=50 PASTERS=3 THINK=20 HOLD=5m RAMP=2m
set -euo pipefail
name=$1; base=$2; shift 2
here=$(cd "$(dirname "$0")" && pwd)
mkdir -p "$here/results" && chmod a+rwx "$here/results"
envs=(); for kv in "$@"; do envs+=(-e "$kv"); done
secs=$(( 60 * 9 ))
(vmstat 10 $((secs / 10)) > "$here/results/$name-vmstat.txt" &)
docker run --rm --network host -v "$here:/load" ${K6_IMAGE:-grafana/k6:0.54.0} run --quiet \
  --summary-export "/load/results/$name-summary.json" -e BASE="$base" "${envs[@]}" /load/crm.js | tee "$here/results/$name-k6.txt"
