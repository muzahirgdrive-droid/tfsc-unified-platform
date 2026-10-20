"""Group leaders may coordinate follow-ups without editing the locked call record."""
import time
from datetime import date
from sqlalchemy import Table, Column, String, Integer, Float, Text, ForeignKey, select, or_
from fastapi import Request, HTTPException
import crm_db as db
import crm_security as sec

tasks = Table('crm_followup_tasks', db.meta,
    Column('lead_id', String(32), ForeignKey('crm_leads.id'), primary_key=True),
    Column('due_date', String(10), nullable=False), Column('notes', Text, nullable=False),
    Column('updated_by', String(32), ForeignKey('crm_users.id'), nullable=False),
    Column('version', Integer, nullable=False), Column('updated', Float, nullable=False))

def visible(c, actor):
    delegated = select(db.members.c.user_id).join(db.teams, db.teams.c.id == db.members.c.team_id).where(
        db.teams.c.org_id == actor['org_id'], db.teams.c.leader_id == actor['id'])
    return or_(sec.scope(c, actor, db.leads.c.owner_id), db.leads.c.owner_id.in_(delegated))

def install(app, clean):
    def lead(c, actor, id):
        row = c.execute(select(db.leads).where(db.leads.c.id == id, db.leads.c.org_id == actor['org_id'],
            visible(c, actor))).mappings().first()
        if not row:
            raise HTTPException(404, 'Follow-up not found.')
        return row

    @app.get('/api/leads/{id}/followup-task')
    def read(id: str, request: Request):
        with db.engine.connect() as c:
            actor = sec.actor(request, c); lead(c, actor, id)
            row = c.execute(select(tasks).where(tasks.c.lead_id == id)).mappings().first()
            return dict(row) if row else {'lead_id': id, 'version': 0, 'due_date': '', 'notes': ''}

    @app.post('/api/leads/{id}/followup-task')
    def schedule(id: str, payload: dict, request: Request):
        with db.engine.begin() as c:
            actor = sec.actor(request, c); record = lead(c, actor, id)
            if record['stage'] not in ('follow_up', 'strong_follow_up_pending', 'strong_follow_up_confirmed'):
                raise HTTPException(409, 'This record has moved beyond follow-up. Ask its supervisor to coordinate the lead.')
            due = date.fromisoformat(str(payload.get('due_date', ''))).isoformat()
            notes = clean(payload.get('notes'), 'Follow-up notes', True, 3000)
            current = c.execute(select(tasks).where(tasks.c.lead_id == id)).mappings().first()
            old_version = current['version'] if current else 0
            if payload.get('version') != old_version:
                raise HTTPException(409, 'Follow-up changed. Refresh before saving.')
            values = {'due_date': due, 'notes': notes, 'updated_by': actor['id'], 'version': old_version + 1, 'updated': time.time()}
            if current:
                if c.execute(tasks.update().where(tasks.c.lead_id == id, tasks.c.version == old_version).values(**values)).rowcount != 1:
                    raise HTTPException(409, 'Follow-up changed. Refresh before saving.')
            else:
                c.execute(tasks.insert().values(lead_id=id, **values))
            db.log(c, actor, 'followup.scheduled', id, {'due_date': due, 'notes': notes}, record['owner_id'])
            return {'lead_id': id, **values}
