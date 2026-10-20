"""Synthetic load-test data (§24). SYNTHETIC ONLY: names like "Practice 000123", 555-01xx-style numbers.

Creates a reporting hierarchy (1 director, managers, supervisors, AGENTS sales agents), then ACTIVITIES committed
activities spread over the last DAYS workdays, encrypted exactly like the app does (field encryption, blind-index phone
keys), plus the derived tables dashboards read (agent_day_stats) and the phone registry the pre-dial check reads.
Every user's password is LOAD_PASSWORD (local login must be allowed on the target for the load test).

Usage (fresh, migrated database):
  CRM_DATABASE_URL=postgresql+psycopg://... CRM_FIELD_KEY=... CRM_PHONE_KEY_SECRET=... \
    python infra/load/gen_data.py --agents 550 --activities 2500000 --days 60
Writes infra/load/users.json (emails for k6). Re-running adds data; use a fresh database for clean numbers.
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'apps' / 'crm-api'))

LOAD_PASSWORD = 'Load-test-password-2026!'  # noqa: S105 - synthetic load-test accounts only


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--agents', type=int, default=550)
    ap.add_argument('--activities', type=int, default=2_500_000)
    ap.add_argument('--days', type=int, default=60)
    ap.add_argument('--seed', type=int, default=24)
    a = ap.parse_args()
    random.seed(a.seed)

    from sqlalchemy import select, text

    from tfsc_crm import crypto
    from tfsc_crm.db import models as m
    from tfsc_crm.db.session import engine, system_tx
    from tfsc_crm.pacific import business_date
    from tfsc_crm.security.auth import hash_password

    pw = hash_password(LOAD_PASSWORD)
    t0 = time.perf_counter()
    with system_tx() as c:
        org = c.execute(select(m.orgs.c.id).limit(1)).scalar_one()
    users: list[dict] = []

    def user(role: str, name: str, manager: uuid.UUID | None) -> uuid.UUID:
        uid = uuid.uuid4()
        users.append({'id': uid, 'org_id': org, 'email': f'{name}@load.test', 'display_name': name.replace('-', ' ').title(), 'crm_role': role,
                      'manager_id': manager, 'status': 'active', 'password_hash': pw, 'hired_on': date(2026, 1, 5),
                      'mfa_required': role != 'sales_agent'})
        return uid

    director = user('director_admin', 'load-director', None)
    n_sup = max(1, a.agents // 11)
    n_mgr = max(1, n_sup // 10)
    managers = [user('manager', f'load-manager-{i:02d}', director) for i in range(n_mgr)]
    sups = [user('supervisor', f'load-sup-{i:03d}', managers[i % n_mgr]) for i in range(n_sup)]
    agents = [user('sales_agent', f'load-agent-{i:04d}', sups[i % n_sup]) for i in range(a.agents)]
    with system_tx() as c:
        c.execute(m.users.insert(), users)
    print(f'users: {len(users)} ({a.agents} agents, {n_sup} supervisors, {n_mgr} managers)', flush=True)

    today = business_date()
    days: list[date] = []
    d = today - timedelta(days=1)
    while len(days) < a.days:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    with system_tx() as c:
        for d in sorted({x.replace(day=1) for x in days}):
            c.execute(text('SELECT crm_ensure_partition_for(:t, :d)'), {'t': 'activities', 'd': d})
    per_day = a.activities / (a.agents * a.days)
    phones = max(1000, a.activities // 3)  # a third of rows re-contact an existing number
    cols = ['id', 'org_id', 'owner_id', 'business_date', 'entry_source', 'raw', 'provider_name', 'phone_display', 'phone_key', 'called',
            'connected', 'text_sent', 'email_sent', 'remarks', 'first_committed_at', 'edit_deadline', 'lock_window_minutes', 'score_points',
            'created_at', 'updated_at', 'version']
    phone_cache: dict[int, tuple[str, str]] = {}
    written = 0
    batch = io.StringIO()

    def flush() -> None:
        nonlocal batch
        raw = engine().raw_connection()
        try:
            with raw.cursor() as cur, cur.copy(f"COPY activities ({','.join(cols)}) FROM STDIN") as cp:
                cp.write(batch.getvalue())
            raw.commit()
        finally:
            raw.close()
        batch = io.StringIO()

    for d in days:
        for ag in agents:
            n = max(0, int(random.gauss(per_day, per_day * 0.15)))
            for _ in range(n):
                p = random.randrange(phones)
                if p not in phone_cache:
                    num = f'+1415{p // 10000 % 1000:03d}{p % 10000:04d}'
                    phone_cache[p] = (crypto.encrypt(f'({num[2:5]}) {num[5:8]}-{num[8:]}'), crypto.phone_key(num))
                enc, key = phone_cache[p]
                connected = random.random() < 0.17
                touch = connected and random.random() < 0.15
                pts = 30 if touch else 20 if connected else 10
                at = datetime(d.year, d.month, d.day, 17, random.randrange(60), tzinfo=UTC) + timedelta(minutes=random.randrange(480))
                row = [uuid.uuid4(), org, ag, d, 'manual', '{}', f'Practice {p:06d}', enc, key, 't', 't' if connected else 'f',
                       't' if touch else 'f', 'f', '', at.isoformat(), (at + timedelta(minutes=60)).isoformat(), 60, pts,
                       at.isoformat(), at.isoformat(), 1]
                batch.write('\t'.join(str(v) for v in row) + '\n')
                written += 1
                if written % 100_000 == 0:
                    flush()
                    print(f'activities: {written:,} ({time.perf_counter() - t0:.0f} s)', flush=True)
    flush()
    print(f'activities: {written:,}', flush=True)
    with system_tx() as c:
        c.execute(text("""
INSERT INTO agent_day_stats (org_id, user_id, business_date, attempts, connected, sms, emails, any_touch, points, points_event, last_activity_at)
SELECT org_id, owner_id, business_date, count(*), count(*) FILTER (WHERE connected), count(*) FILTER (WHERE text_sent),
       count(*) FILTER (WHERE email_sent), count(*) FILTER (WHERE text_sent OR email_sent), sum(score_points), sum(score_points), max(created_at)
FROM activities WHERE org_id = :o GROUP BY org_id, owner_id, business_date
ON CONFLICT (org_id, user_id, business_date) DO NOTHING"""), {'o': org})
        c.execute(text("""
INSERT INTO phone_registry (org_id, phone_key, state, first_seen_activity_id, first_seen_by, first_seen_at, last_activity_at, updated_at)
SELECT DISTINCT ON (phone_key) org_id, phone_key, 'none', id, owner_id, created_at, created_at, created_at
FROM activities WHERE org_id = :o AND phone_key IS NOT NULL ORDER BY phone_key, created_at
ON CONFLICT DO NOTHING"""), {'o': org})
        c.execute(text('ANALYZE'))
    out = Path(__file__).resolve().parent / 'users.json'
    out.write_text(json.dumps({'password': LOAD_PASSWORD, 'agents': [u['email'] for u in users if u['crm_role'] == 'sales_agent'],
                               'supervisors': [u['email'] for u in users if u['crm_role'] == 'supervisor'],
                               'managers': [u['email'] for u in users if u['crm_role'] == 'manager'], 'director': 'load-director@load.test'}))
    print(f'done in {time.perf_counter() - t0:.0f} s; logins in {out}', flush=True)


if __name__ == '__main__':
    main()
