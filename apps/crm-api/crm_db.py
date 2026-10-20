"""CRM persistence. Business tables are separate from TFSC's agreements database."""
from __future__ import annotations
import os, time, uuid, secrets, json
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Text, Float,
    Integer, Boolean, JSON, ForeignKey, Index, UniqueConstraint, event, select, text)

APP=Path(__file__).resolve().parent
DATA=Path(os.getenv('CRM_DATA_DIR',str(APP/'.data'))).resolve()
LOCAL=os.getenv('CRM_MODE','local')=='local'
URL=os.getenv('CRM_DATABASE_URL','sqlite:///'+str(DATA/'crm.db'))
meta=MetaData()
def uid(): return uuid.uuid4().hex
def identity(): return [Column('id',String(32),primary_key=True),Column('org_id',String(32),nullable=False,index=True)]
users=Table('crm_users',meta,*identity(),Column('email',String(200),unique=True,nullable=False),
 Column('name',String(150),nullable=False),Column('password',Text,nullable=False),Column('role',String(24),nullable=False),
 Column('manager_id',String(32),ForeignKey('crm_users.id')),Column('agent_id',String(80)),Column('active',Boolean,nullable=False,default=True),
 Column('routing_view',Boolean,nullable=False,default=False),Column('domain_id',String(60),nullable=False),Column('project_id',String(32)),
 Column('agreements_user_id',String(32)),Column('created',Float,nullable=False),Column('version',Integer,nullable=False,default=1),
 Column('photo',Text),Column('reset_required',Boolean,nullable=False,default=False))
sessions=Table('crm_sessions',meta,Column('token',String(64),primary_key=True),Column('user_id',String(32),ForeignKey('crm_users.id'),nullable=False,index=True),Column('csrf',String(64),nullable=False),Column('expires',Float,nullable=False))
settings=Table('crm_settings',meta,Column('key',String(80),primary_key=True),Column('value',JSON,nullable=False))
domains=Table('crm_domains',meta,Column('id',String(60),primary_key=True),Column('name',String(150),nullable=False,unique=True),Column('active',Boolean,nullable=False,default=True))
projects=Table('crm_projects',meta,Column('id',String(32),primary_key=True),Column('name',String(150)),Column('domain_id',String(60),ForeignKey('crm_domains.id')),Column('owner_name',String(150)),Column('owner_email',String(200)),Column('active',Boolean,nullable=False,default=True))
teams=Table('crm_teams',meta,*identity(),Column('name',String(150),nullable=False),Column('supervisor_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('leader_id',String(32),ForeignKey('crm_users.id')),Column('created',Float,nullable=False))
members=Table('crm_team_members',meta,Column('team_id',String(32),ForeignKey('crm_teams.id'),primary_key=True),Column('user_id',String(32),ForeignKey('crm_users.id'),primary_key=True),Column('created',Float,nullable=False))
activities=Table('crm_activities',meta,*identity(),Column('owner_id',String(32),ForeignKey('crm_users.id'),nullable=False),
 Column('provider_name',String(200),nullable=False),Column('phone',String(100)),Column('phone_key',String(32),index=True),Column('extension',String(20)),Column('email',String(250)),Column('address',Text),
 Column('business_date',String(10),nullable=False),Column('called',Boolean),Column('connected',Boolean),Column('text_sent',Boolean),Column('email_sent',Boolean),
 Column('next_followup',String(40)),Column('remarks',Text,nullable=False),Column('raw',JSON,nullable=False),Column('source',String(30),nullable=False),
 Column('domain_id',String(60),nullable=False),Column('project_id',String(32)),Column('created',Float,nullable=False),Column('updated',Float,nullable=False),
 Column('first_committed_at',Float),Column('edit_deadline',Float),Column('version',Integer,nullable=False),Column('deleted',Boolean,nullable=False,default=False))
leads=Table('crm_leads',meta,*identity(),Column('activity_id',String(32),ForeignKey('crm_activities.id'),nullable=False,unique=True),
 Column('owner_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('stage',String(40),nullable=False),Column('version',Integer,nullable=False),
 Column('created',Float,nullable=False),Column('updated',Float,nullable=False),Column('qualified_by',String(32),ForeignKey('crm_users.id')),Column('sale_confirmed_at',Float))
claims=Table('crm_ownership_claims',meta,Column('claim_key',String(100),primary_key=True),Column('lead_id',String(32),ForeignKey('crm_leads.id'),nullable=False,unique=True),Column('owner_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('created',Float,nullable=False))
conflicts=Table('crm_conflicts',meta,*identity(),Column('activity_id',String(32),ForeignKey('crm_activities.id'),nullable=False),Column('other_id',String(32),ForeignKey('crm_activities.id'),nullable=False),Column('severity',String(30),nullable=False),Column('status',String(30),nullable=False),Column('created',Float,nullable=False),Column('reason',Text),Column('resolved_by',String(32)))
meetings=Table('crm_meetings',meta,*identity(),Column('lead_id',String(32),ForeignKey('crm_leads.id'),nullable=False),Column('owner_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('title',String(200),nullable=False),Column('starts_at',String(50),nullable=False),Column('ends_at',String(50)),Column('timezone',String(80),nullable=False),Column('join_url',Text),Column('provider_id',String(200)),Column('status',String(40),nullable=False),Column('created',Float,nullable=False))
audit=Table('crm_audit',meta,*identity(),Column('actor_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('actor_name',String(150),nullable=False),Column('owner_id',String(32),nullable=False),Column('resource_id',String(64)),Column('action',String(80),nullable=False),Column('detail',JSON,nullable=False),Column('created',Float,nullable=False))
imports=Table('crm_imports',meta,*identity(),Column('owner_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('request_key',String(80),nullable=False),Column('body_hash',String(64),nullable=False),Column('result',JSON,nullable=False),Column('created',Float,nullable=False),UniqueConstraint('owner_id','request_key',name='uq_crm_import'))
outbox=Table('crm_outbox',meta,*identity(),Column('owner_id',String(32),nullable=False),Column('event_type',String(80),nullable=False),Column('payload',JSON,nullable=False),Column('state',String(30),nullable=False),Column('attempts',Integer,nullable=False,default=0),Column('available_at',Float,nullable=False),Column('created',Float,nullable=False))
handoffs=Table('crm_handoffs',meta,*identity(),Column('lead_id',String(32),ForeignKey('crm_leads.id'),nullable=False),Column('owner_id',String(32),nullable=False),Column('key',String(80),nullable=False),Column('body_hash',String(64),nullable=False),Column('result',JSON,nullable=False),Column('created',Float,nullable=False),UniqueConstraint('owner_id','key',name='uq_crm_handoff'))
layouts=Table('crm_layouts',meta,Column('user_id',String(32),ForeignKey('crm_users.id'),primary_key=True),Column('layout',JSON,nullable=False))
throttle=Table('crm_throttle',meta,Column('key',String(64),primary_key=True),Column('count',Integer,nullable=False),Column('until',Float,nullable=False))
Index('ix_crm_activity_owner_date',activities.c.org_id,activities.c.owner_id,activities.c.business_date,activities.c.id)
Index('ix_crm_activity_followup',activities.c.owner_id,activities.c.next_followup)
Index('ix_crm_audit_owner_created',audit.c.org_id,audit.c.owner_id,audit.c.created,audit.c.id)
Index('ix_crm_lead_owner_stage',leads.c.org_id,leads.c.owner_id,leads.c.stage)
Index('ix_crm_outbox_delivery',outbox.c.state,outbox.c.available_at)

DATA.mkdir(parents=True,exist_ok=True)
if URL.startswith('sqlite:'):
 engine=create_engine(URL,connect_args={'check_same_thread':False,'timeout':30},pool_pre_ping=True,hide_parameters=True)
 @event.listens_for(engine,'connect')
 def pragmas(conn,_):
  conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA journal_mode=WAL');conn.execute('PRAGMA busy_timeout=30000')
else:engine=create_engine(URL,pool_pre_ping=True,pool_size=int(os.getenv('CRM_DB_POOL_SIZE','10')),max_overflow=5,pool_recycle=1800,hide_parameters=True)

def init_db():
 with engine.begin() as c:
  if engine.dialect.name=='postgresql':c.execute(text('SELECT pg_advisory_xact_lock(714872002)'))
  meta.create_all(c)
  if not c.execute(select(settings).where(settings.c.key=='schema_version')).first():
   config=json.loads((APP/'seed-config.json').read_text(encoding='utf-8'))
   c.execute(domains.insert(),[{'id':d['id'],'name':d['name'],'active':True} for d in config['sales_domains']])
   c.execute(projects.insert(),[{'id':p['id'],'name':p['id'],'domain_id':None,'owner_name':None,'owner_email':None,'active':True} for p in config['projects']])
   c.execute(settings.insert(),[{'key':'schema_version','value':1},{'key':'policy','value':{'import_edit_minutes':15,'call_target':170,'connection_target':0.6,'version':1}}])
  setup=DATA/'setup-key.txt'
  if not c.execute(select(users.c.id).limit(1)).first() and not setup.exists():
   try:
    with setup.open('x',encoding='utf-8') as f:f.write(secrets.token_urlsafe(24))
   except FileExistsError:pass

def log(c,a,action,resource_id=None,detail=None,owner_id=None):
 c.execute(audit.insert().values(id=uid(),org_id=a['org_id'],actor_id=a['id'],actor_name=a['name'],owner_id=owner_id or a['id'],resource_id=resource_id,action=action,detail=detail or {},created=time.time()))

def policy(c):return c.execute(select(settings.c.value).where(settings.c.key=='policy')).scalar_one()
