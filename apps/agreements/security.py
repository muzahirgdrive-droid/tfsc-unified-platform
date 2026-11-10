"""Session authentication and authorization shared by API and workers."""
import hashlib, os, secrets, time
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import select, or_, false
from fastapi import HTTPException
import database as db
from entities import ENTITIES

hasher=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
def digest(s):return hashlib.sha256(s.encode()).hexdigest()
def password_hash(password):
    if not isinstance(password,str) or not 12<=len(password)<=200:raise HTTPException(400,'Use a password of 12 to 200 characters.')
    return hasher.hash(password)
def verify(encoded,password):
    try:return hasher.verify(encoded,password)
    except (VerificationError,InvalidHashError):return False
_DUMMY=None
def verify_absent(password):
    """Spend the same time as a real check when there is no such account, so response time does not reveal which emails exist."""
    global _DUMMY
    if _DUMMY is None:_DUMMY=hasher.hash(secrets.token_urlsafe(24))
    verify(_DUMMY,password);return False
def public(user):return {k:user[k] for k in ('id','email','name','role','admin_id','companies','active','reset_required')}|{'services':user.get('services')}
def scope(actor,column):
    if actor['role']=='super_admin':return True
    if actor['role']=='admin':return column.in_(select(db.users.c.id).where(or_(db.users.c.id==actor['id'],db.users.c.admin_id==actor['id'])))
    return column==actor['id']
def company_scope(actor,column):
    return True if actor['role']=='super_admin' else column.in_(actor['companies'])
def ensure_company(actor,entity):
    if entity not in ENTITIES or (actor['role']!='super_admin' and entity not in actor['companies']):
        raise HTTPException(403,'You do not have permission for this contracting company.')
def can_manage(actor,target):
    return actor['role']=='super_admin' or (actor['role']=='admin' and target['role']=='user' and target['admin_id']==actor['id'])
# Automatic sign-out (HIPAA automatic logoff): a session ends after IDLE seconds without a request, and never lasts longer
# than ABSOLUTE seconds. The browser warns shortly before the idle limit (portal.js) and signs the person out itself.
IDLE=max(5,int(os.getenv('TFSC_IDLE_MINUTES','30')))*60
ABSOLUTE=max(1,int(os.getenv('TFSC_SESSION_HOURS','12')))*3600
def inactive_admin():
    return HTTPException(403,'Your administrator\'s account is disabled, so your access is paused. Ask a Super Admin to assign you to another administrator.')
def get_actor(request,c,allow_reset=False):
    token=request.cookies.get('tfsc_session','')
    now=time.time()
    row=c.execute(select(db.sessions,db.users).join(db.users,db.users.c.id==db.sessions.c.user_id).where(
        db.sessions.c.token==digest(token),db.sessions.c.expires>now,db.users.c.active==True)).mappings().first() if token else None
    if not row:raise HTTPException(401,'Please sign in.')
    actor=dict(c.execute(select(db.users).where(db.users.c.id==row['user_id'])).mappings().one())
    actor['_csrf']=row['csrf']
    # Each request moves the idle limit forward (at most once a minute; written by the portal after the response, because
    # many requests only read). Sessions from before this release have no start time and simply run out as before.
    started=row.get('created')
    if started and row['expires']-now<IDLE-60 and now<started+ABSOLUTE:
        request.state.touch_session=('staff',digest(token),min(now+IDLE,started+ABSOLUTE))
    actor['_session_ends']=row['expires'] if not started else min(max(row['expires'],now+IDLE),started+ABSOLUTE)
    if actor['reset_required'] and not allow_reset:raise HTTPException(403,'Change your temporary password before continuing.')
    if actor['role']=='user' and actor['admin_id']:
        parent=c.execute(select(db.users).where(db.users.c.id==actor['admin_id'])).mappings().first()
        if not parent or not parent['active']:raise inactive_admin()
        actor['companies']=[x for x in actor['companies'] if x in parent['companies']]
    if request.method not in ('GET','HEAD','OPTIONS'):
        if not secrets.compare_digest(request.headers.get('x-csrf-token',''),actor['_csrf']):raise HTTPException(403,'Reload the page before submitting this request.')
    return actor

def rate_blocked(c,key,limit=10):
    """True when this key already reached its limit in the current window (does not count the attempt)."""
    row=c.execute(select(db.throttle.c.count,db.throttle.c.until).where(db.throttle.c.key==digest(key))).first()
    return bool(row and row[1]>time.time() and row[0]>=limit)
def rate_limit(c,key,limit=10,seconds=900):
    now=time.time();key=digest(key)
    dialect=c.dialect.name
    if dialect=='postgresql':from sqlalchemy.dialects.postgresql import insert
    else:from sqlalchemy.dialects.sqlite import insert
    stmt=insert(db.throttle).values(key=key,count=0,until=now+seconds).on_conflict_do_nothing(index_elements=['key'])
    c.execute(stmt)
    from sqlalchemy import case
    row=c.execute(db.throttle.update().where(db.throttle.c.key==key).values(
        count=case((db.throttle.c.until<now,1),else_=db.throttle.c.count+1),
        until=case((db.throttle.c.until<now,now+seconds),else_=db.throttle.c.until)).returning(db.throttle.c.count)).first()
    return row[0]<=limit

def rate_limit_ok_anonymous(c,request,limit=120,seconds=60):
    """A light limit for public status endpoints (per address, per minute). Read-only connections cannot count, so the
    count is kept in memory per process: good enough to stop a loop, never a reason to refuse a monitor."""
    import threading
    now=time.time();key=str(request.client.host if request.client else '')
    with _anon_lock:
        start,count=_anon.get(key,(now,0))
        if now-start>seconds:start,count=now,0
        _anon[key]=(start,count+1)
        if len(_anon)>5000:_anon.clear()
        return count<limit
import threading as _threading
_anon={};_anon_lock=_threading.Lock()
