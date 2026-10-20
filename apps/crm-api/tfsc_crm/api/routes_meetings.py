"""Meetings with Google Calendar + Meet (§15). Supervisor+ in scope only; never in agent DTOs."""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy import Connection, and_, delete, select, update
from sqlalchemy.dialects.postgresql import insert

from .. import crypto
from ..db import models as m
from ..db.session import read, system_tx, tx
from ..domain import audit, outbox, pipeline
from ..ids import uuid7
from ..integrations import google_calendar as gcal
from ..pacific import utcnow
from ..security.roles import SUPERVISOR, Actor, in_scope, owner_filter
from .deps import ActorDep, parse_uuid
from .schemas import Model, OkOut, Out

router = APIRouter(prefix='/api', tags=['meetings'])
log = logging.getLogger('tfsc_crm.meetings')


class MeetingIn(Model):
    title: str = Field(min_length=1, max_length=200)
    starts_at: datetime
    duration_minutes: int = Field(default=30, ge=5, le=480)
    timezone: str = Field(default='America/Los_Angeles', max_length=64)
    attendees: list[str] = Field(default_factory=list, max_length=20)
    send_invites: bool = False
    notes: str | None = Field(default=None, max_length=4000)


class MeetingPatch(Model):
    version: int
    starts_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    status: Literal['scheduled', 'held', 'no_show', 'rescheduled', 'cancelled'] | None = None
    notes: str | None = Field(default=None, max_length=4000)


class MeetingOut(Out):
    id: uuid.UUID
    lead_id: uuid.UUID
    provider_name: str | None
    organizer_id: uuid.UUID
    organizer_name: str | None
    title: str
    starts_at: datetime
    ends_at: datetime
    timezone: str
    status: str
    meet_url: str | None
    attendees: list[str]
    google_event_id: str | None
    google_state: Literal['synced', 'not_connected', 'error']
    notes: str | None
    version: int


def _require_mgmt(a: Actor) -> None:
    if a.rank < SUPERVISOR:
        raise HTTPException(403, 'You do not have permission for this action.')


def _out(r: dict) -> MeetingOut:
    return MeetingOut(id=r['id'], lead_id=r['lead_id'], provider_name=r.get('provider_name'), organizer_id=r['organizer_id'],
                      organizer_name=r.get('organizer_name'), title=r['title'], starts_at=r['starts_at'], ends_at=r['ends_at'], timezone=r['timezone'],
                      status=r['status'], meet_url=crypto.decrypt(r['meet_url']), attendees=json.loads(crypto.decrypt(r['attendees']) or '[]'),
                      google_event_id=r['google_event_id'], google_state='synced' if r['google_event_id'] else 'not_connected', notes=r['notes'],
                      version=r['version'])


def _connection(c: Connection, user_id: uuid.UUID) -> dict | None:
    r = c.execute(select(m.google_connections).where(m.google_connections.c.user_id == user_id)).mappings().first()
    return dict(r) if r else None


def _push(c: Connection, a: Actor, meeting: dict, attendees: list[str], send_invites: bool) -> dict:
    """Create/update the Google event for a meeting if the organizer is connected."""
    if not gcal.configured():
        return {}
    conn = _connection(c, a.id) if gcal.mode() == 'oauth' else {'encrypted_refresh_token': b''}
    if not conn:
        return {}
    try:
        token, refreshed = gcal.access_token(conn['encrypted_refresh_token'])
        if refreshed:
            c.execute(update(m.google_connections).where(m.google_connections.c.user_id == a.id).values(encrypted_refresh_token=refreshed))
        ev = gcal.provider().upsert(token, meeting, attendees, send_invites)
    except gcal.NotConnected as e:
        raise HTTPException(409, str(e)) from None
    except Exception as e:
        log.warning('google upsert failed: %s', e)
        raise HTTPException(502, 'Google could not save this meeting. Retry safely; nothing was duplicated.') from None
    return {'google_event_id': ev.event_id, 'meet_url': crypto.encrypt(ev.meet_url) if ev.meet_url else None}


@router.post('/leads/{lead_id}/meetings', response_model=MeetingOut)
def create_meeting(lead_id: str, body: MeetingIn, a: ActorDep) -> MeetingOut:
    a.require('meetings.view')
    lid = parse_uuid(lead_id)
    with tx(a.org_id, a.id) as c:
        ld = c.execute(select(m.leads).where(m.leads.c.id == lid).with_for_update()).mappings().first()
        if not ld or not in_scope(c, a, ld['owner_id']):
            raise HTTPException(404, 'Lead not found.')
        if ld['stage'] not in ('qualified_lead', 'meeting'):
            raise HTTPException(409, 'Meetings can be scheduled for qualified leads.')
        mid = uuid7()
        ends = body.starts_at + timedelta(minutes=body.duration_minutes)
        meeting = {'id': mid, 'title': body.title, 'starts_at': body.starts_at, 'ends_at': ends, 'timezone': body.timezone,
                   'description': f'TFSC lead {lid}. {body.notes or ""}'.strip()}
        attendees = [x.strip().lower() for x in body.attendees if x.strip()]
        g = _push(c, a, meeting, attendees, body.send_invites)
        c.execute(m.meetings.insert().values(id=mid, org_id=a.org_id, lead_id=lid, organizer_id=a.id, title=body.title, starts_at=body.starts_at,
                                             ends_at=ends, timezone=body.timezone, attendees=crypto.encrypt(json.dumps(attendees)),
                                             notes=body.notes, status='scheduled', **g))
        if ld['stage'] == 'qualified_lead':
            pipeline_meeting(c, a, ld)
        audit.record(c, a.org_id, a.id, 'meeting.created', 'meeting', mid, None, {'lead_id': str(lid), 'google': bool(g)})
        outbox.emit(c, a.org_id, 'meeting.changed', {'meeting_id': str(mid), 'lead_id': str(lid), 'change': 'created'})
        r = dict(c.execute(select(m.meetings).where(m.meetings.c.id == mid)).mappings().one())
    return _out(r)


def pipeline_meeting(c: Connection, a: Actor, ld) -> None:
    """Creating a meeting moves a qualified lead to the meeting stage (fixes pilot gap)."""
    pipeline.transition(c, a, ld['id'], 'meeting', 'Meeting scheduled', ld['version'])


@router.get('/meetings', response_model=list[MeetingOut])
def list_meetings(a: ActorDep, date_from: datetime | None = None, date_to: datetime | None = None, lead_id: str | None = None) -> list[MeetingOut]:
    _require_mgmt(a)
    mt, ld = m.meetings, m.leads
    start = date_from or (utcnow() - timedelta(days=7))
    end = date_to or (utcnow() + timedelta(days=60))
    with read(a.org_id, a.id) as c:
        stmt = select(mt, m.activities.c.provider_name, m.users.c.display_name.label('organizer_name')).join(ld, ld.c.id == mt.c.lead_id) \
            .join(m.activities, and_(m.activities.c.id == ld.c.origin_activity_id, m.activities.c.business_date == ld.c.origin_activity_date)) \
            .join(m.users, m.users.c.id == mt.c.organizer_id).where(owner_filter(a, ld.c.owner_id), mt.c.starts_at.between(start, end))
        if lead_id:
            stmt = stmt.where(mt.c.lead_id == parse_uuid(lead_id))
        rows = c.execute(stmt.order_by(mt.c.starts_at)).mappings().all()
    return [_out(dict(r)) for r in rows]


@router.patch('/meetings/{meeting_id}', response_model=MeetingOut)
def update_meeting(meeting_id: str, body: MeetingPatch, a: ActorDep) -> MeetingOut:
    _require_mgmt(a)
    with tx(a.org_id, a.id) as c:
        r = c.execute(select(m.meetings, m.leads.c.owner_id).join(m.leads, m.leads.c.id == m.meetings.c.lead_id)
                      .where(m.meetings.c.id == parse_uuid(meeting_id)).with_for_update(of=m.meetings)).mappings().first()
        if not r or not in_scope(c, a, r['owner_id']):
            raise HTTPException(404, 'Meeting not found.')
        if r['version'] != body.version:
            raise HTTPException(409, 'This meeting changed. Refresh first.')
        vals: dict = {'version': r['version'] + 1, 'updated_at': utcnow()}
        if body.starts_at:
            dur = body.duration_minutes or int((r['ends_at'] - r['starts_at']).total_seconds() // 60)
            vals.update(starts_at=body.starts_at, ends_at=body.starts_at + timedelta(minutes=dur), status='scheduled')
        if body.status:
            vals['status'] = body.status
        if body.notes is not None:
            vals['notes'] = body.notes
        merged = {**dict(r), **vals}
        if r['google_event_id'] and gcal.configured() and ('starts_at' in vals or vals.get('status') == 'cancelled'):
            conn = _connection(c, r['organizer_id']) if gcal.mode() == 'oauth' else {'encrypted_refresh_token': b''}
            if conn:
                try:
                    token, _ = gcal.access_token(conn['encrypted_refresh_token'])
                    if vals.get('status') == 'cancelled':
                        gcal.provider().cancel(token, r['google_event_id'])
                    else:
                        gcal.provider().upsert(token, merged, json.loads(crypto.decrypt(r['attendees']) or '[]'), False)
                except Exception as e:
                    raise HTTPException(502, f'Google update failed: {e}') from None
        c.execute(update(m.meetings).where(m.meetings.c.id == r['id']).values(**vals))
        audit.record(c, a.org_id, a.id, 'meeting.updated', 'meeting', r['id'], {'status': r['status']}, {k: str(v) for k, v in vals.items()})
        outbox.emit(c, a.org_id, 'meeting.changed', {'meeting_id': str(r['id']), 'lead_id': str(r['lead_id']), 'change': vals.get('status', 'updated')})
        return _out(dict(c.execute(select(m.meetings).where(m.meetings.c.id == r['id'])).mappings().one()))


# ------------------------------------------------------------------ Google connection
@router.get('/integrations/google/status')
def google_status(a: ActorDep) -> dict:
    _require_mgmt(a)
    with read(a.org_id, a.id) as c:
        connected = _connection(c, a.id) is not None or gcal.mode() == 'mock'
    return {'mode': gcal.mode(), 'configured': gcal.configured(), 'connected': connected,
            'message': None if gcal.configured() else 'Google Calendar is not connected. Meetings are recorded in the CRM without a Meet link.'}


@router.get('/integrations/google/start', include_in_schema=False)
def google_start(a: ActorDep) -> RedirectResponse:
    _require_mgmt(a)
    try:
        return RedirectResponse(gcal.oauth_start(a.id))
    except gcal.NotConnected as e:
        raise HTTPException(503, str(e)) from None


@router.get('/integrations/google/callback', include_in_schema=False)
def google_callback(a: ActorDep, state: str = '', code: str = '') -> RedirectResponse:
    _require_mgmt(a)
    try:
        blob = gcal.oauth_finish(state, code, a.id)
    except gcal.NotConnected as e:
        raise HTTPException(401, str(e)) from None
    with tx(a.org_id, a.id) as c:
        ins = insert(m.google_connections).values(user_id=a.id, org_id=a.org_id, encrypted_refresh_token=blob)
        c.execute(ins.on_conflict_do_update(index_elements=['user_id'], set_={'encrypted_refresh_token': blob, 'sync_token': None}))
        audit.record(c, a.org_id, a.id, 'google.connected', 'user', a.id)
    return RedirectResponse('/meetings?google=connected', status_code=303)


@router.post('/integrations/google/disconnect', response_model=OkOut)
def google_disconnect(a: ActorDep) -> OkOut:
    _require_mgmt(a)
    with tx(a.org_id, a.id) as c:
        c.execute(delete(m.google_connections).where(m.google_connections.c.user_id == a.id))
        audit.record(c, a.org_id, a.id, 'google.disconnected', 'user', a.id)
    return OkOut()


def sync_user(user_id: uuid.UUID) -> int:
    """Incremental sync for one organizer: Google reschedules/cancellations update the CRM."""
    with system_tx() as c:
        conn = _connection(c, user_id)
        if not conn and gcal.mode() != 'mock':
            return 0
        org = c.execute(select(m.users.c.org_id).where(m.users.c.id == user_id)).scalar_one()
    blob = conn['encrypted_refresh_token'] if conn else b''
    token, refreshed = gcal.access_token(blob)
    events, nxt = gcal.provider().changes(token, conn['sync_token'] if conn else None)
    changed = 0
    with tx(org, user_id) as c:
        if conn and (refreshed or nxt):
            c.execute(update(m.google_connections).where(m.google_connections.c.user_id == user_id).values(
                sync_token=nxt, **({'encrypted_refresh_token': refreshed} if refreshed else {})))
        for ev in events:
            row = c.execute(select(m.meetings).where(m.meetings.c.google_event_id == ev.event_id)).mappings().first()
            if row:
                vals: dict = {}
                if ev.status == 'cancelled' and row['status'] != 'cancelled':
                    vals['status'] = 'cancelled'
                if ev.starts_at and ev.starts_at != row['starts_at']:
                    vals.update(starts_at=ev.starts_at, ends_at=ev.ends_at or ev.starts_at, status='rescheduled' if row['status'] == 'scheduled' else row['status'])
                if ev.meet_url and crypto.decrypt(row['meet_url']) != ev.meet_url:
                    vals['meet_url'] = crypto.encrypt(ev.meet_url)
                if vals:
                    vals['version'] = row['version'] + 1
                    c.execute(update(m.meetings).where(m.meetings.c.id == row['id']).values(**vals))
                    outbox.emit(c, org, 'meeting.changed', {'meeting_id': str(row['id']), 'change': 'google_sync'})
                    changed += 1
            elif ev.status != 'cancelled':
                # Suggested match: a lead phone in the description (supervisor confirms; never auto-linked).
                for parts in set(re.findall(r'\(?(\d{3})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})', ev.description)):
                    key = crypto.phone_key('+1' + ''.join(parts))
                    lead = c.execute(select(m.leads.c.id).where(m.leads.c.phone_key == key, m.leads.c.stage.in_(['qualified_lead', 'meeting']))).first()
                    if lead:
                        from ..domain import notify
                        notify.notify(c, org, [user_id], 'meeting.suggested_match', {'lead_id': str(lead[0]), 'google_event_id': ev.event_id})
    return changed


@router.post('/meetings/sync', response_model=dict)
def manual_sync(a: ActorDep) -> dict:
    """Fallback manual sync (push notifications normally trigger it)."""
    _require_mgmt(a)
    if not gcal.configured():
        raise HTTPException(409, 'Google Calendar is not connected.')
    try:
        return {'changed': sync_user(a.id)}
    except gcal.NotConnected as e:
        raise HTTPException(409, str(e)) from None


@router.post('/integrations/google/notifications', include_in_schema=False)
def google_push(request: Request) -> dict:
    """Google events.watch push channel. Authenticated by the per-channel HMAC token."""
    cid = request.headers.get('x-goog-channel-id', '')
    if not cid or request.headers.get('x-goog-channel-token') != gcal.channel_token(cid):
        raise HTTPException(401, 'Unknown channel.')
    with system_tx() as c:
        uid = c.execute(select(m.google_connections.c.user_id).where(m.google_connections.c.channel_id == cid)).scalar_one_or_none()
    if uid:
        try:
            sync_user(uid)
        except Exception:
            log.exception('google push sync failed')
    return {'ok': True}


__all__ = ['pipeline']
