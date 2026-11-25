#!/usr/bin/env python3
"""Production safety checks for TFSC Agreements on legal.24-7consultancy.cloud (standard library only, Python 3.8+).

    legalcheck.py capture  PROD_CONF COMPOSE_JSON RUNNING_DIR OUT
        Before an update: writes OUT, a Compose override that gives the new web and worker containers exactly what the
        running ones have (environment, web processes, names, limits, networks, aliases, mounts). Mode 600; values never
        printed. RUNNING_DIR holds docker inspect output saved by agreements.sh.

    legalcheck.py compare  PROD_CONF COMPOSE_JSON RUNNING_DIR
        The final configuration (release + OUT) against the running containers: environment values (compared, never
        printed), mounts, networks, aliases, published ports (none in production), container names, memory limits and web
        processes must be the same. Exit 1 on any difference that is not allowed in prod.conf.

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
import sys
from pathlib import Path

FAIL, WARN, OK = 'FAIL', 'WARN', 'ok'
ANY_ADDRESS = '0.0.0.0'  # noqa: S104  (a value to compare published ports with, nothing listens here)


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


# ------------------------------------------------------------------ before an update: keep what production runs
def _running(run: Path, svc: str):
    f = run / f'{svc}.json'
    if not f.exists():
        return None, None
    return json.loads(f.read_text())[0], json.loads((run / f'{svc}.image.json').read_text())[0]


def _own_env(cont, img) -> dict:
    """The container's environment without what its image sets itself (those come from the new image)."""
    image_env = env_list(img.get('Config', {}).get('Env'))
    return {k: v for k, v in env_list(cont.get('Config', {}).get('Env')).items() if not (k in image_env and image_env[k] == v)}


def _workers(args):
    return args[args.index('--workers') + 1] if '--workers' in (args or []) else None


def capture(conf_path, compose_json, running_dir, out_path) -> int:
    """Writes a Compose override that gives the new web and worker containers exactly what the running ones have:
    every environment variable (values never printed), the number of web processes, container names, memory and process
    limits, restart policy, extra networks, aliases and mounts. Variables production does not set stay unset. The file
    holds production values (secrets included): agreements.sh writes it with mode 600 on the server only."""
    c = conf(conf_path)
    cfg = json.loads(Path(compose_json).read_text())
    run = Path(running_dir)
    project = c['PROJECT']
    release_env = set((c.get('RELEASE_ENV') or '').split())
    out = {'services': {}, 'networks': {}, 'volumes': {}}
    nets_top, vols_top = cfg.get('networks', {}), cfg.get('volumes', {})
    by_net_name = {(v or {}).get('name', k): k for k, v in nets_top.items()}
    by_vol_name = {(v or {}).get('name', k): k for k, v in vols_top.items()}
    for svc in (c['WEB'], c['WORKER']):
        cont, img = _running(run, svc)
        spec = cfg['services'][svc]
        if cont is None:
            print(f'  FAIL  no running container for {svc}: nothing to keep from')
            return 1
        o = {}
        env = _own_env(cont, img)
        new_env = {}
        for k, v in env.items():
            if k not in release_env:
                new_env[k] = v.replace('$', '$$')          # Compose would otherwise read $NAME as a variable
        for k in (spec.get('environment') or {}):
            if k not in env and k not in release_env:
                new_env[k] = None                          # not set in production: stays unset
        o['environment'] = new_env
        args = cont.get('Args') or []
        if svc == c['WEB'] and _workers(args) and _workers(spec.get('command')):
            cmd = list(spec['command'])
            cmd[cmd.index('--workers') + 1] = _workers(args)
            o['command'] = [x.replace('$', '$$') for x in cmd]
        name = cont.get('Name', '').lstrip('/')
        if name != f'{project}-{svc}-1':
            o['container_name'] = name
        hc = cont.get('HostConfig', {})
        o['mem_limit'] = int(hc.get('Memory') or 0) or None
        o['memswap_limit'] = int(hc.get('MemorySwap') or 0) or None
        o['pids_limit'] = hc.get('PidsLimit') if hc.get('PidsLimit') else -1
        if (hc.get('RestartPolicy') or {}).get('Name'):
            o['restart'] = hc['RestartPolicy']['Name']
        o['init'] = bool(hc.get('Init'))
        # networks and aliases: everything the running container has, plus what the release adds (tfsc-link for web)
        auto = {svc, name, cont.get('Id', '')[:12]}
        nets = {}
        for key, x in (spec.get('networks') or {}).items():
            nets[key] = {'aliases': sorted(set((x or {}).get('aliases') or []))}
        for n, x in (cont.get('NetworkSettings', {}).get('Networks') or {}).items():
            key = by_net_name.get(n)
            if key is None:
                key = 'kept-' + re.sub(r'[^A-Za-z0-9_-]', '-', n)
                out['networks'][key] = {'external': True, 'name': n}
                by_net_name[n] = key
            aliases = set(nets.get(key, {}).get('aliases', [])) | (set(x.get('Aliases') or []) - auto)
            nets[key] = {'aliases': sorted(aliases)}
        o['networks'] = {k: ({'aliases': v['aliases']} if v['aliases'] else {}) for k, v in nets.items()}
        # mounts the running container has and the release does not describe
        have = {(v.get('target')) for v in spec.get('volumes') or []}
        extra = []
        for m in cont.get('Mounts', []):
            if m.get('Destination') in have:
                continue
            if m.get('Type') == 'volume':
                key = by_vol_name.get(m['Name']) or 'kept-' + re.sub(r'[^A-Za-z0-9_-]', '-', m['Name'])
                if m['Name'] not in by_vol_name:
                    out['volumes'][key] = {'external': True, 'name': m['Name']}
                    by_vol_name[m['Name']] = key
                extra.append({'type': 'volume', 'source': key, 'target': m['Destination'], 'read_only': not m.get('RW', True)})
            elif m.get('Type') == 'bind':
                extra.append({'type': 'bind', 'source': m['Source'], 'target': m['Destination'], 'read_only': not m.get('RW', True)})
        if extra:
            o['volumes'] = extra
        out['services'][svc] = {k: v for k, v in o.items() if v is not None or k in ('mem_limit',)}
        if o['mem_limit'] is None:
            out['services'][svc].pop('mem_limit', None)
            out['services'][svc]['mem_limit'] = 0       # 0 = no limit, as now
        print(f'  ok    {svc}: keeps {len(env)} environment variables (names only below), '
              f'{"its container name, " if "container_name" in o else ""}{len(nets)} networks'
              f'{", " + str(len(extra)) + " extra mounts" if extra else ""}')
        print('        ' + ' '.join(sorted(env)))
    for k in ('networks', 'volumes'):
        if not out[k]:
            del out[k]
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write('# Written by agreements.sh from the running containers. Production values (secrets): mode 600, server only.\n')
        f.write(json.dumps(out, indent=1, sort_keys=True))
    return 0


def compare(conf_path, compose_json, running_dir) -> int:
    """The final configuration (release + captured override) against the running containers: must be the same."""
    c = conf(conf_path)
    cfg = json.loads(Path(compose_json).read_text())
    run = Path(running_dir)
    r = Report()
    allow = set((c.get('ALLOW_ENV_CHANGES') or '').split()) | set((c.get('RELEASE_ENV') or '').split())
    project = c['PROJECT']
    if cfg.get('name') != project:
        r.fail(f"compose project is {cfg.get('name')!r}, production is {project!r}")
    nets_top = cfg.get('networks', {})
    vols_top = cfg.get('volumes', {})
    pools = {}
    for svc in (c['WEB'], c['WORKER']):
        spec = cfg.get('services', {}).get(svc)
        cont, img = _running(run, svc)
        if spec is None:
            r.fail(f'service {svc} is missing from the new compose file')
            continue
        if cont is None:
            r.fail(f'no running container found for {svc}')
            continue
        running = _own_env(cont, img)
        new = {k: ('' if v is None else str(v)) for k, v in (spec.get('environment') or {}).items()}
        lost = sorted(k for k in running if k not in new)
        changed = sorted(k for k in running if k in new and new[k] != running[k])
        added = sorted(k for k in new if k not in running)
        for k in lost:
            (r.warn if k in allow else r.fail)(f'{svc}: environment variable {k} would be lost (value not shown)')
        for k in changed:
            (r.warn if k in allow else r.fail)(f'{svc}: environment variable {k} would get a different value (values not shown)')
        for k in added:
            (r.warn if k in allow else r.fail)(f'{svc}: environment variable {k} would be added')
        if not (lost or changed or added):
            r.ok(f'{svc}: environment identical to the running container ({len(running)} variables, values compared, not shown)')
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
            r.ok(f'{svc}: same volumes at the same paths ({", ".join(sorted(str(n) for n, _ in have))})')
        cname = cont.get('Name', '').lstrip('/')
        auto = {svc, cname, cont.get('Id', '')[:12]}
        have_n = {n: set(x.get('Aliases') or []) - auto for n, x in (cont.get('NetworkSettings', {}).get('Networks') or {}).items()}
        want_n = {(nets_top.get(k) or {}).get('name', k): set((x or {}).get('aliases') or []) - auto for k, x in (spec.get('networks') or {}).items()}
        for n in sorted(set(have_n) - set(want_n)):
            r.fail(f'{svc}: would leave network {n}')
        for n in sorted(set(want_n) - set(have_n)):
            r.warn(f'{svc}: joins network {n} (aliases {sorted(want_n[n])})')
        for n in sorted(set(have_n) & set(want_n)):
            if have_n[n] - want_n[n]:
                r.fail(f'{svc}: alias(es) {sorted(have_n[n] - want_n[n])} on {n} would be lost')
        r.ok(f'{svc}: networks and aliases kept ({", ".join(f"{n}{sorted(a) if a else ""}" for n, a in sorted(have_n.items()))})')
        have_p = sorted((b.get('HostIp') or ANY_ADDRESS, str(b.get('HostPort')), k.split('/')[0])
                        for k, binds in (cont.get('HostConfig', {}).get('PortBindings') or {}).items() for b in (binds or []))
        want_p = sorted((p.get('host_ip') or ANY_ADDRESS, str(p.get('published')), str(p.get('target'))) for p in spec.get('ports') or [])
        if have_p != want_p:
            r.fail(f'{svc}: published ports differ: running {have_p or "none"}, new {want_p or "none"}')
        else:
            r.ok(f'{svc}: published host ports: {have_p or "none"} (unchanged)')
        want_name = spec.get('container_name') or f'{project}-{svc}-1'
        (r.ok if cname == want_name else r.fail)(f'{svc}: container name {cname} -> {want_name}')
        hc = cont.get('HostConfig', {})
        mem, new_mem = int(hc.get('Memory') or 0), int(spec.get('mem_limit') or 0)
        (r.ok if mem == new_mem else r.fail)(f'{svc}: memory limit {mem or "none"} -> {new_mem or "none"}')
        pol = (hc.get('RestartPolicy') or {}).get('Name')
        (r.ok if pol == spec.get('restart') else r.warn)(f'{svc}: restart policy {pol} -> {spec.get("restart")}')
        if svc == c['WEB']:
            a, b = _workers(cont.get('Args')), _workers(spec.get('command'))
            (r.ok if a == b else r.fail)(f'{svc}: web processes {a} -> {b}')
            procs = int(b or 1)
        else:
            e = new
            procs = int(e.get('TFSC_GENERATION_WORKERS') or 2) + int(e.get('TFSC_COMPLETION_WORKERS') or 2) + 3   # + mail, sweep, supervisor
        size = max(2, int(new.get('DB_POOL_SIZE') or 20))
        over = int(new.get('DB_POOL_OVERFLOW') or 5)
        pools[svc] = (procs, size, over)
        r.ok(f'{svc}: DB_POOL_SIZE {running.get("DB_POOL_SIZE", "unset (20)")} -> {new.get("DB_POOL_SIZE", "unset (20)")}, '
             f'DB_POOL_OVERFLOW {running.get("DB_POOL_OVERFLOW", "unset (5)")} -> {new.get("DB_POOL_OVERFLOW", "unset (5)")}')
    for item in (c.get('APP_EXPECT') or '').split():
        where, _, kv = item.partition(':')
        k, _, v = kv.partition('=')
        svc = c['WEB'] if where == 'web' else c['WORKER']
        got = (cfg['services'].get(svc, {}).get('environment') or {}).get(k)
        (r.ok if str(got) == v else r.fail)(f'{svc}: {k} = {got} (production: {v})')
    if pools:
        most = sum(p * (s + o) for p, s, o in pools.values())
        r.ok('database connections at most: ' + ' + '.join(f'{svc} {p}x({s}+{o})' for svc, (p, s, o) in pools.items())
             + f' = {most} (same as now; PostgreSQL max_connections {c.get("PG_MAX_CONNECTIONS", "?")})')
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
        for m in re.finditer(r'\b(?:dc|docker\s+compose)\b[^\n]*\sup\s[^\n]*', code):
            seg = m.group(0)
            if '--no-deps' not in seg or '$WEB' not in seg or '$WORKER' not in seg:
                r.fail(f'agreements.sh: an "up" that is not limited to the web and worker services: {seg.strip()!r}')
        for m in re.finditer(r'\b(?:dc|docker\s+compose)\s+(?:-\S+\s+\S+\s+)*(restart|stop|start|rm|kill|pause|create)\b[^\n]*', code):
            seg = m.group(0)
            if '$WEB' not in seg:
                r.fail(f'agreements.sh: a {m.group(1)} not limited to the production web and worker services: {seg.strip()!r}')
        for m in re.finditer(r'\bdc\b\s+(exec|run|logs|restart|stop|up)\b[^\n]*?\s(agreements)(\s|$)', code):
            r.fail(f'agreements.sh: refers to the upstream service name "agreements": {m.group(0).strip()!r}')
        for m in re.finditer(r'(-d|--dbname)\s+[\'"]?crm\b', code):
            r.fail(f'agreements.sh: database "crm" written in instead of $DB_NAME: {code[max(0, m.start() - 40):m.end()].strip()!r}')
        if re.search(r'HEALTH_PORT|health_port|127\.0\.0\.1:\$', code):
            r.fail('agreements.sh: depends on a published host port (production has none)')
        if 'capture_runtime' not in code:
            r.fail('agreements.sh does not keep the running containers\' settings (capture_runtime)')
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
        if (name == '.env' or (name.endswith('.env') and not name.endswith('.env.example')) or name == 'compose.local.yaml'
                or re.search(r'\.(pem|key|p12|pfx|dump|dump\.enc|sql|sqlite3?|db|bak)$', name) or 'private-assets/signatures/' in rel
                or name in ('setup-key.txt', 'users.json', 'token.txt', 'compose.runtime.yaml') or '/state/' in '/' + rel or re.search(r'\.(xlsx)$', name)):
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
            if (p.get('host_ip') or ANY_ADDRESS) not in ('127.0.0.1', '::1') or str(p.get('published')) in ('80', '443'):
                r.fail(f'compose: {name} publishes {p} beyond 127.0.0.1')
            elif name == c['WEB'] and c.get('WEB_PORTS') == 'none':
                r.fail(f'compose: {name} publishes {p}; production publishes no port for it (Caddy uses alphabridge_edge)')
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
    if c.get('WEB_PORTS') == 'none' and not services.get(c['WEB'], {}).get('ports'):
        r.ok(f'compose: {c["WEB"]} publishes no host port (as production)')
    hc = ' '.join(services.get(c['DB'], {}).get('healthcheck', {}).get('test') or [])
    if '-d crm' in hc or (c.get('DB_NAME') and f"-d {c['DB_NAME']}" not in hc):
        r.fail(f'compose: postgres health check does not use database {c.get("DB_NAME")}: {hc!r}')
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
    if len(sys.argv) >= 6 and sys.argv[1] == 'capture':
        sys.exit(capture(*sys.argv[2:6]))
    if len(sys.argv) >= 5 and sys.argv[1] == 'compare':
        sys.exit(compare(*sys.argv[2:5]))
    if len(sys.argv) >= 4 and sys.argv[1] == 'release':
        sys.exit(release(*sys.argv[2:6]))
    print(__doc__)
    sys.exit(2)
