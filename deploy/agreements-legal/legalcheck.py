#!/usr/bin/env python3
"""Production safety checks for TFSC Agreements on legal.24-7consultancy.cloud (standard library only, Python 3.8+).

    legalcheck.py compare  PROD_CONF COMPOSE_JSON RUNNING_DIR
        Before an update: compares what the new compose file would create for the web and worker services with the running
        containers (docker inspect output saved in RUNNING_DIR by agreements.sh). Environment values are compared, never
        printed. Exit 1 when something would change that must not (a variable lost or changed, a volume, network, alias,
        published port or container name), 0 otherwise. Warnings are printed for differences that are allowed.

    legalcheck.py release  PROD_CONF RELEASE_DIR [COMPOSE_JSON] [UPSTREAM_COMPOSE_JSON]
        For a new release: checks the production adaptation (compose, script) against the production requirements, scans
        migrations for destructive changes, compares the upstream compose with the production one (variables added upstream),
        and looks for files that must never ship. Exit 1 on a failure.

Nothing here changes anything; it only reads.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

FAIL, WARN, OK = 'FAIL', 'WARN', 'ok'


class Report:
    def __init__(self):
        self.lines: list[tuple[str, str]] = []

    def add(self, level, text):
        self.lines.append((level, text))

    def ok(self, text):
        self.add(OK, text)

    def warn(self, text):
        self.add(WARN, text)

    def fail(self, text):
        self.add(FAIL, text)

    def finish(self) -> int:
        for level, text in self.lines:
            print(f'  {level:<4}  {text}')
        fails = sum(1 for level, _ in self.lines if level == FAIL)
        warns = sum(1 for level, _ in self.lines if level == WARN)
        print(f'\n  {fails} failed, {warns} warnings, {sum(1 for level, _ in self.lines if level == OK)} passed')
        return 1 if fails else 0


def conf(path) -> dict:
    out = {}
    for line in Path(path).read_text().splitlines():
        line = line.split('#', 1)[0].strip() if not line.lstrip().startswith('#') else ''
        if '=' in line:
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def env_list(items) -> dict:
    out = {}
    for item in items or []:
        k, _, v = item.partition('=')
        out[k] = v
    return out


# ------------------------------------------------------------------ before an update: new config vs running containers
def compare(conf_path, compose_json, running_dir) -> int:
    c = conf(conf_path)
    cfg = json.loads(Path(compose_json).read_text())
    run = Path(running_dir)
    r = Report()
    allow = set((c.get('ALLOW_ENV_CHANGES') or '').split())
    project = c['PROJECT']
    if cfg.get('name') != project:
        r.fail(f"compose project is {cfg.get('name')!r}, production is {project!r}")
    nets_top = cfg.get('networks', {})
    vols_top = cfg.get('volumes', {})
    for svc in (c['WEB'], c['WORKER']):
        spec = cfg.get('services', {}).get(svc)
        f = run / f'{svc}.json'
        if spec is None:
            r.fail(f'service {svc} is missing from the new compose file')
            continue
        if not f.exists():
            r.fail(f'no running container found for {svc}')
            continue
        cont = json.loads(f.read_text())[0]
        img = json.loads((run / f'{svc}.image.json').read_text())[0]
        image_env = env_list(img.get('Config', {}).get('Env'))
        running = env_list(cont.get('Config', {}).get('Env'))
        new = {k: ('' if v is None else str(v)) for k, v in (spec.get('environment') or {}).items()}
        lost, changed, added = [], [], []
        for k, v in running.items():
            if k in new:
                if new[k] != v:
                    changed.append(k)
            elif not (k in image_env and image_env[k] == v):
                lost.append(k)
        for k in new:
            if k not in running:
                added.append(k)
        for k in sorted(lost):
            (r.warn if k in allow else r.fail)(f'{svc}: environment variable {k} is set on the running container but not in the new configuration (value not shown)')
        for k in sorted(changed):
            (r.warn if k in allow else r.fail)(f'{svc}: environment variable {k} would get a different value (values not shown)')
        for k in sorted(added):
            r.warn(f'{svc}: environment variable {k} is new in this release')
        if not (lost or changed):
            r.ok(f'{svc}: all {len(running)} environment variables of the running container are kept with the same values')
        # volumes and bind mounts
        have = {(m.get('Name') if m.get('Type') == 'volume' else m.get('Source'), m.get('Destination')) for m in cont.get('Mounts', [])}
        want = set()
        for v in spec.get('volumes') or []:
            if v.get('type') == 'volume':
                want.add(((vols_top.get(v['source']) or {}).get('name', v['source']), v['target']))
            else:
                want.add((v.get('source'), v.get('target')))
        if have != want:
            r.fail(f'{svc}: mounts differ: running {sorted(have)}, new {sorted(want)}')
        else:
            r.ok(f'{svc}: same volumes at the same paths ({", ".join(sorted(n for n, _ in have))})')
        # networks and aliases (Docker adds the service name, the container name and the short id itself)
        cname = cont.get('Name', '').lstrip('/')
        auto = {svc, cname, cont.get('Id', '')[:12]}
        have_n = {n: sorted(set(x.get('Aliases') or []) - auto) for n, x in cont.get('NetworkSettings', {}).get('Networks', {}).items()}
        want_n = {}
        for key, x in (spec.get('networks') or {}).items():
            name = (nets_top.get(key) or {}).get('name', key)
            want_n[name] = sorted(set((x or {}).get('aliases') or []) - auto)
        if set(have_n) != set(want_n):
            r.fail(f'{svc}: networks differ: running {sorted(have_n)}, new {sorted(want_n)}')
        else:
            r.ok(f'{svc}: same networks ({", ".join(sorted(have_n))})')
        for n in set(have_n) & set(want_n):
            missing = set(have_n[n]) - set(want_n[n])
            if missing:
                r.fail(f'{svc}: network alias(es) {sorted(missing)} on {n} would be lost')
            extra = set(want_n[n]) - set(have_n[n])
            if extra:
                r.warn(f'{svc}: new network alias(es) {sorted(extra)} on {n}')
        # published ports
        have_p = sorted((b.get('HostIp') or '0.0.0.0', str(b.get('HostPort')), k.split('/')[0])
                        for k, binds in (cont.get('HostConfig', {}).get('PortBindings') or {}).items() for b in (binds or []))
        want_p = sorted((p.get('host_ip') or '0.0.0.0', str(p.get('published')), str(p.get('target'))) for p in spec.get('ports') or [])
        if have_p != want_p:
            r.fail(f'{svc}: published ports differ: running {have_p}, new {want_p}')
        elif have_p:
            r.ok(f'{svc}: same published port(s) {have_p}')
        if any(h not in ('127.0.0.1', '::1') for h, _, _ in want_p):
            r.fail(f'{svc}: a port would be published beyond 127.0.0.1: {want_p}')
        # container name (something may refer to it)
        want_name = spec.get('container_name') or f'{project}-{svc}-1'
        if cname != want_name:
            r.fail(f'{svc}: the running container is named {cname!r}; the new one would be {want_name!r} (set container_name in compose.local.yaml)')
        # allowed differences, reported
        cmd = (cont.get('Path') and [cont['Path']] or []) + (cont.get('Args') or [])
        if spec.get('command') and cmd[-len(spec['command']):] != spec['command']:
            r.warn(f'{svc}: command differs: running {shlex.join(cmd)!r}, new {shlex.join(spec["command"])!r}')
        mem = cont.get('HostConfig', {}).get('Memory') or 0
        new_mem = int(spec.get('mem_limit') or 0) if str(spec.get('mem_limit') or '0').isdigit() else spec.get('mem_limit')
        if mem and new_mem and mem != new_mem:
            r.warn(f'{svc}: memory limit differs: running {mem}, new {new_mem}')
        pol = (cont.get('HostConfig', {}).get('RestartPolicy') or {}).get('Name')
        if pol and spec.get('restart') and pol != spec['restart']:
            r.warn(f'{svc}: restart policy differs: running {pol}, new {spec["restart"]}')
    # production application settings
    web, worker = cfg['services'].get(c['WEB'], {}), cfg['services'].get(c['WORKER'], {})
    for item in (c.get('APP_EXPECT') or '').split():
        k, _, v = item.partition('=')
        if k == 'WEB_WORKERS':
            cmd = web.get('command') or []
            got = cmd[cmd.index('--workers') + 1] if '--workers' in cmd else None
        elif k == 'WEB_THREADS':
            continue           # upstream uses WEB_THREADS only as the web pool size; DB_POOL_SIZE is checked directly
        elif k in ('TFSC_GENERATION_WORKERS', 'TFSC_COMPLETION_WORKERS'):
            got = (worker.get('environment') or {}).get(k)
        else:
            got = (web.get('environment') or {}).get(k)
        (r.ok if str(got) == v else r.fail)(f'{k} = {got} (production: {v})')
    return r.finish()


# ------------------------------------------------------------------ a new release: static checks
DESTRUCTIVE = re.compile(r'op\.drop_table|op\.drop_column|op\.drop_index|op\.drop_constraint|op\.alter_column|op\.rename_table|batch_op\.drop_column|'
                         r'\bDROP\s+(TABLE|COLUMN|SCHEMA|DATABASE|INDEX)\b|\bTRUNCATE\b|\bDELETE\s+FROM\b|\bALTER\s+TABLE\b[^;]*\b(DROP|RENAME|TYPE)\b', re.I)
SCRIPT_FORBIDDEN = [
    (r'docker\s+volume\s+(prune|rm)', 'removes Docker volumes'),
    (r'docker\s+(system|image|builder|container|network)\s+prune', 'prunes Docker objects'),
    (r'docker\s+network\s+rm', 'removes a network'),
    (r'\bcompose\b[^\n]*\bdown\b|\bdc\s+down\b', 'stops and removes the whole stack'),
    (r'--remove-orphans', 'removes containers of other definitions'),
    (r'\balembic\b[^\n]*\bdowngrade\b|command\.downgrade', 'downgrades the database'),
    (r'\bdropdb\b|DROP\s+DATABASE|\binitdb\b', 'destroys or re-creates the database'),
    (r'\bufw\b|\biptables\b|\bnft\b', 'changes the firewall'),
    (r'caddy\s+(reload|run|start|adapt\s+--config\s+/etc)', 'touches Caddy'),
    (r'docker\s+build[^\n]*infra/compose/postgres', 'rebuilds the PostgreSQL image'),
    (r'(^|\s)rm\s+-[rf]+\s+[^\n]*(/backups|pre-update)', 'deletes backups'),
]


def release(conf_path, release_dir, compose_json=None, upstream_json=None) -> int:
    c = conf(conf_path)
    root = Path(release_dir)
    r = Report()
    legal = root / 'deploy' / 'agreements-legal'
    script = root / 'agreements.sh'
    if not script.exists():                       # the repository: the script lives in deploy/agreements-legal
        script = legal / 'agreements.sh'
    # 1. the production script
    if not script.exists():
        r.fail('agreements.sh (production) is missing')
    else:
        text = script.read_text()
        code = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        if 'agreements-legal' not in text or 'prod.conf' not in text:
            r.fail('agreements.sh at the top is not the production script (it does not use deploy/agreements-legal/prod.conf)')
        for pattern, why in SCRIPT_FORBIDDEN:
            for m in re.finditer(pattern, code, re.I | re.M):
                line = code[:m.start()].count('\n') + 1
                r.fail(f'agreements.sh line ~{line}: {why}: {m.group(0).strip()!r}')
        for tag in (c.get('FORBIDDEN_IMAGES') or '').split():
            if tag in code:
                r.fail(f'agreements.sh refers to {tag}, which on this server is a different image')
        # every "up" names the two application services and never starts dependencies
        for m in re.finditer(r'\bdc\b[^\n]*\bup\b[^\n]*', code):
            seg = m.group(0)
            if '--no-deps' not in seg or '$WEB' not in seg or '$WORKER' not in seg:
                r.fail(f'agreements.sh: an "up" that is not limited to the web and worker services: {seg.strip()!r}')
        for m in re.finditer(r'\bdc\b[^\n]*\b(restart|stop|start|rm|kill)\b[^\n]*', code):
            seg = m.group(0)
            if '$WEB' not in seg:
                r.fail(f'agreements.sh: a whole-stack {m.group(1)}: {seg.strip()!r}')
        for m in re.finditer(r'\bdc\b\s+(exec|run|logs|restart|stop|up)\b[^\n]*?\s(agreements)(\s|$)', code):
            r.fail(f'agreements.sh: refers to the upstream service name "agreements": {m.group(0).strip()!r}')
        if 'ROLLBACK_IMAGE' not in code:
            r.fail('agreements.sh does not use ROLLBACK_IMAGE from prod.conf')
        else:
            r.ok('agreements.sh: no forbidden command, every "up" limited to web and worker, rollback image from prod.conf')
    # 2. prod.conf
    for key in ('PROJECT', 'WEB', 'WORKER', 'DB', 'BACKUP', 'CLAMAV', 'ROLLBACK_IMAGE', 'EDGE_NETWORK', 'EDGE_ALIAS', 'LINK_NETWORK', 'VOLUMES'):
        if not c.get(key):
            r.fail(f'prod.conf: {key} is empty')
    expected = {'PROJECT': 'tfsc-agreements-legal', 'WEB': 'legal-agreements', 'WORKER': 'agreements-worker', 'DB': 'postgres', 'BACKUP': 'backup',
                'CLAMAV': 'clamav', 'EDGE_NETWORK': 'alphabridge_edge', 'EDGE_ALIAS': 'tfsc-agreements-legal', 'LINK_NETWORK': 'tfsc-link',
                'POSTGRES_IMAGE': 'tfsc-postgres:16', 'BACKUP_IMAGE': 'tfsc-postgres:20261110-c803dc9', 'CLAMAV_IMAGE': 'clamav/clamav:1.4.6',
                'DOMAIN': 'legal.24-7consultancy.cloud'}
    for k, v in expected.items():
        if c.get(k) != v:
            r.fail(f'prod.conf: {k} is {c.get(k)!r}; the production value is {v!r} (change only with the owner\'s authorization)')
    if c.get('ROLLBACK_IMAGE') in (c.get('FORBIDDEN_IMAGES') or '').split():
        r.fail(f"prod.conf: ROLLBACK_IMAGE {c.get('ROLLBACK_IMAGE')} is one of the forbidden tags")
    # 3. the production compose (rendered by docker compose config --format json)
    if compose_json:
        cfg = json.loads(Path(compose_json).read_text())
        compose_checks(r, c, cfg)
        if upstream_json:
            upstream_checks(r, c, cfg, json.loads(Path(upstream_json).read_text()))
    else:
        r.warn('production compose not rendered (docker compose not available): compose checks NOT RUN')
    # 4. migrations
    versions = root / 'apps' / 'agreements' / 'migrations' / 'versions'
    for f in sorted(versions.glob('*.py')):
        src = f.read_text()
        up = src.split('def downgrade', 1)[0]
        hits = sorted({m.group(0) for m in DESTRUCTIVE.finditer(up.split('def upgrade', 1)[-1])})
        if hits:
            r.warn(f'migration {f.name}: upgrade() contains {hits}: review it before updating (MIGRATION-REVIEW.md)')
    known = set((c.get('KNOWN_MIGRATIONS') or '').split())
    if known:
        new = sorted(f.stem for f in versions.glob('*.py') if f.stem not in known)
        if new:
            r.warn(f'new migrations since the last reviewed release: {new}')
    r.ok(f'{len(list(versions.glob("*.py")))} migrations scanned')
    # 5. nothing that must never ship
    bad = []
    for p in root.rglob('*'):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        name = p.name
        if (name == '.env' or name.endswith('.env') and not name.endswith('.env.example') or name == 'compose.local.yaml'
                or re.search(r'\.(pem|key|p12|pfx|dump|dump\.enc|sql|sqlite3?|db|bak)$', name) or 'private-assets/signatures/' in rel
                or name in ('setup-key.txt', 'users.json', 'token.txt') or re.search(r'\.(xlsx)$', name)):
            bad.append(rel)
            continue
        if p.suffix in ('.py', '.sh', '.yaml', '.yml', '.conf', '.md', '.txt', '.json', '.js', '.html', '.css', '') and p.stat().st_size < 2_000_000:
            try:
                t = p.read_text(errors='ignore')
            except OSError:
                continue
            if re.search(r'-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----', t):
                bad.append(rel + ' (private key)')
            for m in re.finditer(r'^(POSTGRES_PASSWORD|BACKUP_PASSPHRASE|TFSC_SECRET_KEY|SMTP_PASSWORD)=([^\s#]+)', t, re.M):
                if not re.fullmatch(r"['\"]?(CHANGE_ME|placeholder|validation-only[-\w]*|\$\(.*\)|\$\{.*\}|%s)['\"]?", m.group(2)):
                    bad.append(f'{rel} ({m.group(1)} with a value)')
    for b in bad:
        r.fail(f'must not ship: {b}')
    if not bad:
        r.ok('no .env, keys, dumps, signatures, setup keys or secret values in the release')
    return r.finish()


def compose_checks(r: Report, c: dict, cfg: dict):
    services = cfg.get('services', {})
    want = {c['WEB'], c['WORKER'], c['DB'], c['BACKUP'], c['CLAMAV']}
    if cfg.get('name') != c['PROJECT']:
        r.fail(f"compose: project name {cfg.get('name')!r}, production {c['PROJECT']!r}")
    if set(services) != want:
        r.fail(f'compose: services are {sorted(services)}, production has exactly {sorted(want)}')
    else:
        r.ok(f'compose: services {sorted(services)}')
    for name, s in services.items():
        image = str(s.get('image', ''))
        if re.search(r'caddy|nginx|traefik|haproxy|envoy', image + ' ' + name, re.I):
            r.fail(f'compose: {name} is a proxy ({image}); Caddy is managed separately on this server')
        for p in s.get('ports') or []:
            if (p.get('host_ip') or '0.0.0.0') not in ('127.0.0.1', '::1') or str(p.get('published')) in ('80', '443'):
                r.fail(f'compose: {name} publishes {p} beyond 127.0.0.1')
        if s.get('build') and name != c['WEB'] and name != c['WORKER']:
            r.fail(f'compose: {name} has a build: section (only the application image is ever built)')
        if s.get('build'):
            r.warn(f'compose: {name} has a build: section; agreements.sh builds the application image itself')
    images = {c['DB']: c['POSTGRES_IMAGE'], c['BACKUP']: c['BACKUP_IMAGE'], c['CLAMAV']: c['CLAMAV_IMAGE']}
    for svc, img in images.items():
        got = services.get(svc, {}).get('image')
        (r.ok if got == img else r.fail)(f'compose: {svc} image {got} (production {img})')
    for svc in (c['WEB'], c['WORKER']):
        got = str(services.get(svc, {}).get('image', ''))
        if got in (c.get('FORBIDDEN_IMAGES') or '').split() or not got.startswith(c['APP_REPO'] + ':'):
            r.fail(f'compose: {svc} image {got!r} is not a {c["APP_REPO"]} image chosen by agreements.sh')
    pg = ' '.join(services.get(c['DB'], {}).get('command') or [])
    for item in (c.get('PG_EXPECT') or '').split():
        (r.ok if item in pg else r.fail)(f'compose: postgres {item}')
    if 'archive_command=pgbackrest' not in pg:
        r.fail('compose: postgres WAL archiving command missing')
    # volumes: external, exact names
    vols = cfg.get('volumes', {})
    names = {(v or {}).get('name') for v in vols.values()}
    for v in (c['VOLUMES']).split():
        (r.ok if v in names else r.fail)(f'compose: volume {v} reused')
    for key, v in vols.items():
        if not (v or {}).get('external'):
            r.fail(f'compose: volume {key} is not external (it could be created anew)')
    # networks
    nets = cfg.get('networks', {})
    by_name = {(v or {}).get('name', k): (k, v or {}) for k, v in nets.items()}
    for n in (c['EDGE_NETWORK'], c['LINK_NETWORK'], c.get('DEFAULT_NETWORK') or f"{c['PROJECT']}_default"):
        if n not in by_name or not by_name[n][1].get('external'):
            r.fail(f'compose: network {n} is not an external network')
        else:
            r.ok(f'compose: network {n} external')
    web_nets = services.get(c['WEB'], {}).get('networks') or {}
    for n in (c['EDGE_NETWORK'], c['LINK_NETWORK']):
        key = by_name.get(n, (None,))[0]
        aliases = ((web_nets.get(key) or {}).get('aliases') or []) if key in web_nets else None
        if aliases is None:
            r.fail(f'compose: {c["WEB"]} is not on {n}')
        elif c['EDGE_ALIAS'] not in aliases:
            r.fail(f'compose: {c["WEB"]} has no alias {c["EDGE_ALIAS"]} on {n}')
        else:
            r.ok(f'compose: {c["WEB"]} on {n} as {c["EDGE_ALIAS"]}')
    # mounts of the application
    for svc in (c['WEB'], c['WORKER']):
        targets = {(v.get('source'), v.get('target')) for v in services.get(svc, {}).get('volumes') or []}
        for need in (('agreements-data', '/data'), ('contracts', '/contracts')):
            (r.ok if need in targets else r.fail)(f'compose: {svc} mounts {need[0]} at {need[1]}')


def upstream_checks(r: Report, c: dict, cfg: dict, up: dict):
    """Variables the upstream release gives its application services that production does not (new settings upstream)."""
    pairs = ((up.get('services', {}).get('agreements', {}), cfg['services'].get(c['WEB'], {}), c['WEB']),
             (up.get('services', {}).get('agreements-worker', {}), cfg['services'].get(c['WORKER'], {}), c['WORKER']))
    for upstream, prod, name in pairs:
        missing = sorted(set(upstream.get('environment') or {}) - set(prod.get('environment') or {}))
        if missing:
            r.fail(f'upstream gives its {name} equivalent environment variable(s) the production compose lacks: {missing}')
        else:
            r.ok(f'{name}: every upstream environment variable is in the production compose')
    up_services = set(up.get('services', {}))
    known = {'postgres', 'backup', 'clamav', 'agreements', 'agreements-worker', 'edge'}
    if up_services - known:
        r.fail(f'upstream added service(s) {sorted(up_services - known)}: decide how production runs them')
    up_vols = set(up.get('volumes', {})) - {'caddy-data', 'caddy-config'}
    prod_vols = set(cfg.get('volumes', {}))
    if up_vols - prod_vols:
        r.fail(f'upstream added volume(s) {sorted(up_vols - prod_vols)}: production needs an external volume for each')
    for svc in ('agreements', 'agreements-worker'):
        mounts = {v.get('target') for v in up.get('services', {}).get(svc, {}).get('volumes') or []}
        prod = {v.get('target') for v in cfg['services'].get(c['WEB'] if svc == 'agreements' else c['WORKER'], {}).get('volumes') or []}
        if mounts - prod:
            r.fail(f'upstream {svc} mounts {sorted(mounts - prod)} that production does not')


if __name__ == '__main__':
    if len(sys.argv) >= 5 and sys.argv[1] == 'compare':
        sys.exit(compare(*sys.argv[2:5]))
    if len(sys.argv) >= 4 and sys.argv[1] == 'release':
        sys.exit(release(*sys.argv[2:6]))
    print(__doc__)
    sys.exit(2)
