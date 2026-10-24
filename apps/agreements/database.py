"""Tenant-scoped persistence, immutable document bytes and durable generation queue."""
from __future__ import annotations
import contextvars, os, time, uuid
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Text, Integer,
    Float, Boolean, LargeBinary, JSON, ForeignKey, Index, UniqueConstraint, event, select, text)

APP=Path(__file__).resolve().parent
DATA=Path(os.getenv('TFSC_DATA_DIR',str(APP/'.data'))).resolve()
OUTPUT=Path(os.getenv('TFSC_OUTPUT_ROOT',str(APP.parent/'Client Contracts'))).resolve()
LOCAL=os.getenv('TFSC_MODE','local')=='local'
URL=os.getenv('DATABASE_URL','sqlite:///'+str(DATA/'tfsc.db'))
meta=MetaData()
users=Table('users',meta,
    Column('id',String(32),primary_key=True),Column('email',String(200),unique=True,nullable=False),
    Column('name',String(150),nullable=False),Column('password',Text,nullable=False),
    Column('role',String(20),nullable=False),Column('admin_id',String(32),ForeignKey('users.id'),index=True),
    Column('companies',JSON,nullable=False),Column('active',Boolean,nullable=False,default=True),
    Column('reset_required',Boolean,nullable=False,default=False),Column('created',Float,nullable=False))
sessions=Table('sessions',meta,Column('token',String(64),primary_key=True),
    Column('user_id',String(32),ForeignKey('users.id'),nullable=False,index=True),
    Column('csrf',String(64),nullable=False),Column('expires',Float,nullable=False,index=True))
clients=Table('clients',meta,Column('id',String(32),primary_key=True),
    Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),
    Column('name',String(200),nullable=False),Column('name_key',String(64),nullable=False),
    Column('address',Text,nullable=False),Column('draft',JSON,nullable=False),Column('updated',Float,nullable=False),
    Column('profile',JSON,nullable=True),Column('created',Float,nullable=True),  # profile: phone, email, website, notes
    UniqueConstraint('owner_id','name_key',name='uq_client_owner_name'))
contracts=Table('contracts',meta,Column('id',String(64),primary_key=True),
    Column('client_id',String(32),ForeignKey('clients.id'),nullable=False),
    Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),Column('entity',String(100),nullable=False),
    Column('created',Float,nullable=False),Column('payload',JSON,nullable=False),Column('folder',Text,nullable=False),
    Column('version',String(40),nullable=False),Column('legacy',Boolean,nullable=False,default=False))
documents=Table('documents',meta,Column('id',String(32),primary_key=True),
    Column('contract_id',String(64),ForeignKey('contracts.id'),nullable=False,index=True),
    Column('name',String(200),nullable=False),Column('kind',String(20),nullable=False),
    Column('agreement',String(20),nullable=False),Column('path',Text,nullable=False),
    Column('sha256',String(64),nullable=False),Column('size',Integer,nullable=False),Column('body',LargeBinary,nullable=False))
audit=Table('audit',meta,Column('id',String(32),primary_key=True),Column('created',Float,nullable=False),
    Column('actor_id',String(32),ForeignKey('users.id'),nullable=True),Column('actor_name',String(150),nullable=False),
    Column('client_id',String(32),ForeignKey('clients.id'),nullable=True),
    Column('action',String(80),nullable=False),Column('detail',JSON,nullable=False),
    Column('ip',String(64),nullable=True),Column('user_agent',String(300),nullable=True))
jobs=Table('jobs',meta,Column('id',String(32),primary_key=True),
    Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),Column('client_id',String(32),ForeignKey('clients.id'),nullable=False),
    Column('key',String(64),nullable=False),Column('state',String(20),nullable=False),
    Column('payload',JSON,nullable=False),Column('created',Float,nullable=False),Column('updated',Float,nullable=False),
    Column('lease',String(32)),Column('lease_until',Float),Column('attempts',Integer,nullable=False,default=0),
    Column('result',JSON),Column('error',Text),UniqueConstraint('owner_id','key',name='uq_job_request'))
throttle=Table('throttle',meta,Column('key',String(64),primary_key=True),Column('count',Integer,nullable=False),Column('until',Float,nullable=False))
settings=Table('settings',meta,Column('key',String(60),primary_key=True),Column('value',JSON,nullable=False))
# The signatory, title and signature each company signs generated agreements with (Admin > Company settings).
company_profiles=Table('company_profiles',meta,Column('entity',String(100),primary_key=True),
    Column('signatory',String(150),nullable=False),Column('title',String(150),nullable=False),
    Column('signature',LargeBinary,nullable=True),Column('signature_kind',String(10),nullable=True),  # uploaded | drawn | typed
    Column('signature_scale',Float,nullable=True),  # share of the largest signature box; empty: the company default
    Column('updated',Float,nullable=False),Column('updated_by',String(32),ForeignKey('users.id'),nullable=True))
saved_reports=Table('saved_reports',meta,Column('id',String(32),primary_key=True),
    Column('owner_id',String(32),ForeignKey('users.id'),nullable=False,index=True),Column('name',String(120),nullable=False),
    Column('definition',JSON,nullable=False),Column('created',Float,nullable=False),Column('updated',Float,nullable=False))
Index('ix_client_owner_updated',clients.c.owner_id,clients.c.updated,clients.c.id)
Index('ix_contract_client_created',contracts.c.client_id,contracts.c.created,contracts.c.id)
Index('ix_contract_owner_created',contracts.c.owner_id,contracts.c.created)
Index('ix_audit_client_created',audit.c.client_id,audit.c.created,audit.c.id)
Index('ix_audit_created',audit.c.created)
Index('ix_audit_actor_created',audit.c.actor_id,audit.c.created)
Index('ix_job_queue',jobs.c.state,jobs.c.created)
Index('ix_job_owner_created',jobs.c.owner_id,jobs.c.created)

def make_engine(url=URL):
    if url.startswith('sqlite:'):
        DATA.mkdir(parents=True,exist_ok=True)
        e=create_engine(url,connect_args={'check_same_thread':False,'timeout':30},pool_pre_ping=True)
        @event.listens_for(e,'connect')
        def configure(conn,_):
            conn.execute('PRAGMA foreign_keys=ON');conn.execute('PRAGMA journal_mode=WAL');conn.execute('PRAGMA busy_timeout=30000')
    else:
        e=create_engine(url,pool_pre_ping=True,pool_size=int(os.getenv('DB_POOL_SIZE','5')),max_overflow=5,pool_recycle=1800)
    return e

engine=make_engine()
def init_db():
    import migrate
    with engine.begin() as c:
        if engine.dialect.name=='postgresql':
            c.execute(text('SELECT pg_advisory_xact_lock(714872001)'))
        else:
            # pysqlite runs DDL outside a transaction unless one is begun explicitly: this makes the upgrade all-or-nothing
            # and lets only one process migrate at a time.
            c.exec_driver_sql('BEGIN IMMEDIATE')
        migrate.upgrade_to_head(c,meta)
        if not c.execute(select(settings).where(settings.c.key=='schema_version')).first():
            c.execute(settings.insert().values(key='schema_version',value=1))

def uid():return uuid.uuid4().hex
# Client address and browser of the request being served (set by the portal middleware), recorded with each audit entry.
request_meta=contextvars.ContextVar('request_meta',default=('',''))
def log(c,actor,action,client_id=None,detail=None,who=None):
    # who: the person acting without an account (a signer), or the application itself when there is no actor.
    ip,agent=request_meta.get()
    c.execute(audit.insert().values(id=uid(),created=time.time(),actor_id=actor['id'] if actor else None,
        actor_name=actor['name'] if actor else (who or ('Legacy import' if action.startswith('legacy.') else 'TFSC Agreements'))[:150],client_id=client_id,action=action,detail=detail or {},ip=ip[:64] or None,user_agent=agent[:300] or None))
