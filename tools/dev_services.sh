#!/usr/bin/env bash
# Starts a throwaway local PostgreSQL 16 (port 54329) + Redis (port 63799) for development/tests.
# Data lives in .data/dev-services (gitignored). Usage: tools/dev_services.sh start|stop|url
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; D="$ROOT/.data/dev-services"; PGBIN=/usr/lib/postgresql/16/bin
PGPORT=54329; RPORT=63799
run_pg(){ if [ "$(id -u)" = 0 ]; then su postgres -s /bin/bash -c "$*"; else bash -c "$*"; fi; }
case "${1:-start}" in
 start)
  mkdir -p "$D"; [ "$(id -u)" = 0 ] && chown postgres "$D"
  if [ ! -f "$D/pg/PG_VERSION" ]; then run_pg "$PGBIN/initdb -D $D/pg -U postgres --auth=trust -E UTF8 >/dev/null"; fi
  run_pg "$PGBIN/pg_ctl -D $D/pg -o '-p $PGPORT -k /tmp -c max_connections=300 -c fsync=off -c synchronous_commit=off' -l $D/pg.log -w start >/dev/null" || true
  for db in crm crm_test tfsc tfsc_test; do psql -h /tmp -p $PGPORT -U postgres -tc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || psql -h /tmp -p $PGPORT -U postgres -qc "CREATE DATABASE $db"; done
  redis-server --port $RPORT --daemonize yes --save '' --appendonly no >/dev/null
  echo "postgresql+psycopg://postgres@127.0.0.1:$PGPORT/crm  redis://127.0.0.1:$RPORT/0";;
 stop) run_pg "$PGBIN/pg_ctl -D $D/pg -m fast stop" || true; redis-cli -p $RPORT shutdown nosave || true;;
 url) echo "postgresql+psycopg://postgres@127.0.0.1:$PGPORT/crm";;
esac
