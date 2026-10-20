"""Google Calendar + Meet adapter (§15).

Modes (CRM_GOOGLE_MODE): 'off' (default, UI shows "Not connected"), 'oauth' (organizer
accounts of Supervisor+ via OAuth 2.0 + PKCE), 'mock' (tests only; refused in hosted mode).
Refresh tokens are AES-GCM encrypted (CRM_FIELD_KEY required). Event ids are
deterministic ('tfsc' + meeting uuid hex) so retries never duplicate events; Meet links are
created with conferenceData.createRequest. Two-way sync: events.watch push channels plus an
incremental syncToken job. Nothing here ever reports success without Google's response.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx

from .. import crypto
from ..config import get_settings

API = 'https://www.googleapis.com/calendar/v3'
TOKEN_URL = 'https://oauth2.googleapis.com/token'  # noqa: S105 - public endpoint URL, not a secret
AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
SCOPE = 'https://www.googleapis.com/auth/calendar.events'


class NotConnected(Exception):
    pass


@dataclass
class RemoteEvent:
    event_id: str
    meet_url: str | None
    status: str  # confirmed | cancelled
    starts_at: datetime | None
    ends_at: datetime | None
    description: str = ''


def mode() -> str:
    m = os.getenv('CRM_GOOGLE_MODE', 'off')
    if m == 'mock' and get_settings().hosted:
        raise RuntimeError('CRM_GOOGLE_MODE=mock is not allowed in hosted mode.')
    return m


def configured() -> bool:
    if mode() == 'mock':
        return True
    return mode() == 'oauth' and bool(os.getenv('CRM_GOOGLE_CLIENT_ID') and os.getenv('CRM_GOOGLE_CLIENT_SECRET') and get_settings().field_key)


def event_id_for(meeting_id: uuid.UUID) -> str:
    return 'tfsc' + meeting_id.hex  # base32hex-safe, deterministic


class Provider(Protocol):
    def upsert(self, token: dict, meeting: dict, attendees: list[str], send_invites: bool) -> RemoteEvent: ...
    def cancel(self, token: dict, event_id: str) -> None: ...
    def changes(self, token: dict, sync_token: str | None) -> tuple[list[RemoteEvent], str | None]: ...


class MockProvider:
    """Deterministic in-memory provider for automated tests. Never used in production."""
    store: dict[str, RemoteEvent] = {}

    def upsert(self, token: dict, meeting: dict, attendees: list[str], send_invites: bool) -> RemoteEvent:
        eid = event_id_for(meeting['id'])
        ev = RemoteEvent(eid, f'https://meet.google.com/mock-{eid[4:12]}', 'confirmed', meeting['starts_at'], meeting['ends_at'])
        self.store[eid] = ev
        return ev

    def cancel(self, token: dict, event_id: str) -> None:
        if event_id in self.store:
            self.store[event_id].status = 'cancelled'

    def changes(self, token: dict, sync_token: str | None) -> tuple[list[RemoteEvent], str | None]:
        return list(self.store.values()), 'mock-sync'


class GoogleProvider:
    def _h(self, token: dict) -> dict[str, str]:
        return {'Authorization': f'Bearer {token["access_token"]}'}

    def upsert(self, token: dict, meeting: dict, attendees: list[str], send_invites: bool) -> RemoteEvent:
        eid = event_id_for(meeting['id'])
        tz = meeting.get('timezone') or 'America/Los_Angeles'
        body: dict[str, Any] = {
            'id': eid, 'summary': meeting['title'], 'visibility': 'private', 'guestsCanInviteOthers': False, 'guestsCanSeeOtherGuests': False,
            'start': {'dateTime': meeting['starts_at'].isoformat(), 'timeZone': tz}, 'end': {'dateTime': meeting['ends_at'].isoformat(), 'timeZone': tz},
            'description': meeting.get('description') or '',
            'conferenceData': {'createRequest': {'requestId': eid, 'conferenceSolutionKey': {'type': 'hangoutsMeet'}}},
        }
        if attendees:
            body['attendees'] = [{'email': e} for e in attendees]
        params = {'conferenceDataVersion': 1, 'sendUpdates': 'all' if send_invites else 'none'}
        base = f'{API}/calendars/primary/events'
        with httpx.Client(timeout=15) as h:
            r = h.put(f'{base}/{eid}', headers=self._h(token), params=params, json=body)
            if r.status_code == 404:
                r = h.post(base, headers=self._h(token), params=params, json=body)
            r.raise_for_status()
        return _remote(r.json())

    def cancel(self, token: dict, event_id: str) -> None:
        with httpx.Client(timeout=15) as h:
            r = h.delete(f'{API}/calendars/primary/events/{event_id}', headers=self._h(token), params={'sendUpdates': 'none'})
            if r.status_code not in (200, 204, 404, 410):
                r.raise_for_status()

    def changes(self, token: dict, sync_token: str | None) -> tuple[list[RemoteEvent], str | None]:
        out: list[RemoteEvent] = []
        params: dict[str, Any] = {'singleEvents': 'true', 'showDeleted': 'true', 'maxResults': 250}
        if sync_token:
            params['syncToken'] = sync_token
        else:
            params['timeMin'] = datetime.now().astimezone().isoformat()
        nxt = None
        with httpx.Client(timeout=20) as h:
            while True:
                r = h.get(f'{API}/calendars/primary/events', headers=self._h(token), params=params)
                if r.status_code == 410:  # sync token expired -> full resync
                    return self.changes(token, None)
                r.raise_for_status()
                data = r.json()
                out += [_remote(e) for e in data.get('items', [])]
                if data.get('nextPageToken'):
                    params['pageToken'] = data['nextPageToken']
                    continue
                nxt = data.get('nextSyncToken')
                break
        return out, nxt


def _dt(v: dict | None) -> datetime | None:
    if not v or not v.get('dateTime'):
        return None
    return datetime.fromisoformat(v['dateTime'].replace('Z', '+00:00'))


def _remote(e: dict) -> RemoteEvent:
    return RemoteEvent(e['id'], e.get('hangoutLink'), e.get('status', 'confirmed'), _dt(e.get('start')), _dt(e.get('end')), e.get('description') or '')


def provider() -> Provider:
    return MockProvider() if mode() == 'mock' else GoogleProvider()


# ------------------------------------------------------------------ OAuth (stateless signed state + derived PKCE verifier)
def _secret() -> bytes:
    return (get_settings().field_key or get_settings().phone_key_secret or 'dev').encode()


def oauth_start(user_id: uuid.UUID) -> str:
    if mode() != 'oauth' or not configured():
        raise NotConnected('Google Calendar is not configured on this server.')
    payload = base64.urlsafe_b64encode(json.dumps({'u': str(user_id), 'x': int(time.time()) + 600, 'n': secrets.token_urlsafe(8)}).encode()).decode()
    state = payload + '.' + hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    verifier = hmac.new(_secret(), ('pkce:' + state).encode(), hashlib.sha256).hexdigest() + 'x' * 20
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    args = {'client_id': os.environ['CRM_GOOGLE_CLIENT_ID'], 'redirect_uri': get_settings().origin + '/api/integrations/google/callback',
            'response_type': 'code', 'scope': SCOPE, 'access_type': 'offline', 'prompt': 'consent', 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256', 'include_granted_scopes': 'true'}
    return AUTH_URL + '?' + urlencode(args)


def oauth_finish(state: str, code: str, user_id: uuid.UUID) -> bytes:
    payload, _, sig = state.partition('.')
    if not hmac.compare_digest(sig, hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()):
        raise NotConnected('Google authorization did not match.')
    data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    if data['u'] != str(user_id) or data['x'] < time.time():
        raise NotConnected('Google authorization expired or belongs to another account.')
    verifier = hmac.new(_secret(), ('pkce:' + state).encode(), hashlib.sha256).hexdigest() + 'x' * 20
    r = httpx.post(TOKEN_URL, data={'client_id': os.environ['CRM_GOOGLE_CLIENT_ID'], 'client_secret': os.environ['CRM_GOOGLE_CLIENT_SECRET'],
                                    'grant_type': 'authorization_code', 'code': code, 'code_verifier': verifier,
                                    'redirect_uri': get_settings().origin + '/api/integrations/google/callback'}, timeout=15)
    r.raise_for_status()
    tok = r.json()
    tok['expires_at'] = time.time() + int(tok.get('expires_in', 3600))
    return (crypto.encrypt(json.dumps(tok)) or '').encode()


def access_token(encrypted: bytes) -> tuple[dict, bytes | None]:
    """Return a usable token (refreshing if needed) and the re-encrypted blob if it changed."""
    if mode() == 'mock':
        return {'access_token': 'mock'}, None
    tok = json.loads(crypto.decrypt(encrypted.decode()) or '{}')
    if tok.get('expires_at', 0) > time.time() + 60:
        return tok, None
    if not tok.get('refresh_token'):
        raise NotConnected('Reconnect Google to refresh organizer permission.')
    r = httpx.post(TOKEN_URL, data={'grant_type': 'refresh_token', 'refresh_token': tok['refresh_token'],
                                    'client_id': os.environ['CRM_GOOGLE_CLIENT_ID'], 'client_secret': os.environ['CRM_GOOGLE_CLIENT_SECRET']}, timeout=15)
    if r.status_code >= 400:
        raise NotConnected('Google access was revoked or expired. Reconnect the organizer account.')
    new = r.json()
    tok.update(access_token=new['access_token'], expires_at=time.time() + int(new.get('expires_in', 3600)))
    return tok, (crypto.encrypt(json.dumps(tok)) or '').encode()


def channel_token(channel_id: str) -> str:
    return hmac.new(_secret(), ('gcal-channel:' + channel_id).encode(), hashlib.sha256).hexdigest()
