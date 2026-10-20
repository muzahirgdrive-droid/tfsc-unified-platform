"""Additive authenticated CRM handoff and exact-revision release approval."""
import os, time, json, hashlib, hmac, secrets, re
from pathlib import Path
from sqlalchemy import Table,Column,String,Text,Float,Integer,JSON,ForeignKey,UniqueConstraint,select,delete,text
from fastapi import Request,HTTPException
from fastapi.responses import FileResponse,RedirectResponse
import database as db
import security as sec
import server as legacy
from entities import signature_path
import crm_bridge_v2 as events

actors=Table('crm_actor_links',db.meta,Column('crm_actor_id',String(64),primary_key=True),Column('user_id',String(32),ForeignKey('users.id'),nullable=False),Column('created',Float,nullable=False))
nonces=Table('crm_bridge_nonces',db.meta,Column('nonce',String(100),primary_key=True),Column('created',Float,nullable=False))
links=Table('crm_client_links',db.meta,Column('id',String(32),primary_key=True),Column('crm_client_id',String(64),nullable=False),Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),Column('client_id',String(32),ForeignKey('clients.id'),nullable=False),UniqueConstraint('crm_client_id','owner_id',name='uq_crm_client_mapping'))
handoffs=Table('crm_bridge_handoffs',db.meta,Column('id',String(32),primary_key=True),Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),Column('request_key',String(80),nullable=False),Column('body_hash',String(64),nullable=False),Column('client_id',String(32),ForeignKey('clients.id'),nullable=False),Column('token_hash',String(64),nullable=False),Column('expires',Float,nullable=False),Column('redeemed',Float),Column('created',Float,nullable=False),UniqueConstraint('owner_id','request_key',name='uq_crm_handoff_key'))
reviews=Table('agreement_reviews',db.meta,Column('id',String(32),primary_key=True),Column('owner_id',String(32),ForeignKey('users.id'),nullable=False),Column('client_id',String(32),ForeignKey('clients.id'),nullable=False),Column('entity',String(100),nullable=False),Column('payload',JSON,nullable=False),Column('payload_hash',String(64),nullable=False),Column('assets_hash',String(64),nullable=False),Column('state',String(30),nullable=False),Column('version',Integer,nullable=False),Column('approver_id',String(32),ForeignKey('users.id')),Column('reason',Text),Column('job_id',String(32),ForeignKey('jobs.id')),Column('manifest',JSON),Column('created',Float,nullable=False),Column('updated',Float,nullable=False))

def digest(payload):return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def asset_hash(entity):
 h=hashlib.sha256()
 assets=[p for root in (db.APP/'templates',db.APP/'assets',db.APP/'deployment-fonts') for p in root.rglob('*') if p.is_file()]
 for p in sorted([*assets,db.APP/'contract_content.py',db.APP/'revised_content.py',db.APP/'document_engine.py',db.APP/'entities.py',db.APP/'signature_store.py',signature_path(db.DATA,entity),signature_path(db.DATA,entity).with_suffix('.json')],key=str):
  h.update(str(p.relative_to(db.APP)) .encode() if p.is_relative_to(db.APP) else ('signature/'+p.name).encode());h.update(p.read_bytes() if p.exists() else b'absent')
 return h.hexdigest()
def needs_review(c,client_id):return os.getenv('TFSC_REQUIRE_APPROVAL','0')=='1' or bool(c.execute(select(links.c.id).where(links.c.client_id==client_id)).first()) or bool(c.execute(select(reviews.c.id).where(reviews.c.client_id==client_id)).first())
def current_actor(c,id):
 r=c.execute(select(db.users).where(db.users.c.id==id,db.users.c.active==True)).mappings().first()
 if not r:raise HTTPException(403,'Mapped agreements account is inactive or missing.')
 a=dict(r)
 if a['admin_id']:
  p=c.execute(select(db.users).where(db.users.c.id==a['admin_id'],db.users.c.active==True)).mappings().first()
  if not p:raise HTTPException(403,'Assigned administrator is inactive.')
  a['companies']=[v for v in a['companies'] if v in p['companies']]
 return a
def require_reviewer(c,a,r):
 if a['role']=='user' or not c.execute(select(db.clients.c.id).where(db.clients.c.id==r['client_id'],sec.scope(a,db.clients.c.owner_id))).first():raise HTTPException(403,'An administrator within the client scope must review this request.')
 sec.ensure_company(a,r['entity'])
 if a['id']==r['owner_id']:raise HTTPException(403,'A separate administrator must approve this request.')
def check_approval(c,r,payload):
 if r['payload_hash']!=digest(payload) or r['assets_hash']!=asset_hash(payload['entity']):raise HTTPException(409,'The content, template or signature changed. Submit a new revision for approval.')
 approver=current_actor(c,r['approver_id']);require_reviewer(c,approver,r)
def bind_generation(c,a,payload,job_id):
 if not needs_review(c,payload['id']):return
 r=c.execute(select(reviews).where(reviews.c.client_id==payload['id'],reviews.c.owner_id==a['id'],reviews.c.state=='approved',reviews.c.payload_hash==digest(payload)).order_by(reviews.c.created.desc()).limit(1)).mappings().first()
 if not r:raise HTTPException(409,'Submit this exact draft for independent approval at /reviews before generating final documents.')
 check_approval(c,r,payload)
 if c.execute(reviews.update().where(reviews.c.id==r['id'],reviews.c.state=='approved',reviews.c.version==r['version']).values(state='queued',job_id=job_id,version=r['version']+1,updated=time.time())).rowcount!=1:raise HTTPException(409,'Approval was already used or changed.')
def check_worker(c,job):
 if not needs_review(c,job['client_id']):return
 r=c.execute(select(reviews).where(reviews.c.job_id==job['id'],reviews.c.state=='queued')).mappings().first()
 if not r:raise ValueError('Final release has no valid approval.')
 check_approval(c,r,job['payload']);a=current_actor(c,job['owner_id']);sec.ensure_company(a,job['payload']['entity'])
def finish_worker(c,job,files):
 check_worker(c,job)
 c.execute(reviews.update().where(reviews.c.job_id==job['id']).values(state='issued',manifest=[{'id':f['id'],'sha256':f['sha256'],'name':f['name']} for f in files],updated=time.time()))
 r=c.execute(select(reviews).where(reviews.c.job_id==job['id'])).mappings().first()
 if r:events.emit(c,r['client_id'],'agreement.issued','issued',dict(r),files)
def release_failed(c,job_ids):
 # A failed or abandoned generation hands the approval back (state 'approved') so the owner can retry explicitly;
 # the failure stays in the job record and the audit log. Without this the review stayed 'queued' for good.
 if job_ids:c.execute(reviews.update().where(reviews.c.job_id.in_(list(job_ids)),reviews.c.state=='queued').values(state='approved',job_id=None,version=reviews.c.version+1,updated=time.time()))
def check_download(c,document):
 # Legacy finalized documents have no review link. New reviewed artifacts must match their released manifest.
 row=c.execute(select(reviews).where(reviews.c.job_id==document['contract_id'].removeprefix('TFSC-').lower())).mappings().first()
 if row and (row['state']!='issued' or not any(x['id']==document['id'] and x['sha256']==document['sha256'] for x in row['manifest'] or [])):raise HTTPException(403,'This document has not been released.')

async def signed(request,c):
 secret=os.getenv('TFSC_CRM_SHARED_SECRET','')
 if len(secret)<32:raise HTTPException(503,'CRM bridge is not configured with a sufficiently strong shared secret.')
 stamp=request.headers.get('x-tfsc-timestamp','');nonce=request.headers.get('x-tfsc-nonce','')
 try:valid=abs(time.time()-int(stamp))<=300
 except ValueError:valid=False
 if not valid or not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',nonce):raise HTTPException(401,'Invalid bridge authentication.')
 raw=await request.body();value='\n'.join([stamp,nonce,request.method,request.url.path,hashlib.sha256(raw).hexdigest()]);expected=hmac.new(secret.encode(),value.encode(),hashlib.sha256).hexdigest()
 if not secrets.compare_digest(request.headers.get('x-tfsc-signature',''),expected):raise HTTPException(401,'Invalid bridge authentication.')
 c.execute(delete(nonces).where(nonces.c.created<time.time()-600))
 if c.execute(select(nonces).where(nonces.c.nonce==nonce)).first():raise HTTPException(409,'Bridge request was already received.')
 c.execute(nonces.insert().values(nonce=nonce,created=time.time()))
 body=json.loads(raw);link=c.execute(select(actors).where(actors.c.crm_actor_id==body.get('crm_actor_id'))).mappings().first()
 if not link:
  # v2: accounts provisioned from the CRM (crm_bridge_v2.account_links) need no manual UUID mapping
  v2=c.execute(select(events.account_links).where(events.account_links.c.crm_user_id==str(body.get('crm_actor_id')),events.account_links.c.status=='active')).mappings().first()
  link={'user_id':v2['tfsc_user_id']} if v2 else None
 if not link:raise HTTPException(403,'Map this CRM actor to an agreements account first.')
 return body,current_actor(c,link['user_id'])

def install(app):
 @app.post('/api/integrations/v1/crm/status')
 async def client_status(request:Request):
  with db.engine.begin() as c:
   body,a=await signed(request,c)
   link=c.execute(select(links).where(links.c.crm_client_id==body.get('crm_client_id'),links.c.owner_id==a['id'])).mappings().first()
   if not link:return {'items':[]}
   rows=c.execute(select(reviews).where(reviews.c.client_id==link['client_id'],reviews.c.owner_id==a['id'],sec.company_scope(a,reviews.c.entity)).order_by(reviews.c.created.desc()).limit(200)).mappings().all()
   return {'items':[{'id':r['id'],'client_id':r['client_id'],'entity':r['entity'],'state':r['state'],'version':r['version'],'created':r['created'],'updated':r['updated'],'files':[{'id':f['id'],'name':f['name'],'download_path':'/files/'+f['id'],'sha256':f['sha256']} for f in r['manifest'] or []]} for r in rows]}
 @app.post('/api/integrations/crm/actors')
 def map_actor(payload:dict,request:Request):
  with db.engine.begin() as c:
   a=sec.get_actor(request,c)
   if a['role']!='super_admin':raise HTTPException(403,'Super Admin access required.')
   crm_id=str(payload.get('crm_actor_id',''));uid=payload.get('tfsc_user_id')
   if not re.fullmatch(r'[a-f0-9]{32}',crm_id):raise HTTPException(400,'Use the CRM actor ID.')
   current_actor(c,uid);c.execute(delete(actors).where(actors.c.crm_actor_id==crm_id));c.execute(actors.insert().values(crm_actor_id=crm_id,user_id=uid,created=time.time()));db.log(c,a,'crm.actor_linked',detail={'crm_actor_id':crm_id,'tfsc_user_id':uid});return {'ok':True}
 @app.post('/api/integrations/v1/crm/handoffs')
 async def receive_handoff(request:Request):
  import portal
  with db.engine.begin() as c:
   body,a=await signed(request,c);sec.ensure_company(a,body.get('entity'));key=legacy.clean(body.get('idempotency_key'),'Request key',True,80);crm_id=legacy.clean(body.get('crm_client_id'),'CRM client ID',True,64)
   # One handoff at a time per CRM client: parallel requests (double-click, two tabs, API workers) must see each
   # other's draft, link and request key instead of racing on the unique constraints. SQLite already serializes writers.
   if c.dialect.name=='postgresql':c.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:k,0))'),{'k':'crm-handoff:'+crm_id})
   old=c.execute(select(handoffs).where(handoffs.c.owner_id==a['id'],handoffs.c.request_key==key)).mappings().first();token=secrets.token_urlsafe(32)
   if old:
    if old['body_hash']!=digest(body):raise HTTPException(409,'Request key belongs to a different handoff.')
    c.execute(handoffs.update().where(handoffs.c.id==old['id']).values(token_hash=sec.digest(token),expires=time.time()+600,redeemed=None));return {'handoff_id':old['id'],'tfsc_client_id':old['client_id'],'redeem_token':token,'redeem_url':'/reviews'}
   mapped=c.execute(select(links).where(links.c.crm_client_id==crm_id,links.c.owner_id==a['id'])).mappings().first()
   raw={**body.get('client',{}),'entity':body['entity'],'effective_date':body.get('effective_date'),'document_types':body.get('document_types',['baa']),'services':body.get('services',[]),'output_root':str(db.OUTPUT),'id':mapped['client_id'] if mapped else ''}
   draft=legacy.validate(raw,True);client_id=portal.save_draft(c,a,draft)
   if not mapped:c.execute(links.insert().values(id=db.uid(),crm_client_id=crm_id,owner_id=a['id'],client_id=client_id))
   events.emit(c,client_id,'agreement.draft_created','draft')
   hid=db.uid();c.execute(handoffs.insert().values(id=hid,owner_id=a['id'],request_key=key,body_hash=digest(body),client_id=client_id,token_hash=sec.digest(token),expires=time.time()+600,created=time.time()))
   db.log(c,a,'crm.handoff_received',client_id,{'handoff_id':hid,'crm_client_id':crm_id});return {'handoff_id':hid,'tfsc_client_id':client_id,'redeem_token':token,'redeem_url':'/reviews'}
 @app.post('/api/handoffs/redeem')
 def redeem(payload:dict,request:Request):
  with db.engine.begin() as c:
   a=sec.get_actor(request,c);token=sec.digest(str(payload.get('token','')))
   row=c.execute(select(handoffs).where(handoffs.c.owner_id==a['id'],handoffs.c.token_hash==token,handoffs.c.expires>time.time(),handoffs.c.redeemed==None)).mappings().first()
   if not row:raise HTTPException(404,'This code is expired, used, or belongs to another user.')
   if c.execute(handoffs.update().where(handoffs.c.id==row['id'],handoffs.c.redeemed==None).values(redeemed=time.time())).rowcount!=1:raise HTTPException(409,'Code already used.')
   draft=c.execute(select(db.clients.c.draft).where(db.clients.c.id==row['client_id'])).scalar_one();sec.ensure_company(a,draft['entity']);db.log(c,a,'crm.handoff_redeemed',row['client_id']);return {'client_id':row['client_id'],'client_name':draft['client_name']}
 @app.get('/api/reviews')
 def list_reviews(request:Request):
  with db.engine.connect() as c:
   a=sec.get_actor(request,c);rows=c.execute(select(reviews).where(sec.scope(a,reviews.c.owner_id),sec.company_scope(a,reviews.c.entity)).order_by(reviews.c.created.desc()).limit(100)).mappings().all()
   return {'reviews':[dict(r) for r in rows],'user':sec.public(a),'require_approval':os.getenv('TFSC_REQUIRE_APPROVAL','0')=='1'}
 @app.post('/api/reviews/submit')
 def submit(payload:dict,request:Request):
  import portal
  with db.engine.begin() as c:
   a=sec.get_actor(request,c);cid=payload.get('client_id');client=portal.client_allowed(c,a,cid)
   if client['owner_id']!=a['id']:raise HTTPException(403,'The draft owner must submit the request.')
   d=legacy.validate(client['draft']);d['id']=cid;d['output_root']=str(portal.output_path(d['output_root'],a));sec.ensure_company(a,d['entity']);now=time.time()
   for old in c.execute(select(reviews).where(reviews.c.client_id==cid,reviews.c.state.in_(['submitted','approved']))).mappings().all():events.emit(c,cid,'agreement.superseded','superseded',dict(old))
   c.execute(reviews.update().where(reviews.c.client_id==cid,reviews.c.state.in_(['submitted','approved'])).values(state='superseded',updated=now))
   r=dict(id=db.uid(),owner_id=a['id'],client_id=cid,entity=d['entity'],payload=d,payload_hash=digest(d),assets_hash=asset_hash(d['entity']),state='submitted',version=1,created=now,updated=now)
   c.execute(reviews.insert().values(**r));db.log(c,a,'agreement.review_submitted',cid,{'review_id':r['id'],'hash':r['payload_hash']});events.emit(c,cid,'agreement.submitted','submitted',r);return r
 @app.post('/api/reviews/{id}/decision')
 def decide(id:str,payload:dict,request:Request):
  with db.engine.begin() as c:
   a=sec.get_actor(request,c);r=c.execute(select(reviews).where(reviews.c.id==id)).mappings().first()
   if not r:raise HTTPException(404,'Review not found.')
   require_reviewer(c,a,r)
   if r['state']!='submitted' or r['version']!=payload.get('version'):raise HTTPException(409,'Review changed. Refresh.')
   if payload.get('decision') not in ('approve','reject'):raise HTTPException(400,'Choose approve or reject.')
   reason=legacy.clean(payload.get('reason'),'Review reason',True,2000)
   if r['assets_hash']!=asset_hash(r['entity']):raise HTTPException(409,'Template or signature changed. Submit a new revision.')
   state='approved' if payload['decision']=='approve' else 'changes_requested'
   if c.execute(reviews.update().where(reviews.c.id==id,reviews.c.version==r['version'],reviews.c.state=='submitted').values(state=state,approver_id=a['id'],reason=reason,version=r['version']+1,updated=time.time())).rowcount!=1:raise HTTPException(409,'Review changed.')
   db.log(c,a,'agreement.'+state,r['client_id'],{'review_id':id,'reason':reason});events.emit(c,r['client_id'],'agreement.'+state,state,{**dict(r),'version':r['version']+1});return {'state':state}
 @app.post('/api/reviews/{id}/generate')
 def issue(id:str,request:Request):
  import portal
  with db.engine.connect() as c:
   a=sec.get_actor(request,c);r=c.execute(select(reviews).where(reviews.c.id==id,reviews.c.owner_id==a['id'])).mappings().first()
   if not r:raise HTTPException(404,'Review not found.')
   payload=dict(r['payload'])
  # portal.generate enforces the same gate and its existing idempotency/queue limits.
  return portal.generate(payload,request)
 @app.get('/reviews')
 def review_page(request:Request):
  # Signed out (or temporary password): sign in first, then come back. The browser keeps the #redeem= fragment across
  # the redirect and login.js returns to `next` with it, so a CRM handoff link works for a first-time visitor too.
  with db.engine.connect() as c:
   try:sec.get_actor(request,c)
   except HTTPException:return RedirectResponse('/login?next=/reviews',303)
  return FileResponse(db.APP/'reviews.html')
