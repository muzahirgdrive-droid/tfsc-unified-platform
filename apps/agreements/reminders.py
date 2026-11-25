"""Reminders you control (release 5.4).

Who is reminded, when, how often and how many times is decided here, and nowhere else:

* Rules (Admin › Reminders): named schedules. First reminder after N working days, then every N working days, at most N
  reminders, only on the chosen days between the chosen hours in the sending company's time zone. Options: one email per
  person per day for several envelopes, no reminder when the person opened the envelope in the last hours, remind people
  who only view a copy, let signers stop reminders from the email, tell the sender after the last reminder.
* Each company has a default rule (Standard until changed); the sender may choose another rule for an envelope.
* Reminders can be switched off or paused for one person on one envelope, paused for a whole envelope, paused for
  everyone, or switched off for a person everywhere (the do-not-remind list).

Safety guards that no rule can switch off:
* an email is sent at most once (mailer.py: one handed to the mail server is never resent by itself);
* at most 1 automatic email per person per company per day, and 2 per person per day in all;
* no new reminder while an earlier one to the same person is still waiting to go out or unconfirmed;
* never to an address that bounced or failed, never after the person signed, declined or was replaced, never after the
  envelope is completed, voided or expired.

The worker's sweep lane calls run() every minute. Reminders are planned from the tables each time (nothing is queued in
advance), so a change to a rule, a pause or a switch applies at once.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import html
import re
import time
import zoneinfo

from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import JSON, Boolean, Column, Float, Index, Integer, String, Table, Text, and_, or_, select, update

import database as db
import security as sec

AUTO_KINDS = ('reminder', 'reminder_digest', 'expiring')
OPEN = ('sent', 'delivered')
PER_COMPANY_PER_DAY = 1
PER_PERSON_PER_DAY = 2
COMBINE_AHEAD = 86400            # a reminder due within a day for the same person and company rides along in the same email
RUN_LIMIT = 300                  # emails per pass at most; the rest follow a minute later
FOREVER = 4102444800.0           # 2100-01-01: "until I resume"
DAY = 86400

rules = Table('reminder_rules', db.meta,
              Column('id', String(40), primary_key=True),
              Column('name', String(80), nullable=False),
              Column('description', Text, nullable=False),
              Column('first_after', Integer, nullable=False),
              Column('every', Integer, nullable=False),
              Column('max_count', Integer, nullable=False),
              Column('weekdays', JSON, nullable=False),              # 0 = Monday ... 6 = Sunday
              Column('hour_from', Integer, nullable=False),
              Column('hour_to', Integer, nullable=False),
              Column('combine', Boolean, nullable=False),
              Column('skip_opened_hours', Integer, nullable=False),
              Column('remind_viewers', Boolean, nullable=False),
              Column('allow_signer_stop', Boolean, nullable=False),
              Column('notify_sender_after_last', Boolean, nullable=False),
              Column('active', Boolean, nullable=False),
              Column('builtin', Boolean, nullable=False),
              Column('created', Float, nullable=False),
              Column('updated', Float, nullable=False),
              Column('updated_by', String(32), nullable=True))
optouts = Table('reminder_optouts', db.meta,
                Column('id', String(32), primary_key=True),
                Column('email', String(200), nullable=False),
                Column('name', String(150), nullable=False),
                Column('entity', String(100), nullable=True),            # None: every company
                Column('reason', Text, nullable=False),
                Column('until', Float, nullable=True),
                Column('created', Float, nullable=False),
                Column('created_by', String(32), nullable=True),
                Column('created_by_name', String(150), nullable=False),
                Column('removed_at', Float, nullable=True),
                Column('removed_by', String(32), nullable=True))
Index('ix_reminder_optouts_email', optouts.c.email)

DAY_NAMES = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
SETTING_DEFAULTS = 'reminders:defaults'
SETTING_PAUSE = 'reminders:paused_until'
SETTING_INSTALLED = 'reminders:installed_at'


def now() -> float:
    import esign
    return esign.now()          # one clock for the whole e-signature engine (the tests move it)


# ------------------------------------------------------------------ settings and rules
def _setting(c, key, default=None):
    value = c.execute(select(db.settings.c.value).where(db.settings.c.key == key)).scalar()
    return default if value is None else value


def _put(c, key, value) -> None:
    if not c.execute(db.settings.update().where(db.settings.c.key == key).values(value=value)).rowcount:
        c.execute(db.settings.insert().values(key=key, value=value))


def load_rules(c, include_retired: bool = True) -> dict[str, dict]:
    q = select(rules)
    if not include_retired:
        q = q.where(rules.c.active.is_(True))
    return {r['id']: dict(r) for r in c.execute(q.order_by(rules.c.builtin.desc(), rules.c.created, rules.c.id)).mappings()}


def defaults(c) -> dict:
    value = _setting(c, SETTING_DEFAULTS, {})
    return value if isinstance(value, dict) else {}


def default_rule_id(c, entity: str, all_rules: dict, chosen: dict | None = None) -> str:
    rid = (defaults(c) if chosen is None else chosen).get(entity)
    if rid in all_rules and all_rules[rid]['active']:
        return rid
    return 'standard' if 'standard' in all_rules else next(iter(all_rules), 'standard')


def rule_for(c, envelope: dict, all_rules: dict, chosen: dict | None = None) -> dict:
    """The rule an envelope follows: the one chosen when sending, else the company's default. An envelope sent before 5.4
    with reminders set to Off stays off."""
    s = envelope.get('settings') or {}
    rid = s.get('reminder_rule') or ''
    if not rid and 'reminder_rule' not in s and s.get('reminder_every') == 0:
        rid = 'off'
    if rid not in all_rules:
        rid = default_rule_id(c, envelope['entity'], all_rules, chosen)
    return all_rules.get(rid) or {'id': 'off', 'name': 'Off', 'max_count': 0, 'first_after': 2, 'every': 3, 'weekdays': [0, 1, 2, 3, 4], 'hour_from': 9,
                                  'hour_to': 17, 'combine': True, 'skip_opened_hours': 24, 'remind_viewers': False, 'allow_signer_stop': True,
                                  'notify_sender_after_last': False, 'active': True}


def summary(rule: dict) -> str:
    if not rule['max_count']:
        return 'No automatic reminders'
    days = sorted(set(rule['weekdays'] or range(7)))
    when = 'every day' if days == list(range(7)) else 'Mon–Fri' if days == [0, 1, 2, 3, 4] else ', '.join(DAY_NAMES[d][:3] for d in days)
    hours = 'any time' if (rule['hour_from'], rule['hour_to']) == (0, 24) else f"{rule['hour_from']}:00–{rule['hour_to']}:00"
    unit = 'working day' if days != list(range(7)) else 'day'
    first = f"After {rule['first_after']} {unit}{'s' if rule['first_after'] != 1 else ''}"
    then = f", then every {rule['every']}" if rule['max_count'] > 1 else ''
    tail = ' · then the sender is told' if rule['notify_sender_after_last'] else ''
    return f"{first}{then} · at most {rule['max_count']} · {when} {hours}{tail}"


def rule_view(rule: dict, used: int = 0, default_for: list | None = None) -> dict:
    keys = ('id', 'name', 'description', 'first_after', 'every', 'max_count', 'weekdays', 'hour_from', 'hour_to', 'combine', 'skip_opened_hours',
            'remind_viewers', 'allow_signer_stop', 'notify_sender_after_last', 'active', 'builtin')
    return {k: rule[k] for k in keys} | {'summary': summary(rule), 'envelopes': used, 'default_for': default_for or []}


NEW_RULE = dict(first_after=2, every=3, max_count=4, weekdays=[0, 1, 2, 3, 4], hour_from=9, hour_to=17, skip_opened_hours=24, combine=True,
                remind_viewers=False, allow_signer_stop=True, notify_sender_after_last=True)


def validate_rule(payload: dict, current: dict | None = None) -> dict:
    cur = current or NEW_RULE           # a new rule starts from the Standard settings
    def whole(key, low, high, label):
        try:
            value = int(payload.get(key, cur.get(key)))
        except (TypeError, ValueError):
            raise ValueError(f'{label}: enter a whole number.') from None
        if not low <= value <= high:
            raise ValueError(f'{label}: enter a number from {low} to {high}.')
        return value
    name = re.sub(r'\s+', ' ', str(payload.get('name', cur.get('name', '')) or '')).strip()
    if not 1 <= len(name) <= 80:
        raise ValueError('Give the rule a name of up to 80 characters.')
    out = {'name': name, 'description': str(payload.get('description', cur.get('description', '')) or '').strip()[:500],
           'first_after': whole('first_after', 1, 30, 'First reminder'), 'every': whole('every', 1, 30, 'Then every'),
           'max_count': whole('max_count', 0, 20, 'At most'), 'hour_from': whole('hour_from', 0, 23, 'From'), 'hour_to': whole('hour_to', 1, 24, 'Until'),
           'skip_opened_hours': whole('skip_opened_hours', 0, 168, 'Skip if opened within')}
    if out['hour_to'] <= out['hour_from']:
        raise ValueError('The end of the sending hours must be after the start.')
    days = payload.get('weekdays', cur.get('weekdays'))
    if not isinstance(days, list) or not days or any(not isinstance(d, int) or not 0 <= d <= 6 for d in days):
        raise ValueError('Choose at least one day on which reminders may be sent.')
    out['weekdays'] = sorted(set(days))
    for key in ('combine', 'remind_viewers', 'allow_signer_stop', 'notify_sender_after_last'):
        value = payload.get(key, cur.get(key, False))
        out[key] = value is True
    return out


# ------------------------------------------------------------------ time: working days and sending hours
_TZ: dict[str, dt.tzinfo] = {}


def tz_of(entity: str) -> dt.tzinfo:
    """The company's time zone (Admin › Company settings; a change applies at once). Unknown company or zone: UTC."""
    from entities import ENTITIES
    name = str((ENTITIES.get(entity) or {}).get('timezone') or 'UTC')
    if name not in _TZ:
        try:
            _TZ[name] = zoneinfo.ZoneInfo(name)
        except Exception:  # noqa: BLE001
            _TZ[name] = dt.UTC
    return _TZ[name]


def _days(rule: dict) -> set[int]:
    return set(rule.get('weekdays') or range(7))


def in_window(moment: dt.datetime, rule: dict) -> bool:
    return moment.weekday() in _days(rule) and rule['hour_from'] <= moment.hour < rule['hour_to']


def to_window(moment: dt.datetime, rule: dict) -> dt.datetime:
    """The first moment at or after `moment` inside the sending days and hours (wall-clock time of the company)."""
    tz = moment.tzinfo
    m = moment
    for _ in range(15):
        if in_window(m, rule):
            return m
        if m.weekday() in _days(rule) and m.hour < rule['hour_from']:
            return dt.datetime(m.year, m.month, m.day, rule['hour_from'], tzinfo=tz)
        nxt = (m + dt.timedelta(days=1)).date()
        m = dt.datetime(nxt.year, nxt.month, nxt.day, rule['hour_from'], tzinfo=tz)
    return m


def add_working(moment: dt.datetime, n: int, rule: dict) -> dt.datetime:
    """`n` sending days later, at the same wall-clock time."""
    days = _days(rule)
    d = moment.date()
    added = 0
    while added < n:
        d += dt.timedelta(days=1)
        if d.weekday() in days:
            added += 1
    return dt.datetime(d.year, d.month, d.day, moment.hour, moment.minute, moment.second, tzinfo=moment.tzinfo)


def _local(ts: float, entity: str) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, tz_of(entity))


def schedule_after(base: float, n: int, rule: dict, entity: str) -> float:
    return to_window(add_working(_local(base, entity), n, rule), rule).timestamp()


def window_from(ts: float, rule: dict, entity: str) -> float:
    return to_window(_local(ts, entity), rule).timestamp()


# ------------------------------------------------------------------ who may be emailed
def active_optouts(c, t: float) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in c.execute(select(optouts).where(optouts.c.removed_at.is_(None), or_(optouts.c.until.is_(None), optouts.c.until > t))).mappings():
        out.setdefault(r['email'], []).append(dict(r))
    return out


def opted_out(blocked: dict, email: str, entity: str) -> dict | None:
    for o in blocked.get((email or '').lower(), []):
        if o['entity'] in (None, '', entity):
            return o
    return None


def may_email(c, envelope: dict, r: dict) -> bool:
    """For automatic emails other than reminders (the expiration warning): the same switches apply."""
    t = now()
    if r.get('reminders_off') or (r.get('reminders_paused_until') or 0) > t or r.get('delivery') in ('bounced', 'failed'):
        return False
    return opted_out(active_optouts(c, t), r.get('email'), envelope['entity']) is None


def _last_activity(c, recipient_ids: list[str]) -> dict[str, float]:
    """The last time each person opened or worked on their envelope."""
    from esign_db import events
    out: dict[str, float] = {}
    for i in range(0, len(recipient_ids), 500):
        part = recipient_ids[i:i + 500]
        for rid, at in c.execute(select(events.c.recipient_id, events.c.at).where(events.c.recipient_id.in_(part),
                                 events.c.kind.in_(('delivered', 'consent', 'auth_passed', 'adopted', 'saved', 'downloaded', 'printed', 'portal_session')))):
            if at > out.get(rid, 0):
                out[rid] = at
    return out


# ------------------------------------------------------------------ the plan: every waiting person's next reminder
def plan(c, t: float, where=None, with_waiting: bool = False) -> list[dict]:
    """One entry per person on an envelope out for signature: what happens next and when.

    state: scheduled (due at `due`), final (the sender is told at `due`), done (all reminders sent and the sender told or not
    to be told), off (switched off for this person), paused, opted_out, undeliverable, rule_off, not_reminded (a copy or
    in-person signer, or a viewer under a rule that does not remind viewers), waiting_turn (not yet invited)."""
    from esign_db import envelopes, recipients
    all_rules = load_rules(c)
    chosen = defaults(c)
    global_pause = float(_setting(c, SETTING_PAUSE, 0) or 0)
    installed = float(_setting(c, SETTING_INSTALLED, 0) or 0)
    q = select(envelopes.c.id, envelopes.c.ref, envelopes.c.entity, envelopes.c.subject, envelopes.c.settings, envelopes.c.sent_at, envelopes.c.owner_id,
               envelopes.c.client_id, envelopes.c.test).where(envelopes.c.status == 'sent')
    if where is not None:
        q = q.where(where)
    envs = {e['id']: dict(e) for e in c.execute(q).mappings()}
    if not envs:
        return []
    statuses = OPEN + (('created',) if with_waiting else ())
    rows = []
    ids = list(envs)
    for i in range(0, len(ids), 500):
        rows += [dict(r) for r in c.execute(select(recipients).where(recipients.c.envelope_id.in_(ids[i:i + 500]), recipients.c.status.in_(statuses))
                                            .order_by(recipients.c.envelope_id, recipients.c.routing_order, recipients.c.created)).mappings()]
    blocked = active_optouts(c, t)
    activity = _last_activity(c, [r['id'] for r in rows if r['status'] in OPEN])
    out = []
    for r in rows:
        e = envs[r['envelope_id']]
        rule = rule_for(c, e, all_rules, chosen)
        s = e['settings'] or {}
        item = {'recipient': r, 'envelope': e, 'rule': rule, 'email': (r['email'] or '').lower(), 'entity': e['entity'], 'due': None,
                'number': r['reminders_sent'] + 1, 'max': rule['max_count'], 'state': 'scheduled', 'reason': ''}
        out.append(item)
        if r['status'] == 'created':
            item.update(state='waiting_turn', reason='Invited when it is their turn to sign')
            continue
        if r['role'] in ('cc', 'in_person') or (r['role'] == 'viewer' and not rule['remind_viewers']):
            item.update(state='not_reminded', reason={'cc': 'Receives a copy', 'in_person': 'Signs in person with a host',
                                                      'viewer': 'Only views a copy'}[r['role']])
            continue
        if r['delivery'] in ('bounced', 'failed'):
            item.update(state='undeliverable', reason='Their email address did not accept our emails')
            continue
        o = opted_out(blocked, r['email'], e['entity'])
        if o:
            item.update(state='opted_out', reason=f"On the do-not-remind list: {o['reason']}", optout=o)
            continue
        if r['reminders_off']:
            item.update(state='off', reason={'signer': 'Stopped by the recipient from the reminder email', 'staff': 'Switched off',
                                             'sender': 'Switched off when sending'}.get(r['reminders_off_source'] or '', 'Switched off'))
            continue
        if not rule['max_count']:
            item.update(state='rule_off', reason=f"Rule: {rule['name']}")
            continue
        base = max(x for x in (r['last_reminder_at'], r['sent_at'] or e['sent_at'], r['reminder_anchor'], 0) if x is not None)
        if r['reminders_sent'] >= rule['max_count']:
            # the last reminder went out: tell the sender once (only for reminders sent by this release, so an update does
            # not notify every sender of every old envelope on the first day)
            if (rule['notify_sender_after_last'] and not r['reminder_final_notice_at'] and r['last_reminder_at']
                    and r['last_reminder_at'] >= installed):
                item.update(state='final', due=schedule_after(r['last_reminder_at'], rule['every'], rule, e['entity']))
            else:
                item.update(state='done', reason='All reminders sent')
            continue
        n = rule['every'] if (r['last_reminder_at'] or r['reminder_anchor']) else rule['first_after']
        due = schedule_after(base, n, rule, e['entity'])
        pause = max(global_pause, float(s.get('reminders_paused_until') or 0), float(r['reminders_paused_until'] or 0))
        if pause > t:
            item.update(state='paused', paused_until=pause, reason='Paused for everyone' if pause == global_pause else
                        'Paused for this envelope' if pause == float(s.get('reminders_paused_until') or 0) else 'Paused for this person')
            due = max(due, window_from(pause, rule, e['entity'])) if pause < FOREVER else None
        seen = activity.get(r['id'])
        if due and rule['skip_opened_hours'] and seen and due < seen + rule['skip_opened_hours'] * 3600:
            due = window_from(seen + rule['skip_opened_hours'] * 3600, rule, e['entity'])
            item['after_open'] = True
        item['due'] = due
    return out


# ------------------------------------------------------------------ sending
def _recent_automatic(c, t: float) -> list[dict]:
    from esign_db import outbox
    return [dict(r) for r in c.execute(select(outbox.c.id, outbox.c.entity, outbox.c.to_addrs, outbox.c.state, outbox.c.created, outbox.c.kind)
                                       .where(outbox.c.kind.in_(AUTO_KINDS),
                                              # still waiting or unconfirmed, however old: nothing new piles up on top of it
                                              or_(outbox.c.created > t - 7 * DAY, outbox.c.state.in_(('queued', 'sending', 'uncertain'))))).mappings()]


def blockers(recent: list[dict], email: str, entity: str, t: float) -> str:
    """Why an automatic reminder to this person cannot go now ('' when it can)."""
    email = email.lower()
    mine = [m for m in recent if email in [a.lower() for a in (m['to_addrs'] or [])]]
    if any(m['state'] in ('queued', 'sending', 'uncertain') and m['entity'] == entity for m in mine):
        return 'An earlier reminder to this person is still waiting to go out'
    today = [m for m in mine if m['created'] > t - DAY and m['state'] not in ('failed',)]
    if sum(1 for m in today if m['entity'] == entity) >= PER_COMPANY_PER_DAY:
        return 'Already reminded today by this company'
    if len(today) >= PER_PERSON_PER_DAY:
        return 'Already received two automatic emails today'
    return ''


def stop_token(recipient_id: str) -> str | None:
    import secretbox
    try:
        key = secretbox._key()
    except Exception:  # noqa: BLE001  (no installation key: no stop link)
        return None
    return recipient_id + hmac.new(key, b'reminders-stop:' + recipient_id.encode(), hashlib.sha256).hexdigest()[:32]


def check_stop_token(token: str) -> str | None:
    if not re.fullmatch(r'[a-f0-9]{64}', token or ''):
        return None
    rid = token[:32]
    good = stop_token(rid)
    return rid if good and hmac.compare_digest(good, token) else None


def stop_url(recipient_id: str) -> str | None:
    import mailer
    token = stop_token(recipient_id)
    return f'{mailer.origin()}/reminders/stop/{token}' if token else None


def _send(c, items: list[dict], t: float, actor_name: str = 'TFSC Agreements', manual: bool = False) -> int:
    """Email one reminder, or one combined reminder for several envelopes of the same person and company, and record it.
    Everything is re-read under the envelope locks first: a person who signed a moment ago is not reminded."""
    import esign
    import mailer
    from esign_db import recipients
    fresh = []
    for item in sorted(items, key=lambda i: i['envelope']['id']):
        envelope = esign.lock(c, item['envelope']['id'])
        r = c.execute(select(recipients).where(recipients.c.id == item['recipient']['id'])).mappings().first()
        if (envelope['status'] != 'sent' or not r or r['status'] not in OPEN or (not manual and r['reminders_off'])
                or r['last_reminder_at'] != item['recipient']['last_reminder_at'] or r['delivery'] in ('bounced', 'failed')):
            continue
        fresh.append((dict(envelope), dict(r), item))
    if not fresh:
        return 0
    if len(fresh) == 1:
        envelope, r, item = fresh[0]
        extra = {'reminder_number': r['reminders_sent'] + 1, 'reminder_max': item['max'] if r['reminders_sent'] < item['max'] else 0}
        if item['rule']['allow_signer_stop']:
            extra['stop_url'] = stop_url(r['id'])
        esign.recipient_email(c, envelope, r, 'reminder', extra)
    else:
        envelope = fresh[0][0]
        from entities import get_entity
        entity = get_entity(envelope['entity'])
        box = mailer.load(c, envelope['entity'])
        owner = esign.user_row(c, envelope['owner_id'])
        entries = []
        from esign_db import documents
        for env, r, item in fresh:
            docs = [d['name'] for d in c.execute(select(documents.c.name).where(documents.c.envelope_id == env['id']).order_by(documents.c.position)).mappings()]
            entries.append({'subject': env['subject'], 'documents': docs, 'url': esign.sign_url(esign.issue_token(c, r['id'])), 'expires': esign.expires_label(env),
                            'number': r['reminders_sent'] + 1, 'max': item['max'], 'role': r['role'],
                            'stop_url': stop_url(r['id']) if item['rule']['allow_signer_stop'] else None})
        first = fresh[0][1]
        ctx = dict(entity=entity, box=box, recipient_name=first['name'] or first['email'], sender_name=entity['display'],
                   sender_email=box['address'] or owner['email'], items=entries, subject=f'{len(entries)} documents are waiting for your signature')
        subject, body, html_body = mailer.compose('reminder_digest', ctx)
        mailer.enqueue(c, envelope['entity'], 'reminder_digest', [first['email']], subject, body, html_body, envelope_id=envelope['id'], recipient_id=first['id'])
    for env, r, item in fresh:
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(last_reminder_at=t, reminders_sent=r['reminders_sent'] + 1, reminder_anchor=None))
        esign.record(c, env['id'], 'reminder', actor_name, recipient_id=r['id'],
                     detail={'to': r['email'], 'number': r['reminders_sent'] + 1, 'of': item['max'], 'combined': len(fresh) if len(fresh) > 1 else None,
                             'manual': manual or None, 'rule': item['rule']['name']})
    return len(fresh)


def tell_sender(c, envelope: dict, event: str, who: str, detail: str = '') -> None:
    """The sender (and the company's compliance mailbox) is told; always sent, whatever the notification settings."""
    import esign
    import mailer
    from entities import get_entity
    box = mailer.load(c, envelope['entity'])
    if not mailer.ready(box):
        return
    owner = esign.user_row(c, envelope['owner_id'])
    ctx = dict(entity=get_entity(envelope['entity']), box=box, subject=envelope['subject'], ref=envelope['ref'], actor=who, detail=detail,
               url=esign.portal_url(envelope), attached=False)
    subject, body, html_body = mailer.compose('notify_' + event, ctx)
    to = [owner['email']] if owner['active'] else []
    if to or box['address']:
        mailer.enqueue(c, envelope['entity'], 'notify_' + event, to or [box['address']], subject, body, html_body, envelope_id=envelope['id'],
                       bcc=[box['address']] if to and box['address'] else [])


def run(t: float | None = None) -> int:
    """One pass of the worker: send the reminders that are due, then the notices to senders. Returns emails queued."""
    import esign
    t = now() if t is None else t
    with db.engine.connect() as c:
        if float(_setting(c, SETTING_PAUSE, 0) or 0) > t:
            return 0
        items = plan(c, t)
        recent = _recent_automatic(c, t)
    due = [i for i in items if i['state'] == 'scheduled' and i['due'] and i['due'] <= t and in_window(_local(t, i['entity']), i['rule'])]
    sent = 0
    done: set[str] = set()
    groups: dict[tuple, list] = {}
    for i in sorted(due, key=lambda i: i['due']):
        groups.setdefault((i['email'], i['entity']), []).append(i)
    for (email, entity), group in groups.items():
        if sent >= RUN_LIMIT:
            break
        if not email or blockers(recent, email, entity, t):
            continue
        if group[0]['rule']['combine']:
            # other envelopes of the same person and company due within a day go in the same email
            ahead = [i for i in items if i['state'] == 'scheduled' and i['email'] == email and i['entity'] == entity and i not in group and i['due']
                     and i['due'] <= t + COMBINE_AHEAD and i['rule']['combine']]
            batch = [i for i in group if i['rule']['combine']] + ahead
            batch = batch or group[:1]
        else:
            batch = group[:1]
        try:
            with esign.write_tx() as c:
                n = _send(c, batch, t)
        except Exception:  # noqa: BLE001  (one person's reminder never stops the others; it is tried next minute)
            import logging
            logging.getLogger('tfsc.reminders').exception('reminder to %s could not be queued', email)
            continue
        if n:
            sent += n
            done.update(i['recipient']['id'] for i in batch)
            recent.append({'entity': entity, 'to_addrs': [email], 'state': 'queued', 'created': t, 'kind': 'reminder'})
    for i in items:
        if i['state'] != 'final' or not i['due'] or i['due'] > t or not in_window(_local(t, i['entity']), i['rule']):
            continue
        try:
            with esign.write_tx() as c:
                from esign_db import recipients
                envelope = esign.lock(c, i['envelope']['id'])
                r = c.execute(select(recipients).where(recipients.c.id == i['recipient']['id'])).mappings().first()
                if envelope['status'] != 'sent' or not r or r['status'] not in OPEN or r['reminder_final_notice_at']:
                    continue
                c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminder_final_notice_at=t))
                who = r['name'] or r['email']
                tell_sender(c, dict(envelope), 'reminders_exhausted', who,
                            f"{who} ({r['email']}) has not signed after {r['reminders_sent']} reminder{'s' if r['reminders_sent'] != 1 else ''}. "
                            'No more reminders are sent automatically. You can remind them once more from the envelope, or follow up by phone.')
                esign.record(c, envelope['id'], 'reminders_exhausted', 'TFSC Agreements', recipient_id=r['id'], detail={'to': r['email']})
                sent += 1
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger('tfsc.reminders').exception('final notice for %s could not be queued', i['recipient']['id'])
    return sent


# ------------------------------------------------------------------ actions on one person
def _envelope_for(c, actor: dict, envelope_id: str) -> dict:
    import esign
    return esign.get_envelope(c, actor, envelope_id)


def _recipient(c, envelope: dict, recipient_id: str) -> dict:
    from esign_db import recipients
    r = c.execute(select(recipients).where(recipients.c.id == recipient_id, recipients.c.envelope_id == envelope['id'])).mappings().first()
    if not r:
        raise HTTPException(404, 'Recipient not found.')
    return dict(r)


def remind_now(c, actor: dict, envelope: dict, r: dict, t: float) -> None:
    if envelope['status'] != 'sent':
        raise ValueError('Only an envelope that is waiting for signatures can be reminded.')
    if r['status'] not in OPEN:
        raise ValueError('This person has nothing to sign right now.')
    if r['role'] in ('cc', 'in_person'):
        raise ValueError('People who receive a copy or sign in person are not reminded by email.')
    if r['delivery'] in ('bounced', 'failed'):
        raise ValueError("Their email address did not accept our emails. Correct it on the envelope first.")
    if r['reminders_off']:
        raise ValueError(f"Reminders are switched off for {r['name'] or r['email']}"
                         + (' (they stopped them from a reminder email).' if r['reminders_off_source'] == 'signer' else '. Switch them on first.'))
    o = opted_out(active_optouts(c, t), r['email'], envelope['entity'])
    if o:
        raise ValueError(f"{r['name'] or r['email']} is on the do-not-remind list ({o['reason']}).")
    last = max(x for x in (r['last_reminder_at'], r['sent_at'], 0) if x is not None)
    if last > t - DAY:
        allowed = dt.datetime.fromtimestamp(last + DAY, tz_of(envelope['entity'])).strftime('%b %d, %H:%M').replace(' 0', ' ')
        raise ValueError(f'They received an email about this envelope less than 24 hours ago. You can remind them again from {allowed}.')
    recent = _recent_automatic(c, t)
    email = (r['email'] or '').lower()
    if any(m['state'] in ('queued', 'sending', 'uncertain') and email in [a.lower() for a in (m['to_addrs'] or [])] for m in recent):
        raise ValueError('An earlier reminder to this person is still waiting to go out.')
    import esign
    all_rules = load_rules(c)
    rule = rule_for(c, envelope, all_rules)
    _send(c, [{'envelope': envelope, 'recipient': r, 'rule': rule, 'max': rule['max_count']}], t, actor_name=actor['name'], manual=True)


def set_person(c, actor: dict, envelope: dict, r: dict, action: str, until: float | None, t: float) -> None:
    import esign
    from esign_db import recipients
    who = r['name'] or r['email']
    if action == 'off':
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminders_off=True, reminders_off_source='staff'))
        esign.record(c, envelope['id'], 'reminders_off', actor['name'], recipient_id=r['id'], detail={'to': r['email']})
    elif action == 'on':
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminders_off=False, reminders_off_source=None, reminders_paused_until=None,
                                                                              reminder_anchor=t))
        esign.record(c, envelope['id'], 'reminders_on', actor['name'], recipient_id=r['id'], detail={'to': r['email']})
    elif action == 'pause':
        if not until or until <= t:
            raise ValueError('Choose a date in the future to pause until.')
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminders_paused_until=until))
        esign.record(c, envelope['id'], 'reminders_paused', actor['name'], recipient_id=r['id'], detail={'to': r['email'], 'until': until})
    elif action == 'resume':
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminders_paused_until=None))
        esign.record(c, envelope['id'], 'reminders_resumed', actor['name'], recipient_id=r['id'], detail={'to': r['email']})
    elif action == 'skip':
        item = next((i for i in plan(c, t, envelopes_id_is(envelope['id'])) if i['recipient']['id'] == r['id']), None)
        if not item or item['state'] not in ('scheduled', 'paused') or not item['due']:
            raise ValueError(f'No reminder is planned for {who}.')
        c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminder_anchor=item['due']))
        esign.record(c, envelope['id'], 'reminder_skipped', actor['name'], recipient_id=r['id'], detail={'to': r['email']})
    elif action == 'remind_now':
        remind_now(c, actor, envelope, r, t)
    else:
        raise ValueError('Unknown action.')
    db.log(c, actor, f'esign.reminders_{action}', envelope['client_id'], {'envelope': envelope['ref'], 'to': esign.mask_email(r['email'])})


def envelopes_id_is(envelope_id: str):
    from esign_db import envelopes
    return envelopes.c.id == envelope_id


# ------------------------------------------------------------------ views for the screens
def _when(ts: float | None) -> float | None:
    return round(ts, 3) if ts else None


def person_view(c, item: dict, rule: dict, t: float) -> dict:
    """One person's reminder timeline: what was sent and what comes next."""
    from esign_db import events
    r, e = item['recipient'], item['envelope']
    steps = []
    if r['sent_at']:
        steps.append({'label': 'Invitation', 'at': _when(r['sent_at']), 'kind': 'done'})
    for at, detail in c.execute(select(events.c.at, events.c.detail).where(events.c.recipient_id == r['id'], events.c.kind == 'reminder').order_by(events.c.seq)):
        number = (detail or {}).get('number')
        steps.append({'label': f'Reminder {number}' if number else 'Reminder', 'at': _when(at), 'kind': 'done',
                      'note': 'combined' if (detail or {}).get('combined') else 'sent by hand' if (detail or {}).get('manual') else ''})
    if item['state'] in ('scheduled', 'paused') and item['due']:
        due, n, sent = item['due'], item['number'], r['reminders_sent']
        while n <= rule['max_count'] and len(steps) < 30:
            steps.append({'label': f'Reminder {n}', 'at': _when(due), 'kind': 'next' if n == sent + 1 else 'planned'})
            n += 1
            due = schedule_after(due, rule['every'], rule, e['entity'])
        if rule['notify_sender_after_last']:
            steps.append({'label': 'Sender told, if not signed', 'at': _when(due), 'kind': 'planned'})
    elif item['state'] == 'final':
        steps.append({'label': 'Sender told, if not signed', 'at': _when(item['due']), 'kind': 'next'})
    elif item['state'] == 'waiting_turn':
        steps.append({'label': 'Invited when it is their turn', 'at': None, 'kind': 'planned'})
    if r['reminder_final_notice_at']:
        steps.append({'label': 'Sender told', 'at': _when(r['reminder_final_notice_at']), 'kind': 'done'})
    last = max(x for x in (r['last_reminder_at'], r['sent_at'], 0) if x is not None)
    return {'id': r['id'], 'name': r['name'], 'email': r['email'], 'role': r['role'], 'status': r['status'], 'routing_order': r['routing_order'],
            'state': item['state'], 'reason': item.get('reason', ''), 'due': _when(item['due']), 'paused_until': _when(r['reminders_paused_until']),
            'reminders_off': bool(r['reminders_off']), 'off_source': r['reminders_off_source'], 'reminders_sent': r['reminders_sent'],
            'max': rule['max_count'], 'steps': steps, 'after_open': bool(item.get('after_open')),
            'remind_now_from': _when(last + DAY) if last > t - DAY else None,
            'optout': {k: item['optout'][k] for k in ('id', 'reason', 'entity', 'created_by_name', 'created')} if item.get('optout') else None}


def envelope_view(c, actor: dict, envelope: dict, t: float) -> dict:
    all_rules = load_rules(c)
    rule = rule_for(c, envelope, all_rules)
    items = plan(c, t, envelopes_id_is(envelope['id']), with_waiting=True) if envelope['status'] == 'sent' else []
    from entities import ENTITIES
    tzname = getattr(tz_of(envelope['entity']), 'key', 'UTC')
    s = envelope['settings'] or {}
    return {'status': envelope['status'], 'rule': rule_view(rule), 'chosen': s.get('reminder_rule') or '', 'default_rule': default_rule_id(c, envelope['entity'], all_rules),
            'rules': [rule_view(x) for x in all_rules.values() if x['active']], 'timezone': tzname,
            'company': (ENTITIES[envelope['entity']].get('short') if envelope['entity'] in ENTITIES else envelope['entity']),
            'paused_until': _when(float(s.get('reminders_paused_until') or 0) or None), 'global_paused_until': _when(float(_setting(c, SETTING_PAUSE, 0) or 0) or None),
            'people': [person_view(c, i, rule, t) for i in items], 'can_manage_list': actor['role'] == 'super_admin'}


def centre(c, actor: dict, t: float, days: int = 7) -> dict:
    import esign
    from esign_db import envelopes, outbox
    visible = esign.visible(actor)
    items = plan(c, t, visible)
    horizon = t + days * DAY
    recent = _recent_automatic(c, t)
    def row(i):
        r, e = i['recipient'], i['envelope']
        return {'recipient_id': r['id'], 'envelope_id': e['id'], 'ref': e['ref'], 'subject': e['subject'], 'name': r['name'], 'email': r['email'],
                'entity': e['entity'], 'due': _when(i['due']), 'number': i['number'], 'max': i['max'], 'rule': i['rule']['name'], 'state': i['state'],
                'reason': i.get('reason', ''), 'timezone': getattr(tz_of(e['entity']), 'key', 'UTC'), 'paused_until': _when(i.get('paused_until')),
                'held': blockers(recent, i['email'], i['entity'], max(t, i['due'] or t)) if i['state'] == 'scheduled' else '', 'after_open': bool(i.get('after_open'))}
    upcoming = sorted((row(i) for i in items if i['state'] in ('scheduled', 'final') and i['due'] and i['due'] <= horizon), key=lambda x: x['due'])
    held = sorted((row(i) for i in items if i['state'] in ('paused', 'off', 'opted_out', 'undeliverable', 'rule_off')), key=lambda x: (x['state'], x['name'] or ''))
    sent_q = (select(outbox.c.id, outbox.c.kind, outbox.c.to_addrs, outbox.c.subject, outbox.c.state, outbox.c.created, outbox.c.sent_at, outbox.c.envelope_id,
                     outbox.c.entity, envelopes.c.ref, envelopes.c.subject.label('envelope_subject'))
              .join(envelopes, envelopes.c.id == outbox.c.envelope_id).where(outbox.c.kind.in_(AUTO_KINDS), outbox.c.created > t - 30 * DAY, visible)
              .order_by(outbox.c.created.desc()).limit(300))
    sent = [{'id': m['id'], 'kind': m['kind'], 'to': ', '.join(m['to_addrs'] or []), 'subject': m['subject'], 'state': m['state'], 'created': m['created'],
             'sent_at': m['sent_at'], 'envelope_id': m['envelope_id'], 'ref': m['ref'], 'envelope_subject': m['envelope_subject']}
            for m in c.execute(sent_q).mappings()]
    attention_q = (select(outbox.c.id, outbox.c.kind, outbox.c.to_addrs, outbox.c.subject, outbox.c.created, outbox.c.handed_at, outbox.c.last_error,
                          outbox.c.envelope_id, outbox.c.attachments, envelopes.c.ref)
                   .join(envelopes, envelopes.c.id == outbox.c.envelope_id).where(outbox.c.state == 'uncertain', visible).order_by(outbox.c.created.desc()).limit(200))
    attention = [{'id': m['id'], 'kind': m['kind'], 'to': ', '.join(m['to_addrs'] or []), 'subject': m['subject'], 'created': m['created'],
                  'handed_at': m['handed_at'], 'reason': m['last_error'] or '', 'envelope_id': m['envelope_id'], 'ref': m['ref'],
                  'attachments': len(m['attachments'] or [])} for m in c.execute(attention_q).mappings()]
    week = [m for m in sent if m['created'] > t - 7 * DAY]
    blocked = active_optouts(c, t)
    return {'upcoming': upcoming, 'held': held, 'sent': sent, 'attention': attention,
            'counts': {'upcoming': len(upcoming), 'people': len({u['email'] for u in upcoming}), 'sent_week': len(week),
                       'held': len(held), 'optouts': sum(len(v) for v in blocked.values()), 'attention': len(attention)},
            'paused_until': _when(float(_setting(c, SETTING_PAUSE, 0) or 0) or None), 'days': days, 'is_super': actor['role'] == 'super_admin'}


# ------------------------------------------------------------------ the page a signer sees from "Stop reminders"
def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>{html.escape(title)}</title><link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/ui.css"></head>
<body class="stop-page"><main class="stop-wrap"><section class="stop-card">{body}</section></main></body></html>'''
    return HTMLResponse(doc, status, headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


def _stop_context(c, token: str):
    from esign_db import envelopes, recipients
    rid = check_stop_token(token)
    if not rid:
        return None, None
    r = c.execute(select(recipients).where(recipients.c.id == rid)).mappings().first()
    if not r:
        return None, None
    e = c.execute(select(envelopes).where(envelopes.c.id == r['envelope_id'])).mappings().first()
    return (dict(r), dict(e)) if e else (None, None)


def stop_page(r: dict | None, e: dict | None, token: str, done: bool = False) -> HTMLResponse:
    from entities import ENTITIES
    if not r or not e:
        return _page('Link not valid', '<p class="eyebrow">Reminders</p><h1>This link is not valid</h1><p>Please use the link from your most recent '
                     'reminder email, or reply to that email.</p>', 404)
    company = html.escape(ENTITIES[e['entity']].get('display', e['entity']) if e['entity'] in ENTITIES else e['entity'])
    subject = html.escape(e['subject'])
    if done or r['reminders_off']:
        return _page('Reminders stopped', f'<div class="stop-ok" aria-hidden="true">✓</div><h1>Reminders stopped</h1><p>You will not receive more '
                     f'reminders about <strong>{subject}</strong> from {company}.</p><p>{company} has been told. You can still review and sign at any '
                     'time with the button in your earlier email.</p>')
    if e['status'] != 'sent' or r['status'] not in OPEN:
        return _page('Nothing to stop', f'<p class="eyebrow">{company}</p><h1>No reminders are planned</h1><p><strong>{subject}</strong> is no longer '
                     'waiting for you, so you will not receive reminders about it.</p>')
    # Stopping takes a press of the button: mail scanners and link previews open links on their own, and must not stop anything.
    return _page('Stop reminders', f'<p class="eyebrow">{company}</p><h1>Stop reminders for these documents?</h1><p>You will not receive more '
                 f'reminders about <strong>{subject}</strong>. {company} will be told, and you can still review and sign at any time.</p>'
                 f'<form method="post" action="/reminders/stop/{html.escape(token)}"><button class="button button-primary" type="submit">Stop reminders</button></form>')


# ------------------------------------------------------------------ routes
def install(app) -> None:
    def actor_of(request, c):
        return sec.get_actor(request, c)

    def super_admin(a):
        if a['role'] != 'super_admin':
            raise HTTPException(403, 'Only the Super Admin can change reminder rules and the do-not-remind list.')

    def fail(exc: ValueError):
        raise HTTPException(400, str(exc))

    def used_counts(c) -> dict:
        from esign_db import envelopes
        all_rules = load_rules(c)
        chosen = defaults(c)
        out: dict[str, int] = {}
        for e in c.execute(select(envelopes.c.entity, envelopes.c.settings).where(envelopes.c.status == 'sent')).mappings():
            rid = rule_for(c, dict(e), all_rules, chosen)['id']
            out[rid] = out.get(rid, 0) + 1
        return out

    def rules_payload(c) -> dict:
        from entities import ENTITIES
        all_rules = load_rules(c)
        chosen = defaults(c)
        used = used_counts(c)
        companies = [{'name': n, 'short': x.get('short') or n, 'rule': default_rule_id(c, n, all_rules),
                      'timezone': getattr(tz_of(n), 'key', 'UTC')} for n, x in ENTITIES.items()]
        default_for = {}
        for comp in companies:
            default_for.setdefault(comp['rule'], []).append(comp['short'])
        del chosen
        return {'rules': [rule_view(r, used.get(r['id'], 0), default_for.get(r['id'])) for r in all_rules.values()], 'companies': companies,
                'paused_until': _when(float(_setting(c, SETTING_PAUSE, 0) or 0) or None),
                'guards': {'per_company_per_day': PER_COMPANY_PER_DAY, 'per_person_per_day': PER_PERSON_PER_DAY}}

    @app.get('/api/reminders/rules')
    def api_rules(request: Request):
        with db.engine.connect() as c:
            actor_of(request, c)
            return rules_payload(c)

    @app.post('/api/reminders/rules')
    def api_rule_create(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            super_admin(a)
            try:
                values = validate_rule(payload)
            except ValueError as exc:
                fail(exc)
            base = re.sub(r'[^a-z0-9]+', '-', values['name'].lower()).strip('-')[:30] or 'rule'
            rid, n = base, 1
            while c.execute(select(rules.c.id).where(rules.c.id == rid)).first():
                n += 1
                rid = f'{base}-{n}'
            t = time.time()
            c.execute(rules.insert().values(id=rid, active=True, builtin=False, created=t, updated=t, updated_by=a['id'], **values))
            db.log(c, a, 'reminders.rule_created', detail={'rule': rid, 'name': values['name']})
            return rules_payload(c) | {'id': rid}

    @app.post('/api/reminders/rules/{rule_id}')
    def api_rule_save(rule_id: str, request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            super_admin(a)
            cur = c.execute(select(rules).where(rules.c.id == rule_id)).mappings().first()
            if not cur:
                raise HTTPException(404, 'Rule not found.')
            values = {}
            if 'active' in payload:
                active = payload['active'] is True
                if not active and cur['builtin']:
                    raise HTTPException(400, 'The built-in rules can be changed but not retired.')
                if not active and rule_id in defaults(c).values():
                    raise HTTPException(400, 'This rule is a company default. Choose another default for that company first.')
                values['active'] = active
            if any(k in payload for k in ('name', 'first_after', 'every', 'max_count', 'weekdays', 'hour_from', 'hour_to', 'skip_opened_hours', 'combine',
                                          'remind_viewers', 'allow_signer_stop', 'notify_sender_after_last', 'description')):
                try:
                    values |= validate_rule(payload, dict(cur))
                except ValueError as exc:
                    fail(exc)
            if rule_id == 'off':
                values['max_count'] = 0           # "Off" stays off whatever is typed
            c.execute(rules.update().where(rules.c.id == rule_id).values(updated=time.time(), updated_by=a['id'], **values))
            db.log(c, a, 'reminders.rule_changed', detail={'rule': rule_id, 'fields': sorted(values)})
            return rules_payload(c)

    @app.post('/api/reminders/defaults')
    def api_defaults(request: Request, payload: dict = Body(...)):
        from entities import ENTITIES
        with db.engine.begin() as c:
            a = actor_of(request, c)
            super_admin(a)
            all_rules = load_rules(c, include_retired=False)
            chosen = defaults(c)
            for company, rid in (payload.get('defaults') or {}).items():
                if company not in ENTITIES or rid not in all_rules:
                    raise HTTPException(400, 'Choose an active rule for each company.')
                chosen[company] = rid
            _put(c, SETTING_DEFAULTS, chosen)
            db.log(c, a, 'reminders.defaults_changed', detail={'defaults': chosen})
            return rules_payload(c)

    @app.post('/api/reminders/pause')
    def api_pause_all(request: Request, payload: dict = Body(...)):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            super_admin(a)
            until = payload.get('until')
            value = 0.0 if not until else FOREVER if until == 'forever' else float(until)
            if value and value != FOREVER and value <= time.time():
                raise HTTPException(400, 'Choose a moment in the future.')
            _put(c, SETTING_PAUSE, value)
            db.log(c, a, 'reminders.paused_all' if value else 'reminders.resumed_all', detail={'until': value or None})
            return rules_payload(c)

    @app.get('/api/reminders/optouts')
    def api_optouts(request: Request, q: str = '', include_removed: int = 0):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            if a['role'] == 'user':
                raise HTTPException(403, 'Admin access required.')
            query = select(optouts)
            if not include_removed:
                query = query.where(optouts.c.removed_at.is_(None))
            if q.strip():
                like = '%' + q.strip().lower() + '%'
                query = query.where(or_(optouts.c.email.like(like), optouts.c.name.ilike(like), optouts.c.reason.ilike(like)))
            rows = [dict(r) for r in c.execute(query.order_by(optouts.c.created.desc()).limit(500)).mappings()]
            t = time.time()
            for r in rows:
                r['active'] = r['removed_at'] is None and (r['until'] is None or r['until'] > t)
            return {'optouts': rows, 'can_edit': a['role'] == 'super_admin'}

    @app.post('/api/reminders/optouts')
    def api_optout_add(request: Request, payload: dict = Body(...)):
        import mailer
        from entities import ENTITIES
        with db.engine.begin() as c:
            a = actor_of(request, c)
            super_admin(a)
            try:
                email = mailer.valid_email(str(payload.get('email') or '').strip()).lower()
            except ValueError as exc:
                fail(exc)
            entity = payload.get('entity') or None
            if entity is not None and entity not in ENTITIES:
                raise HTTPException(400, 'Choose a company, or every company.')
            reason = re.sub(r'\s+', ' ', str(payload.get('reason') or '')).strip()
            if not 3 <= len(reason) <= 500:
                raise HTTPException(400, 'Give a reason (a few words), so the team knows why.')
            until = payload.get('until')
            until = float(until) if until else None
            if until and until <= time.time():
                raise HTTPException(400, 'The end date must be in the future.')
            existing = c.execute(select(optouts.c.id).where(optouts.c.email == email, optouts.c.removed_at.is_(None),
                                                            optouts.c.entity.is_(None) if entity is None else optouts.c.entity == entity)).first()
            if existing:
                raise HTTPException(409, 'This person is already on the do-not-remind list for that choice.')
            row = dict(id=db.uid(), email=email, name=re.sub(r'\s+', ' ', str(payload.get('name') or '')).strip()[:150], entity=entity, reason=reason,
                       until=until, created=time.time(), created_by=a['id'], created_by_name=a['name'])
            c.execute(optouts.insert().values(**row))
            db.log(c, a, 'reminders.optout_added', detail={'email': email, 'entity': entity, 'reason': reason})
            return row

    @app.post('/api/reminders/optouts/{optout_id}/remove')
    def api_optout_remove(optout_id: str, request: Request):
        with db.engine.begin() as c:
            a = actor_of(request, c)
            super_admin(a)
            row = c.execute(select(optouts).where(optouts.c.id == optout_id)).mappings().first()
            if not row:
                raise HTTPException(404, 'Not found.')
            if row['removed_at'] is None:
                c.execute(optouts.update().where(optouts.c.id == optout_id).values(removed_at=time.time(), removed_by=a['id']))
                db.log(c, a, 'reminders.optout_removed', detail={'email': row['email'], 'entity': row['entity']})
            return {'ok': True}

    @app.get('/api/reminders/centre')
    def api_centre(request: Request, days: int = 7):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            return centre(c, a, time.time(), max(1, min(int(days), 60)))

    @app.get('/api/reminders/envelopes/{envelope_id}')
    def api_envelope(envelope_id: str, request: Request):
        with db.engine.connect() as c:
            a = actor_of(request, c)
            e = _envelope_for(c, a, envelope_id)
            return envelope_view(c, a, e, time.time())

    @app.post('/api/reminders/envelopes/{envelope_id}')
    def api_envelope_change(envelope_id: str, request: Request, payload: dict = Body(...)):
        import esign
        from esign_db import envelopes
        with esign.write_tx() as c:
            a = actor_of(request, c)
            e = _envelope_for(c, a, envelope_id)
            e = esign.lock(c, e['id'])
            if e['status'] != 'sent':
                raise HTTPException(409, 'Reminders can only be changed while the envelope is out for signature.')
            s = dict(e['settings'] or {})
            t = time.time()
            if 'rule' in payload:
                rid = str(payload['rule'] or '')
                all_rules = load_rules(c, include_retired=False)
                if rid and rid not in all_rules:
                    raise HTTPException(400, 'Choose a rule from the list.')
                s['reminder_rule'] = rid
                esign.record(c, e['id'], 'reminder_rule', a['name'], detail={'rule': all_rules[rid]['name'] if rid else 'Company default'})
            if 'paused_until' in payload:
                until = payload['paused_until']
                if until:
                    until = FOREVER if until == 'forever' else float(until)
                    if until <= t:
                        raise HTTPException(400, 'Choose a date in the future.')
                    s['reminders_paused_until'] = until
                    esign.record(c, e['id'], 'reminders_paused', a['name'], detail={'until': None if until == FOREVER else until})
                else:
                    s.pop('reminders_paused_until', None)
                    esign.record(c, e['id'], 'reminders_resumed', a['name'])
            c.execute(update(envelopes).where(envelopes.c.id == e['id']).values(settings=s))
            db.log(c, a, 'esign.reminders_changed', e['client_id'], {'envelope': e['ref'], 'fields': sorted(k for k in payload if k in ('rule', 'paused_until'))})
            return envelope_view(c, a, dict(e, settings=s), t)

    @app.post('/api/reminders/recipients/{recipient_id}')
    def api_person(recipient_id: str, request: Request, payload: dict = Body(...)):
        import esign
        from esign_db import recipients
        with esign.write_tx() as c:
            a = actor_of(request, c)
            row = c.execute(select(recipients.c.envelope_id).where(recipients.c.id == recipient_id)).first()
            if not row:
                raise HTTPException(404, 'Recipient not found.')
            e = _envelope_for(c, a, row[0])
            e = esign.lock(c, e['id'])
            r = _recipient(c, e, recipient_id)
            until = payload.get('until')
            until = (FOREVER if until == 'forever' else float(until)) if until else None
            try:
                set_person(c, a, e, r, str(payload.get('action') or ''), until, time.time())
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
            return envelope_view(c, a, dict(e), time.time())

    @app.post('/api/reminders/bulk')
    def api_bulk(request: Request, payload: dict = Body(...)):
        import esign
        from esign_db import recipients
        ids = [x for x in (payload.get('recipient_ids') or []) if isinstance(x, str)][:500]
        action = str(payload.get('action') or '')
        if action not in ('skip', 'pause', 'off'):
            raise HTTPException(400, 'Choose skip, pause or switch off.')
        until = payload.get('until')
        until = (FOREVER if until == 'forever' else float(until)) if until else None
        done, problems = 0, []
        for rid in ids:
            try:
                with esign.write_tx() as c:
                    a = actor_of(request, c)
                    row = c.execute(select(recipients.c.envelope_id).where(recipients.c.id == rid)).first()
                    if not row:
                        continue
                    e = esign.lock(c, _envelope_for(c, a, row[0])['id'])
                    set_person(c, a, e, _recipient(c, e, rid), action, until, time.time())
                    done += 1
            except (HTTPException, ValueError) as exc:
                problems.append(getattr(exc, 'detail', None) or str(exc))
        return {'done': done, 'problems': problems[:10]}

    @app.post('/api/reminders/mail/{outbox_id}')
    def api_mail(outbox_id: str, request: Request, payload: dict = Body(...)):
        """An email the mail server accepted but never confirmed: mark it delivered, or send it once more."""
        import esign
        import mailer
        from esign_db import outbox
        with esign.write_tx() as c:
            a = actor_of(request, c)
            m = c.execute(select(outbox).where(outbox.c.id == outbox_id)).mappings().first()
            if not m or not m['envelope_id']:
                raise HTTPException(404, 'Email not found.')
            e = _envelope_for(c, a, m['envelope_id'])
            if m['state'] != 'uncertain':
                raise HTTPException(409, 'This email no longer needs a look.')
            action = payload.get('action')
            if action == 'delivered':
                c.execute(outbox.update().where(outbox.c.id == outbox_id, outbox.c.state == 'uncertain').values(state='sent', sent_at=m['handed_at'] or time.time()))
            elif action == 'send_again':
                c.execute(outbox.update().where(outbox.c.id == outbox_id, outbox.c.state == 'uncertain')
                          .values(state='queued', next_attempt=time.time(), handed_at=None, lease_until=None))
                mailer.wake.set()
            else:
                raise HTTPException(400, 'Choose "mark as delivered" or "send once more".')
            esign.record(c, e['id'], 'mail_resolved', a['name'], recipient_id=m['recipient_id'],
                         detail={'to': ', '.join(m['to_addrs']), 'kind': m['kind'], 'resolution': action})
            db.log(c, a, 'esign.mail_resolved', e['client_id'], {'envelope': e['ref'], 'resolution': action})
            return {'ok': True}

    @app.get('/reminders/stop/{token}')
    def stop_get(token: str):
        with db.engine.connect() as c:
            r, e = _stop_context(c, token)
        return stop_page(r, e, token)

    @app.post('/reminders/stop/{token}')
    def stop_post(token: str, request: Request):
        import esign
        from esign_db import recipients
        with esign.write_tx() as c:
            r, e = _stop_context(c, token)
            if not r or not e:
                return stop_page(None, None, token)
            e = esign.lock(c, e['id'])
            if e['status'] == 'sent' and r['status'] in OPEN and not r['reminders_off']:
                c.execute(update(recipients).where(recipients.c.id == r['id']).values(reminders_off=True, reminders_off_source='signer'))
                ip = request.client.host if request.client else ''
                esign.record(c, e['id'], 'reminders_stopped', r['name'] or r['email'], recipient_id=r['id'], ip=ip,
                             ua=request.headers.get('user-agent', '')[:300], detail={'to': r['email']})
                who = r['name'] or r['email']
                tell_sender(c, dict(e), 'reminders_stopped', who,
                            f"{who} ({r['email']}) stopped reminders for this envelope from a reminder email. They can still sign with their link. "
                            'You can switch reminders back on from the envelope.')
                db.log(c, None, 'esign.reminders_stopped_by_recipient', e['client_id'], {'envelope': e['ref'], 'to': esign.mask_email(r['email'])},
                       who=who)
                return stop_page(dict(r, reminders_off=True), dict(e), token, done=True)
            return stop_page(r, dict(e), token)


def health_items(c, now_ts: float) -> list[dict]:
    """For the System check: emails the mail server accepted without confirming, and a pause of every reminder."""
    import health
    from sqlalchemy import func

    from esign_db import outbox
    out = []
    n = c.execute(select(func.count()).select_from(outbox).where(outbox.c.state == 'uncertain')).scalar() or 0
    if n:
        out.append(health.item('mail:uncertain', 'Emails to check', 'warn',
                               f'{n} email{"s" if n != 1 else ""} accepted by the mail server without a confirmation. They are not sent again by themselves: '
                               'open Agreements › Reminders › Needs a look to mark each delivered or send it once more.', '#/reminders/attention'))
    paused = float(_setting(c, SETTING_PAUSE, 0) or 0)
    if paused > now_ts:
        out.append(health.item('reminders:paused', 'Reminders', 'warn', 'All automatic reminders are paused' +
                               ('' if paused >= FOREVER else f' until {health.clock(paused)}') + '.', '#/admin/reminders'))
    return out
