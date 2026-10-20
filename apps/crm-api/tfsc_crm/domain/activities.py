"""Activity capture and lifecycle (§11.3): data hunting -> committed -> locked -> soft-deleted.

Rules enforced here (server is the authority):
- all four outcomes Unset = data hunting: no credit, no timer;
- any explicit Yes/No starts the lock timer at server time (manual 60 min, paste/import 10-20 min);
  Called = No starts the timer but earns no attempt; un-setting never resets the deadline;
- Connected => Called; Called = No forbids Connected = Yes;
- after the deadline only Supervisor+ in scope may edit, with a reason; edits never move the deadline;
- phone/name of a row supporting an owned lead change only by Supervisor+ with reason;
- business_date is the Pacific date at commit (live entry); the typed date is kept in `raw`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, and_, select, text
from tfsc_refine import refine_row

from .. import crypto
from ..db import models as m
from ..ids import uuid7
from ..pacific import business_date, utcnow
from ..security.roles import SUPERVISOR, Actor, in_scope
from . import duplicates, scoring
from . import settings as cfg

OUTCOMES = ('called', 'connected', 'text_sent', 'email_sent')
EDITABLE = ('name', 'phone', 'email', 'called', 'connected', 'text_sent', 'email_sent', 'followup_needed', 'next_followup', 'remarks')


class RowRejected(Exception):
    def __init__(self, issues: list[dict], kind: str = 'invalid'):
        super().__init__(kind)
        self.issues = issues
        self.kind = kind


@dataclass
class SaveResult:
    activity: dict
    duplicate: duplicates.DupResult
    issues: list[dict] = field(default_factory=list)


def committed(v: dict) -> bool:
    return any(v.get(k) is not None for k in OUTCOMES)


def validate_outcomes(v: dict) -> list[dict]:
    issues = []
    if v.get('connected') is True and v.get('called') is not True:
        issues.append({'field': 'connected', 'severity': 'red', 'code': 'contradiction', 'message': 'Connected requires Called = Yes.'})
    return issues


def _window(c: Connection, org_id: uuid.UUID, source: str) -> int:
    lw = cfg.get(c, org_id, 'lock_windows')
    if source in ('grid', 'import'):
        return max(int(lw.get('import_min', 10)), min(int(lw.get('import_max', 20)), int(lw.get('import_minutes', 15))))
    return int(lw.get('manual_minutes', 60))


def _owner_stamp(c: Connection, owner_id: uuid.UUID) -> tuple[str | None, str | None]:
    r = c.execute(select(m.users.c.default_domain_id, m.users.c.default_project_id).where(m.users.c.id == owner_id)).one()
    return r[0], r[1]


def _followup_due(next_followup: str | None) -> datetime | None:
    if not next_followup:
        return None
    from ..pacific import pacific_local
    d = date.fromisoformat(next_followup[:10])
    from datetime import time as dtime
    return pacific_local(d, dtime(10, 0))[0]  # default 10:00 AM PT on the chosen date


def refine_input(row: dict[str, Any], c: Connection, org_id: uuid.UUID) -> dict:
    comma = bool(cfg.get(c, org_id, 'names').get('suffix_comma', True))
    return refine_row(row, business_date().isoformat(), comma)


def create(c: Connection, actor: Actor, row: dict[str, Any], *, source: str = 'manual', owner_id: uuid.UUID | None = None,
           batch_id: uuid.UUID | None = None, historical: bool = False, at: datetime | None = None) -> SaveResult:
    owner_id = owner_id or actor.id
    if owner_id != actor.id and not in_scope(c, actor, owner_id):
        raise HTTPException(403, 'You can only record activity for yourself or people in your scope.')
    r = refine_input(row, c, actor.org_id)
    ref = r['refined']
    red = [i for i in r['issues'] if i['severity'] == 'red']
    if red:
        raise RowRejected(red, 'contradiction' if any(i['code'] == 'contradiction' for i in red) else 'invalid')
    if ref['called'] is False and ref['connected'] is True:
        raise RowRejected([{'field': 'connected', 'severity': 'red', 'code': 'contradiction', 'message': 'Called = No cannot be Connected.'}], 'contradiction')
    now = at or utcnow()
    bd = date.fromisoformat(ref['business_date']) if historical else business_date(now)
    is_committed = committed(ref)
    window = _window(c, actor.org_id, source)
    domain_id, project_id = _owner_stamp(c, owner_id)
    pkey = crypto.phone_key(ref['phone_e164'])
    ekey = crypto.email_key(ref['email'] or None)
    aid = uuid7()
    next_due = _followup_due(ref['next_followup'])
    followup_needed = ref['followup_needed'] if ref['followup_needed'] is not None else (True if ref['next_followup'] or ref['call_again'] else None)
    values = {
        'id': aid, 'org_id': actor.org_id, 'owner_id': owner_id, 'business_date': bd,
        'entry_source': 'import_legacy' if historical else source, 'import_batch_id': batch_id,
        'raw': {k: (v if isinstance(v, (str, int, float, bool)) or v is None else str(v)) for k, v in row.items()},
        'provider_name': ref['name'], 'prospect_kind': ref['kind'],
        'phone_display': crypto.encrypt(ref['phone'] or None), 'phone_key': pkey, 'extension': ref['extension'],
        'email': crypto.encrypt(ref['email'] or None), 'email_key': ekey,
        'called': ref['called'], 'connected': ref['connected'], 'text_sent': ref['text_sent'], 'email_sent': ref['email_sent'],
        'followup_needed': followup_needed, 'next_followup_at': next_due, 'remarks': ref['remarks'],
        'first_committed_at': now if is_committed else None,
        'edit_deadline': (now + timedelta(minutes=window)) if is_committed and not historical else None,
        'lock_window_minutes': window if is_committed else None,
        'domain_id': domain_id, 'project_id': project_id, 'created_at': now, 'updated_at': now, 'version': 1,
    }
    dup = duplicates.DupResult(None, duplicates.AGENT_NOTICE[None])
    reg = None
    if pkey:
        reg = duplicates.lock_registry(c, actor.org_id, pkey)
        dup = duplicates.evaluate(c, actor.org_id, reg, values, historical=historical)
        if dup.severity in ('amber', 'red'):
            values['conflict'] = dup.severity
    c.execute(text('SELECT crm_ensure_partition_for(:p, :d)'), {'p': 'activities', 'd': bd})
    c.execute(m.activities.insert().values(**values))
    if reg is not None:
        duplicates.record_contact(c, actor.org_id, reg, values, now)
    if followup_needed:
        c.execute(m.followups.insert().values(id=uuid7(), org_id=actor.org_id, activity_id=aid, activity_date=bd, phone_key=pkey,
                                              owner_id=owner_id, due_at=next_due, status='open', created_at=now, updated_at=now))
        if pkey and dup.severity not in ('amber', 'red') and reg is not None and reg['state'] in ('none', 'followup'):
            duplicates.set_state(c, actor.org_id, pkey, 'followup', owner_id)
    pts = scoring.ladder(scoring.ScoreInput(committed=is_committed, called=ref['called'], connected=ref['connected'],
                                            text_sent=ref['text_sent'], email_sent=ref['email_sent'], conflict=values.get('conflict')))
    values['score_points'] = 0
    scoring.apply(c, actor.org_id, values, pts, now)
    values['score_points'] = pts
    from . import rollups
    rollups.on_activity(c, actor.org_id, None, values, now)
    return SaveResult(values, dup, [i for i in r['issues'] if i['severity'] != 'red'])


def get_for_update(c: Connection, actor: Actor, activity_id: uuid.UUID) -> dict:
    a = c.execute(select(m.activities).where(m.activities.c.id == activity_id, m.activities.c.org_id == actor.org_id).with_for_update()).mappings().first()
    if not a or not (a['owner_id'] == actor.id or (actor.rank >= SUPERVISOR and in_scope(c, actor, a['owner_id']))):
        raise HTTPException(404, 'Record not found.')
    return dict(a)


def supports_owned_lead(c: Connection, a: dict) -> bool:
    return c.execute(select(m.leads.c.id).where(m.leads.c.origin_activity_id == a['id'], m.leads.c.stage != 'lost')).first() is not None


def snapshot(a: dict) -> dict:
    keys = ('provider_name', 'phone_key', 'extension', 'email_key', 'called', 'connected', 'text_sent', 'email_sent', 'followup_needed',
            'next_followup_at', 'remarks', 'deleted_at', 'delete_reason')
    return {k: (a[k].isoformat() if isinstance(a.get(k), (datetime, date)) else a.get(k)) for k in keys}


def _write_revision(c: Connection, actor: Actor, a: dict, after: dict, reason: str | None) -> None:
    rev = c.execute(text('SELECT coalesce(max(rev), 0) + 1 FROM activity_revisions WHERE activity_id = :a'), {'a': a['id']}).scalar_one()
    c.execute(m.activity_revisions.insert().values(id=uuid7(), org_id=actor.org_id, activity_id=a['id'], business_date=a['business_date'],
                                                   rev=rev, changed_by=actor.id, reason=reason, before=snapshot(a), after=snapshot(after)))


def edit(c: Connection, actor: Actor, activity_id: uuid.UUID, patch: dict[str, Any], version: int, reason: str | None) -> SaveResult:
    a = get_for_update(c, actor, activity_id)
    if a['version'] != version:
        raise HTTPException(409, 'This record has changed. Refresh before editing.')
    if a['deleted_at']:
        raise HTTPException(409, 'This record was deleted. Restore it first.')
    now = utcnow()
    locked = a['edit_deadline'] is not None and now >= a['edit_deadline']
    is_owner = a['owner_id'] == actor.id
    reason = (reason or '').strip() or None
    if locked or not is_owner:
        if actor.rank < SUPERVISOR or not in_scope(c, actor, a['owner_id']):
            raise HTTPException(403, 'This entry is locked. Only a supervisor in scope can change it.')
        if not reason:
            raise HTTPException(400, 'A reason is required to change a locked or another person\'s entry.')
    unknown = set(patch) - set(EDITABLE)
    if unknown:
        raise HTTPException(400, 'Unsupported fields: ' + ', '.join(sorted(unknown)))
    identity_change = any(k in patch for k in ('name', 'phone'))
    if identity_change and supports_owned_lead(c, a) and (actor.rank < SUPERVISOR or not reason):
        raise HTTPException(403, 'Phone and name of an owned lead can only be changed by a supervisor with a reason.')
    current = {
        'name': a['provider_name'], 'phone': crypto.decrypt(a['phone_display']) or '', 'email': crypto.decrypt(a['email']) or '',
        'called': a['called'], 'connected': a['connected'], 'text_sent': a['text_sent'], 'email_sent': a['email_sent'],
        'followup_needed': a['followup_needed'], 'next_followup': a['next_followup_at'].astimezone(_pt()).date().isoformat() if a['next_followup_at'] else None,
        'remarks': a['remarks'], 'business_date': a['business_date'].isoformat(),
    }
    if a['extension'] and current['phone'] and 'ext' not in current['phone']:
        current['phone'] += f' ext. {a["extension"]}'
    merged = {**current, **patch}
    if patch.get('connected') is True and merged.get('called') is not True:
        raise HTTPException(400, 'Connected requires Called = Yes.')
    r = refine_input(merged, c, actor.org_id)
    ref = r['refined']
    red = [i for i in r['issues'] if i['severity'] == 'red' and i['field'] not in ('business_date',)]
    if red:
        raise RowRejected(red)
    if ref['called'] is False and ref['connected'] is True:
        raise HTTPException(400, 'Called = No cannot be Connected.')
    vals: dict[str, Any] = {
        'provider_name': ref['name'], 'prospect_kind': ref['kind'], 'phone_display': crypto.encrypt(ref['phone'] or None),
        'phone_key': crypto.phone_key(ref['phone_e164']), 'extension': ref['extension'], 'email': crypto.encrypt(ref['email'] or None),
        'email_key': crypto.email_key(ref['email'] or None), 'called': ref['called'], 'connected': ref['connected'],
        'text_sent': ref['text_sent'], 'email_sent': ref['email_sent'], 'followup_needed': ref['followup_needed'],
        'next_followup_at': _followup_due(ref['next_followup']) if 'next_followup' in patch else a['next_followup_at'],
        'remarks': ref['remarks'], 'updated_at': now, 'version': a['version'] + 1,
    }
    if a['first_committed_at'] is None and committed(ref):
        window = a['lock_window_minutes'] or _window(c, actor.org_id, a['entry_source'])
        vals.update(first_committed_at=now, edit_deadline=now + timedelta(minutes=window), lock_window_minutes=window)
    after = {**a, **vals}
    dup = duplicates.DupResult(None, duplicates.AGENT_NOTICE[None])
    if vals['phone_key'] and vals['phone_key'] != a['phone_key']:
        reg = duplicates.lock_registry(c, actor.org_id, vals['phone_key'])
        dup = duplicates.evaluate(c, actor.org_id, reg, after)
        if dup.severity in ('amber', 'red'):
            vals['conflict'] = after['conflict'] = dup.severity
        duplicates.record_contact(c, actor.org_id, reg, after, now)
    elif vals['phone_key'] and (vals['called'] or vals['connected']):
        reg = duplicates.lock_registry(c, actor.org_id, vals['phone_key'])
        duplicates.record_contact(c, actor.org_id, reg, after, now)
    res = c.execute(m.activities.update().where(and_(m.activities.c.id == a['id'], m.activities.c.business_date == a['business_date'],
                                                     m.activities.c.version == version)).values(**vals))
    if res.rowcount != 1:
        raise HTTPException(409, 'This record has changed. Refresh before editing.')
    _write_revision(c, actor, a, after, reason)
    if vals['followup_needed'] and not a['followup_needed']:
        c.execute(m.followups.insert().values(id=uuid7(), org_id=actor.org_id, activity_id=a['id'], activity_date=a['business_date'],
                                              phone_key=vals['phone_key'], owner_id=a['owner_id'], due_at=vals['next_followup_at'], status='open'))
    if locked:
        from . import audit
        audit.record(c, actor.org_id, actor.id, 'activity.post_lock_edit', 'activity', a['id'], snapshot(a), snapshot(after))
    scoring.rescore(c, actor.org_id, a['id'], a['business_date'], now)
    fresh = dict(c.execute(select(m.activities).where(m.activities.c.id == a['id'], m.activities.c.business_date == a['business_date'])).mappings().one())
    from . import rollups
    rollups.on_activity(c, actor.org_id, a, fresh, now, post_lock=locked)
    return SaveResult(fresh, dup, [i for i in r['issues'] if i['severity'] != 'red'])


def soft_delete(c: Connection, actor: Actor, activity_id: uuid.UUID, reason: str, restore: bool = False) -> dict:
    a = get_for_update(c, actor, activity_id)
    if actor.rank < SUPERVISOR or not in_scope(c, actor, a['owner_id']):
        raise HTTPException(403, 'Only a supervisor in scope can delete or restore entries.')
    if not (reason or '').strip():
        raise HTTPException(400, 'A reason is required.')
    now = utcnow()
    vals = {'deleted_at': None, 'deleted_by': None, 'delete_reason': None} if restore else {'deleted_at': now, 'deleted_by': actor.id, 'delete_reason': reason.strip()}
    vals.update(updated_at=now, version=a['version'] + 1)
    c.execute(m.activities.update().where(m.activities.c.id == a['id'], m.activities.c.business_date == a['business_date']).values(**vals))
    after = {**a, **vals}
    _write_revision(c, actor, a, after, reason)
    from . import audit
    audit.record(c, actor.org_id, actor.id, 'activity.restored' if restore else 'activity.deleted', 'activity', a['id'], None, {'reason': reason})
    scoring.rescore(c, actor.org_id, a['id'], a['business_date'], now)
    fresh = dict(c.execute(select(m.activities).where(m.activities.c.id == a['id'], m.activities.c.business_date == a['business_date'])).mappings().one())
    from . import rollups
    rollups.on_activity(c, actor.org_id, a, fresh, now)
    return fresh


def _pt():
    from ..pacific import PACIFIC
    return PACIFIC
