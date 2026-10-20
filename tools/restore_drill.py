"""Backup + restore drill for the CRM database (docs/runbooks/backups-and-restore.md).

1. pg_dump (custom format) of the source database, with its SHA-256.
2. pg_restore into a brand-new database.
3. Checks: same Alembic revision, same row count in every table, and the audit chain verifies (links, content
   hashes, daily anchors) on the restored copy.
Usage: python tools/restore_drill.py postgresql://user@host:port/dbname [workdir]
Exit code 0 only when every check passes. Prints timings; the restored database is dropped at the end.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

src = sys.argv[1]
work = Path(sys.argv[2] if len(sys.argv) > 2 else '.').resolve()
work.mkdir(parents=True, exist_ok=True)
u = urlsplit(src)
dbname = u.path.lstrip('/')
target = f'{dbname}_restore_drill'
dst = urlunsplit(u._replace(path='/' + target))
admin = urlunsplit(u._replace(path='/postgres'))
dump = work / f'{dbname}.dump'
report: dict = {'source': dbname}


def run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout  # noqa: S603 - fixed tool names, no shell


def counts(url: str) -> dict[str, int]:
    tables = run('psql', url, '-Atc', "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").split()
    sql = ' UNION ALL '.join(f"SELECT '{t}', count(*) FROM \"{t}\"" for t in tables)  # noqa: S608 - names from pg_tables, quoted
    return {line.split('|')[0]: int(line.split('|')[1]) for line in run('psql', url, '-Atc', sql).splitlines()}


t0 = time.perf_counter()
run('pg_dump', '-Fc', '-Z', '6', '-f', str(dump), src)
report['dump_seconds'] = round(time.perf_counter() - t0, 2)
report['dump_bytes'] = dump.stat().st_size
report['dump_sha256'] = hashlib.sha256(dump.read_bytes()).hexdigest()
run('psql', admin, '-c', f'DROP DATABASE IF EXISTS "{target}"', '-c', f'CREATE DATABASE "{target}"')
try:
    t1 = time.perf_counter()
    run('pg_restore', '--no-owner', '--exit-on-error', '-d', dst, str(dump))
    report['restore_seconds'] = round(time.perf_counter() - t1, 2)
    a, b = counts(src), counts(dst)
    report['tables'] = len(a)
    report['rows'] = sum(a.values())
    report['row_counts_equal'] = a == b
    rev = "SELECT version_num FROM alembic_version"
    report['revision_equal'] = run('psql', src, '-Atc', rev) == run('psql', dst, '-Atc', rev)
    env = {**os.environ, 'CRM_DATABASE_URL': urlunsplit(u._replace(scheme='postgresql+psycopg', path='/' + target)), 'CRM_MODE': 'test'}
    code = ("from sqlalchemy import text\nfrom tfsc_crm.db.session import system_tx\nfrom tfsc_crm.domain import audit\nimport json\n"
            "with system_tx() as c:\n    orgs=[r[0] for r in c.execute(text('select id from orgs'))]\n"
            "    print(json.dumps([audit.verify_chain(c, o)['ok'] for o in orgs]))")
    api = Path(__file__).resolve().parents[1] / 'apps' / 'crm-api'
    proc = subprocess.run([sys.executable, '-c', code], cwd=api, env=env, check=True, capture_output=True, text=True)  # noqa: S603 - our own code
    ok = json.loads(proc.stdout.strip().splitlines()[-1])
    report['audit_chain_ok'] = all(ok)
finally:
    run('psql', admin, '-c', f'DROP DATABASE IF EXISTS "{target}"')
report['passed'] = bool(report.get('row_counts_equal') and report.get('revision_equal') and report.get('audit_chain_ok'))
print(json.dumps(report, indent=1))
sys.exit(0 if report['passed'] else 1)
