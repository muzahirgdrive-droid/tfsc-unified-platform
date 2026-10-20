"""Server-enforced CRM roles, scope, field policy and session authentication."""
import time, hashlib, secrets
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from sqlalchemy import select, or_, case
from fastapi import HTTPException
import crm_db as db

ROLES={'sales_agent':0,'supervisor':1,'manager':2,'director_admin':3,'super_admin':4}
hasher=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
def digest(v):return hashlib.sha256(v.encode()).hexdigest()
def password_hash(value):
 if not isinstance(value,str) or not 12<=len(value)<=200:raise HTTPException(400,'Use a password with 12 to 200 characters.')
 return hasher.hash(value)
def verify(value,password):
 try:return hasher.verify(value,password)
 except (VerificationError,InvalidHashError):return False
def rank(a):return ROLES.get(a['role'],-1)
def require(a,level=1):
 if rank(a)<level:raise HTTPException(403,'This action requires a management permission.')
def can_routing(a):return rank(a)>=2 or (rank(a)==1 and a['routing_view'])
def scope_ids(c,a):
 # Recursive CTE, scoped at each edge. No global user list is exposed to agents.
 if a['role']=='super_admin':return select(db.users.c.id).where(db.users.c.org_id==a['org_id'])
 t=select(db.users.c.id).where(db.users.c.id==a['id'],db.users.c.org_id==a['org_id']).cte('managed_users',recursive=True)
 if rank(a)>0:
  u=db.users.alias('managed_child');t=t.union_all(select(u.c.id).join(t,u.c.manager_id==t.c.id).where(u.c.org_id==a['org_id']))
 return select(t.c.id)
def scope(c,a,column):return column.in_(scope_ids(c,a))
def allowed_user(c,a,user_id):
 return c.execute(select(db.users.c.id).where(db.users.c.id==user_id,db.users.c.org_id==a['org_id'],scope(c,a,db.users.c.id))).first() is not None
def capabilities(a):
 caps=['activities.own','research.create','followups.own']
 if rank(a)>=1:caps+=['team.view','leads.review','activities.correct','meetings.manage','dashboard.layout']
 if rank(a)>=2:caps+=['users.manage','routing.grant']
 if rank(a)>=3:caps+=['settings.manage','catalogs.manage']
 if can_routing(a):caps+=['routing.view']
 if a.get('agreements_user_id'):caps+=['agreements.open']
 return caps
def public(a,include_routing=False):
 result={k:a[k] for k in ['id','name','email','role','agent_id','active','version','reset_required']}
 result.update(capabilities=capabilities(a),can_manage_users=rank(a)>=2,can_view_routing=can_routing(a),agreements_access=bool(a.get('agreements_user_id')),has_photo=bool(a.get('photo')))
 if include_routing:result.update({k:a.get(k) for k in ['domain_id','project_id','routing_view','manager_id']})
 return result
def actor(request,c,allow_reset=False):
 token=request.cookies.get('crm_session','')
 row=c.execute(select(db.sessions).where(db.sessions.c.token==digest(token),db.sessions.c.expires>time.time())).mappings().first() if token else None
 if not row:raise HTTPException(401,'Please sign in.')
 u=c.execute(select(db.users).where(db.users.c.id==row['user_id'],db.users.c.active==True)).mappings().first()
 if not u:raise HTTPException(401,'Your account is inactive.')
 a=dict(u);a['_csrf']=row['csrf']
 if a['reset_required'] and not allow_reset:raise HTTPException(403,'Change your temporary password before continuing.')
 if request.method not in ('GET','HEAD','OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token',''),row['csrf']):raise HTTPException(403,'Reload the page before submitting this request.')
 return a
def rate_limit(c,key,limit,seconds=900):
 if c.dialect.name=='postgresql':from sqlalchemy.dialects.postgresql import insert
 else:from sqlalchemy.dialects.sqlite import insert
 key=digest(key);now=time.time()
 c.execute(insert(db.throttle).values(key=key,count=0,until=now+seconds).on_conflict_do_nothing(index_elements=['key']))
 count=c.execute(db.throttle.update().where(db.throttle.c.key==key).values(count=case((db.throttle.c.until<now,1),else_=db.throttle.c.count+1),until=case((db.throttle.c.until<now,now+seconds),else_=db.throttle.c.until)).returning(db.throttle.c.count)).scalar_one()
 return count<=limit
