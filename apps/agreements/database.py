"""Tenant-scoped persistence, immutable document bytes and durable generation queue."""
from __future__ import annotations
import contextvars, os, time, uuid
from pathlib import Path
from sqlalchemy import (create_engine, MetaData, Table, Column, String, Text, Integer,
    Float, Boolean, LargeBinary, JSON, ForeignKey, Index, UniqueConstraint, event, false, select, text)

APP=Path(__file__).resolve().parent
DATA=Path(os.getenv('TFSC_DATA_DIR',str(APP/'.data'))).resolve()
OUTPUT=Path(os.getenv('TFSC_OUTPUT_ROOT',str(APP.parent/'Client Contracts'))).resolve()
LOCAL=os.getenv('TFSC_MODE','local')=='local'
URL=os.getenv('DATABASE_URL','sqlite:///'+str(DATA/'tfsc.db'))
POOL_SIZE=max(2,int(os.getenv('DB_POOL_SIZE','20')))
meta=MetaData()
users=Table('users',meta,
    Column('id',String(32),primary_key=True),Column('email',String(200),unique=True,nullable=False),
    Column('name',String(150),nullable=False),Column('password',Text,nullable=False),
    Column('role',String(20),nullable=False),Column('admin_id',String(32),ForeignKey('users.id'),index=True),
    Column('companies',JSON,nullable=False),Column('active',Boolean,nullable=False,default=True),
    Column('reset_required',Boolean,nullable=False,default=False),Column('created',Float,nullable=False),
    Column('services',JSON,nullable=True),  # allowed service ids; NULL/empty: every service the company offers
    Column('custom_services',Boolean,nullable=True))  # a person limited to certain services may add a custom one only when this is set
sessions=Table('sessions',meta,Column('token',String(64),primary_key=True),
    Column('user_id',String(32),ForeignKey('users.id'),nullable=False,index=True),
    Column('csrf',String(64),nullable=False),Column('expires',Float,nullable=False,index=True),
    Column('created',Float,nullable=True))  # sign-in time: the idle limit slides, but never past created + the absolute limit
clients=Table('clients',meta,Column('id',String(32),primary_key=True),
    Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),
    Column('name',String(200),nullable=False),Column('name_key',String(64),nullable=False),
    Column('address',Text,nullable=False),Column('draft',JSON,nullable=False),Column('updated',Float,nullable=False),
    Column('profile',JSON,nullable=True),Column('created',Float,nullable=True),  # profile: phone, email, website, notes
    Column('test',Boolean,nullable=False,default=False,server_default=false()),  # practice client: kept out of counts and reports
    Column('client_number',String(20),nullable=True),  # permanent register number, CL-0001 onward (client_numbers.py); never reused
    # Trash: a Super Admin deleted the client; it can be restored for 30 days, then it is purged (details wiped, record kept
    # hidden so its signed agreements stay tied to it and its number stays retired). client_trash.py
    Column('deleted_at',Float,nullable=True),Column('deleted_by',String(32),nullable=True),Column('purged_at',Float,nullable=True),
    UniqueConstraint('owner_id','name_key',name='uq_client_owner_name'))
# Every client number ever given, kept for good: a number is never changed, released or given to another client. The database
# itself enforces it (triggers from migration 0011): changing a client's number, reusing one or deleting a row here is refused.
client_number_registry=Table('client_number_registry',meta,Column('number',String(20),primary_key=True),
    Column('client_id',String(32),nullable=False),Column('issued',Float,nullable=False))
contracts=Table('contracts',meta,Column('id',String(64),primary_key=True),
    Column('client_id',String(32),ForeignKey('clients.id'),nullable=False),
    Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),Column('entity',String(100),nullable=False),
    Column('created',Float,nullable=False),Column('payload',JSON,nullable=False),Column('folder',Text,nullable=False),
    Column('version',String(40),nullable=False),Column('legacy',Boolean,nullable=False,default=False),
    Column('test',Boolean,nullable=False,default=False,server_default=false()))  # test agreement: watermarked, excluded from reports
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
# Contracting companies a Super Admin adds from the app (letterhead, address, governing law, signatory, services).
# The three built-in companies stay in entities.py; this table holds only the ones added later. Letterhead and logo
# are kept in the database so backups include them. Geometry (width/top/bottom/left/right) is in twips (dxa).
companies=Table('companies',meta,Column('key',String(40),primary_key=True),
    Column('name',String(200),unique=True,nullable=False),Column('short',String(120),nullable=False),
    Column('display',String(200),nullable=False),Column('address',Text,nullable=False),Column('email',String(200),nullable=False,default=''),
    Column('governing_state',String(60),nullable=False),Column('timezone',String(60),nullable=False),
    Column('color',String(9),nullable=False),Column('accent',String(9),nullable=False),Column('dark',String(9),nullable=False),
    Column('signatory',String(150),nullable=False),Column('title',String(150),nullable=False),Column('signature_scale',Float,nullable=False,default=0.85),
    Column('letterhead',LargeBinary,nullable=False),Column('letterhead_sha256',String(64),nullable=False),
    Column('logo',LargeBinary,nullable=True),Column('logo_ext',String(8),nullable=True),
    Column('width',Integer,nullable=False),Column('top',Integer,nullable=False),Column('bottom',Integer,nullable=False),
    Column('left',Integer,nullable=False),Column('right',Integer,nullable=False),
    Column('services',JSON,nullable=False),Column('doc_types',JSON,nullable=False),Column('active',Boolean,nullable=False,default=True),
    Column('created',Float,nullable=False),Column('created_by',String(32),ForeignKey('users.id'),nullable=True),Column('updated',Float,nullable=False))
Index('ix_client_owner_updated',clients.c.owner_id,clients.c.updated,clients.c.id)
Index('ix_clients_client_number',clients.c.client_number,unique=True)
Index('ix_contract_client_created',contracts.c.client_id,contracts.c.created,contracts.c.id)
Index('ix_contract_owner_created',contracts.c.owner_id,contracts.c.created)
Index('ix_contracts_entity_created',contracts.c.entity,contracts.c.created)
Index('ix_clients_live_updated',clients.c.updated,sqlite_where=text('deleted_at IS NULL'),postgresql_where=text('deleted_at IS NULL'))
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
        # Every thread that serves a request holds one connection, so the pool is at least as large as the thread pool
        # (POOL_SIZE is also the thread limit, portal.py lifespan); the overflow is head-room for the few places that
        # use a second connection. A request that cannot get a connection within a few seconds is answered "busy, try
        # again" (portal.py) instead of hanging for half a minute.
        # The server stops any statement, lock wait or forgotten transaction after these limits, so one stuck request
        # can never hold everybody else up (0 = no limit). The background worker sets longer ones in its environment.
        options=' '.join(f'-c {name}={int(os.getenv(env,default))}' for name,env,default in (
            ('statement_timeout','DB_STATEMENT_TIMEOUT_MS','60000'),('lock_timeout','DB_LOCK_TIMEOUT_MS','10000'),
            ('idle_in_transaction_session_timeout','DB_IDLE_TX_TIMEOUT_MS','300000')))
        e=create_engine(url,pool_pre_ping=True,pool_size=POOL_SIZE,max_overflow=int(os.getenv('DB_POOL_OVERFLOW','5')),
            pool_timeout=float(os.getenv('DB_POOL_TIMEOUT','5')),pool_recycle=1800,
            # prepare_threshold None: every statement is planned for its actual values. Server-side prepared statements made
            # PostgreSQL fall back to one-size-fits-all plans that scanned whole tables for the register and the lists.
            connect_args={'options':options,'prepare_threshold':None})
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
