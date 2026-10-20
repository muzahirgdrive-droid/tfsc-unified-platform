"""Scoped contract history synchronization. Download authorization stays in TFSC."""
import os, time, json, secrets, hashlib, hmac
from urllib.parse import urlsplit
import httpx
from sqlalchemy import Table, Column, String, Float, JSON, ForeignKey, select
from fastapi import Request, HTTPException
import crm_db as db
import crm_security as sec

history = Table('crm_contract_snapshots', db.meta,
    Column('lead_id', String(32), ForeignKey('crm_leads.id'), primary_key=True),
    Column('actor_id', String(32), ForeignKey('crm_users.id'), primary_key=True),
    Column('snapshot', JSON, nullable=False), Column('updated', Float, nullable=False))

def install(app, get_record):
    def authorize(c, request, lead_id):
        actor = sec.actor(request, c)
        sec.require(actor)
        if not actor['agreements_user_id']:
            raise HTTPException(403, 'Your account has no agreements entitlement.')
        lead = get_record(c, actor, db.leads, lead_id)
        return actor, lead

    @app.get('/api/clients/{lead_id}/agreements')
    def read(lead_id: str, request: Request):
        with db.engine.connect() as c:
            actor, _ = authorize(c, request, lead_id)
            row = c.execute(select(history).where(history.c.lead_id == lead_id,
                history.c.actor_id == actor['id'])).mappings().first()
            return {'items': row['snapshot']['items'] if row else [],
                    'updated': row['updated'] if row else None,
                    'agreements_url': os.getenv('CRM_TFSC_URL', 'http://127.0.0.1:8765') + '/reviews'}

    @app.post('/api/clients/{lead_id}/agreements/sync')
    def sync(lead_id: str, request: Request):
        secret = os.getenv('CRM_TFSC_SECRET', '')
        if len(secret) < 32:
            raise HTTPException(503, 'Configure the agreements bridge first.')
        with db.engine.connect() as c:
            actor, lead = authorize(c, request, lead_id)
        public = os.getenv('CRM_TFSC_URL', 'http://127.0.0.1:8765').rstrip('/')
        internal = os.getenv('CRM_TFSC_INTERNAL_URL', public).rstrip('/')
        path = '/api/integrations/v1/crm/status'
        raw = json.dumps({'crm_actor_id': actor['id'], 'crm_client_id': lead_id}, separators=(',', ':')).encode()
        stamp = str(int(time.time())); nonce = secrets.token_urlsafe(24)
        canonical = '\n'.join([stamp, nonce, 'POST', path, hashlib.sha256(raw).hexdigest()])
        signature = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        try:
            response = httpx.post(internal + path, content=raw, headers={
                'Host': urlsplit(public).netloc, 'Content-Type': 'application/json',
                'X-TFSC-Timestamp': stamp, 'X-TFSC-Nonce': nonce, 'X-TFSC-Signature': signature}, timeout=20)
            response.raise_for_status(); snapshot = response.json()
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, 'Agreement status is unavailable. Verify account mapping and company access in TFSC.')
        with db.engine.begin() as c:
            current, lead = authorize(c, request, lead_id)
            if current['agreements_user_id'] != actor['agreements_user_id']:
                raise HTTPException(409, 'Your agreements account mapping changed. Retry.')
            if c.dialect.name == 'postgresql':
                from sqlalchemy.dialects.postgresql import insert
            else:
                from sqlalchemy.dialects.sqlite import insert
            updated = time.time()
            c.execute(insert(history).values(lead_id=lead_id, actor_id=actor['id'], snapshot=snapshot, updated=updated)
                .on_conflict_do_update(index_elements=['lead_id', 'actor_id'], set_={'snapshot': snapshot, 'updated': updated}))
            db.log(c, current, 'agreements.status_synchronized', lead_id, {'count': len(snapshot.get('items', []))}, lead['owner_id'])
        return {**snapshot, 'updated': updated, 'agreements_url': public + '/reviews'}
