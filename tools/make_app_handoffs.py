"""Build the two per-app handoff ZIPs of §29: TFSC-CRM-Handoff-<date>.zip and TFSC-Compliance-Handoff-<date>.zip.

Each holds the app's tracked source (plus the compiled CRM web app) and a regenerated _HANDOFF/ folder:
authored documents from docs/handoff/apps/<app>/ ({DATE} and {COMMIT} filled in), and generated inventories —
API-INVENTORY.json (routes), DATABASE-SCHEMA.json (a database migrated to head, then introspected),
ENVIRONMENT-NAMES.json (names only), SERVICE-CATALOG.json, previews/*.png and MANIFEST.sha256.
Refuses to package workbooks, private signature assets, .env files or load-test logins.

Usage: python tools/make_app_handoffs.py OUT_DIR [--previews DIR] [--admin-db URL]
  --admin-db  PostgreSQL admin URL used to create a throwaway database for the CRM schema
              (default: CRM_TEST_DATABASE_URL or the dev services from tools/dev_services.sh)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = re.compile(r'(\.xlsx?$|\.xlsm$|(^|/)\.env$|private-assets/signatures/(?!\.gitkeep$)|infra/load/users\.json$|infra/load/results/)', re.I)
APPS = {
    'crm': {'folder': 'TFSCSalesCRM', 'zip': 'TFSC-CRM-Handoff', 'previews': ('desktop-', 'mobile-'),
            'include': lambda p: not p.startswith('apps/agreements/')},
    'agreements': {'folder': 'ScribeAlignContractStudio', 'zip': 'TFSC-Compliance-Handoff', 'previews': ('tfsc-',),
                   'include': lambda p: p.startswith(('apps/agreements/', 'packages/bridge-contract/'))},
}
SERVICES = {
    'crm': [
        {'name': 'crm-api', 'command': 'uvicorn tfsc_crm.main:app --workers N --proxy-headers', 'port': 8000, 'health': '/api/health',
         'role': 'Interactive API + compiled web app + SSE. Stateless; scale out (one Redis subscription per worker).'},
        {'name': 'crm-bulk', 'command': 'same image and command as crm-api', 'port': 8000, 'health': '/api/health',
         'role': 'Refine & Save pastes, XLSX/CSV and legacy imports (routed by the edge/Ingress) so bulk CPU never slows crm-api.'},
        {'name': 'crm-dispatcher', 'command': 'python -m tfsc_crm.workers.dispatcher', 'port': None,
         'role': 'Outbox -> SSE broker and TFSC deliveries; FOR UPDATE SKIP LOCKED, retries, dead letters. 1+ replicas.'},
        {'name': 'crm-scheduler', 'command': 'python -m tfsc_crm.workers.scheduler', 'port': None,
         'role': 'Flag SLA, overdue follow-ups, audit anchors, rollup reconcile, partitions, session cleanup. Exactly 1 replica.'},
        {'name': 'crm-migrate', 'command': 'alembic upgrade head', 'port': None, 'role': 'One-shot schema migration before each release.'},
        {'name': 'postgres', 'command': 'PostgreSQL 16', 'port': 5432, 'role': 'Databases crm and tfsc (separate roles).'},
        {'name': 'redis', 'command': 'Redis 7', 'port': 6379, 'role': 'Realtime fan-out between API processes, rate limits.'},
        {'name': 'keycloak', 'command': 'Keycloak 26 (realm infra/keycloak/realm-tfsc.json)', 'port': 8080, 'role': 'SSO with TOTP for Supervisor+.'},
        {'name': 'edge', 'command': 'Caddy 2 (infra/compose/Caddyfile) or the Kubernetes Ingress', 'port': 443, 'role': 'TLS, routing, SSE without buffering.'},
    ],
    'agreements': [
        {'name': 'agreements', 'command': 'uvicorn portal:app --workers 2 --proxy-headers', 'port': 8000, 'health': '/api/health',
         'role': 'TFSC portal (builder, history, reviews, accounts) + CRM bridge endpoints; migrates the schema at start-up.'},
        {'name': 'agreements-worker', 'command': 'python worker.py', 'port': None,
         'role': 'Claims generation jobs, renders DOCX/PDF (LibreOffice on Linux), publishes files, emits agreement.issued.'},
        {'name': 'postgres', 'command': 'PostgreSQL 16 (database tfsc)', 'port': 5432, 'role': 'Clients, contracts, reviews, jobs, documents.'},
        {'name': 'contracts volume', 'command': 'TFSC_OUTPUT_ROOT', 'port': None, 'role': 'Published contract files (shared by API and worker).'},
    ],
}


def run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout  # noqa: S603 - fixed commands


def tracked() -> list[str]:
    return [p for p in run(['git', 'ls-files'], cwd=ROOT).splitlines() if (ROOT / p).is_file()]


def env_names(files: list[str]) -> dict[str, list[str]]:
    pat = re.compile(r"""(?:os\.getenv|os\.environ\.get|_bool|environ\.get)\(\s*['"]([A-Z][A-Z0-9_]{2,})['"]|os\.environ\[['"]([A-Z][A-Z0-9_]{2,})['"]\]"""
                     r"""|process\.env\.([A-Z][A-Z0-9_]{2,})""")
    out: dict[str, list[str]] = {}
    for f in files:
        if not f.endswith(('.py', '.ts', '.mjs', '.js')) or '/tests' in f or f.startswith('docs/'):
            continue
        for n, line in enumerate((ROOT / f).read_text(encoding='utf-8', errors='replace').splitlines(), 1):
            for m in pat.finditer(line):
                out.setdefault(next(g for g in m.groups() if g), []).append(f'{f}:{n}')
    return dict(sorted(out.items()))


def crm_api_inventory() -> list[dict]:
    spec = json.loads((ROOT / 'apps/crm-api/openapi.json').read_text(encoding='utf-8'))
    routes = [{'method': m.upper(), 'path': p, 'summary': op.get('summary'), 'tags': op.get('tags', []), 'in_openapi': True}
              for p, ops in spec['paths'].items() for m, op in ops.items()]
    code = ('import json; from tfsc_crm.main import create_app; app = create_app(); '
            'print(json.dumps([{"methods": sorted(getattr(r, "methods", None) or []), "path": r.path} for r in app.routes '
            'if not getattr(r, "include_in_schema", True) and r.path.startswith(("/api", "/metrics"))]))')
    env = {**os.environ, 'CRM_MODE': 'local', 'CRM_ORIGIN': 'http://127.0.0.1:8766', 'CRM_METRICS_TOKEN': 'inventory-only'}
    for r in json.loads(run([sys.executable, '-c', code], cwd=ROOT / 'apps/crm-api', env=env).strip().splitlines()[-1]):
        for m in r['methods'] or ['GET']:
            routes.append({'method': m, 'path': r['path'], 'summary': None, 'tags': [], 'in_openapi': False})
    return sorted(routes, key=lambda r: (r['path'], r['method']))


def agreements_api_inventory(tmp: Path) -> list[dict]:
    code = ('import json, portal; print(json.dumps([{"methods": sorted(getattr(r, "methods", None) or []), "path": r.path, '
            '"name": getattr(r, "name", None)} for r in portal.app.routes]))')
    env = {**os.environ, 'TFSC_MODE': 'local', 'TFSC_DATA_DIR': str(tmp / 'tfsc'), 'DATABASE_URL': f'sqlite:///{tmp}/inv.db'}
    rows = json.loads(run([sys.executable, '-c', code], cwd=ROOT / 'apps/agreements', env=env).strip().splitlines()[-1])
    return sorted(({'method': m, 'path': r['path'], 'handler': r['name']} for r in rows for m in (r['methods'] or ['GET'])
                   if m != 'HEAD'), key=lambda r: (r['path'], r['method']))


def introspect(url: str, postgres: bool) -> dict:
    from sqlalchemy import create_engine, inspect, text
    eng = create_engine(url)
    ins = inspect(eng)
    tables = {}
    with eng.connect() as c:
        rls = dict(c.execute(text("SELECT relname, relrowsecurity FROM pg_class WHERE relkind IN ('r','p') AND relnamespace = 'public'::regnamespace"))
                   .all()) if postgres else {}
        policies: dict[str, list] = {}
        parts: dict[str, list] = {}
        if postgres:
            for t, name, cmd, roles, using in c.execute(text('SELECT tablename, policyname, cmd, roles::text, qual FROM pg_policies WHERE schemaname = \'public\'')):
                policies.setdefault(t, []).append({'name': name, 'command': cmd, 'roles': roles, 'using': using})
            for parent, child in c.execute(text('SELECT p.relname, ch.relname FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent '
                                                'JOIN pg_class ch ON ch.oid = i.inhrelid ORDER BY 1, 2')):
                parts.setdefault(parent, []).append(child)
    partitions = {ch for v in parts.values() for ch in v}
    for t in sorted(ins.get_table_names()):
        if t in partitions or t == 'alembic_version':
            continue
        tables[t] = {
            'columns': [{'name': col['name'], 'type': str(col['type']), 'nullable': col['nullable'], 'default': str(col['default']) if col.get('default') else None}
                        for col in ins.get_columns(t)],
            'primary_key': ins.get_pk_constraint(t).get('constrained_columns', []),
            'foreign_keys': [{'columns': fk['constrained_columns'], 'references': f"{fk['referred_table']}({', '.join(fk['referred_columns'])})"}
                             for fk in ins.get_foreign_keys(t)],
            'unique': [u['column_names'] for u in ins.get_unique_constraints(t)],
            'indexes': [{'name': i['name'], 'columns': i['column_names'], 'unique': i['unique']} for i in ins.get_indexes(t)],
            **({'row_level_security': bool(rls.get(t)), 'policies': policies.get(t, []), 'partitions': len(parts.get(t, []))} if postgres else {}),
        }
    eng.dispose()
    return tables


def crm_schema(admin_url: str) -> dict:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    name = f'crm_handoff_{uuid.uuid4().hex[:8]}'
    admin = create_engine(admin_url, isolation_level='AUTOCOMMIT')
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE {name}'))
    url = make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    try:
        run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ROOT / 'apps/crm-api', env={**os.environ, 'CRM_DATABASE_URL': url})
        head = run([sys.executable, '-m', 'alembic', 'heads'], cwd=ROOT / 'apps/crm-api', env={**os.environ, 'CRM_DATABASE_URL': url}).split()[0]
        return {'source': f'Alembic head {head} on PostgreSQL', 'tables': introspect(url, True)}
    finally:
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)'))
        admin.dispose()


def agreements_schema(tmp: Path) -> dict:
    url = f'sqlite:///{tmp}/schema.db'
    env = {**os.environ, 'TFSC_MODE': 'local', 'TFSC_DATA_DIR': str(tmp / 'tfsc'), 'DATABASE_URL': url}
    run([sys.executable, '-c', 'import database; database.init_db()'], cwd=ROOT / 'apps/agreements', env=env)
    return {'source': 'Alembic head via database.init_db() on SQLite (hosted deployments use PostgreSQL with the same migrations)',
            'tables': introspect(url, False)}


def build(app: str, out: Path, previews: Path | None, admin_url: str, commit: str, today: str, files: list[str], tmp: Path) -> Path:
    cfg = APPS[app]
    stage = tmp / app / cfg['folder']
    chosen = [f for f in files if cfg['include'](f)]
    bad = [f for f in chosen if FORBIDDEN.search(f)]
    if bad:
        raise SystemExit(f'refusing to package private files: {bad[:5]}')
    for f in chosen:
        dst = stage / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    if app == 'crm':
        dist = ROOT / 'apps/crm-web/dist'
        if not (dist / 'index.html').is_file():
            raise SystemExit('apps/crm-web/dist is missing: run npm run build in apps/crm-web first')
        shutil.copytree(dist, stage / 'apps/crm-web/dist')
    h = stage / '_HANDOFF'
    h.mkdir()
    for doc in sorted((ROOT / 'docs/handoff/apps' / app).iterdir()):
        (h / doc.name).write_text(doc.read_text(encoding='utf-8').replace('{DATE}', today).replace('{COMMIT}', commit), encoding='utf-8')
    if app == 'crm':
        api, schema = crm_api_inventory(), crm_schema(admin_url)
    else:
        api, schema = agreements_api_inventory(tmp), agreements_schema(tmp)
    meta = {'generated': today, 'commit': commit}
    (h / 'API-INVENTORY.json').write_text(json.dumps({**meta, 'routes': api}, indent=1), encoding='utf-8')
    (h / 'DATABASE-SCHEMA.json').write_text(json.dumps({**meta, **schema}, indent=1), encoding='utf-8')
    (h / 'ENVIRONMENT-NAMES.json').write_text(json.dumps({**meta, 'note': 'Names only; values live in the deployment secrets.',
                                                          'names': env_names(chosen)}, indent=1), encoding='utf-8')
    (h / 'SERVICE-CATALOG.json').write_text(json.dumps({**meta, 'services': SERVICES[app]}, indent=1), encoding='utf-8')
    if previews and previews.is_dir():
        shots = sorted(p for p in previews.glob('*.png') if p.name.startswith(cfg['previews']))
        if shots:
            (h / 'previews').mkdir()
            for p in shots:
                shutil.copy2(p, h / 'previews' / p.name)
    lines = []
    for p in sorted(stage.rglob('*')):
        if p.is_file():
            lines.append(f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(stage).as_posix()}')
    (h / 'MANIFEST.sha256').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    target = out / f"{cfg['zip']}-{today}.zip"
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(stage.rglob('*')):
            if p.is_file():
                z.write(p, (Path(cfg['folder']) / p.relative_to(stage)).as_posix())
    print(f'{target.name}: {len(lines) + 1} files, {target.stat().st_size / 1e6:.1f} MB, '
          f'{len(api)} routes, {len(schema["tables"])} tables, sha256 {hashlib.sha256(target.read_bytes()).hexdigest()[:16]}…')
    return target


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('out', type=Path)
    ap.add_argument('--previews', type=Path)
    ap.add_argument('--admin-db', default=os.getenv('CRM_TEST_DATABASE_URL', 'postgresql+psycopg://postgres@127.0.0.1:54329/crm_test'))
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    commit = run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT).strip()
    if run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=ROOT).strip():
        print('note: uncommitted changes to tracked files are included as they are on disk', file=sys.stderr)
    files = tracked()
    with tempfile.TemporaryDirectory(prefix='handoff-') as tmp:
        for app in APPS:
            build(app, a.out, a.previews, a.admin_db, commit, date.today().isoformat(), files, Path(tmp))


if __name__ == '__main__':
    main()
