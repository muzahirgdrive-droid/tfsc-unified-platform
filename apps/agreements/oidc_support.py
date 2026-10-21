"""Optional OIDC login with explicit issuer/subject account mapping and PKCE.

No identity is provisioned or linked by matching an email address: a Super Admin links one, or the CRM sends the
person's verified (issuer, subject) when it provisions their TFSC account (crm_bridge_v2). Back-channel calls can use
an internal URL (<PREFIX>_OIDC_INTERNAL_URL) while browsers and the `iss` claim use the public issuer, as in
Docker/Kubernetes. A plain-http issuer is accepted only in local mode (trials).
"""
import os,re,time,json,secrets,hashlib,base64
from urllib.parse import urlencode,urlsplit
import httpx
from sqlalchemy import Table,Column,String,Float,ForeignKey,select,delete
from fastapi import HTTPException,Request
from fastapi.responses import RedirectResponse

accounts=None  # the oidc_accounts table once installed (used by crm_bridge_v2 to link CRM-provisioned identities)

def install(app,db,sec,session_response,origin,prefix,actor_fn):
 global accounts
 accounts=Table('oidc_accounts',db.meta,Column('issuer',String(500),primary_key=True),Column('subject',String(250),primary_key=True),Column('user_id',String(32),ForeignKey(db.users.c.id),nullable=False))
 states=Table('oidc_states',db.meta,Column('state',String(64),primary_key=True),Column('verifier',String(100),nullable=False),Column('nonce',String(100),nullable=False),Column('expires',Float,nullable=False))
 cache={}
 def enabled():return all(os.getenv(prefix+'_OIDC_'+k) for k in ('ISSUER','CLIENT_ID','CLIENT_SECRET'))
 def setting(k):return os.getenv(prefix+'_OIDC_'+k,'')
 def schemes():return ('https','http') if db.LOCAL else ('https',)
 def back(url):
  issuer,internal=setting('ISSUER').rstrip('/'),setting('INTERNAL_URL').rstrip('/')
  return internal+url[len(issuer):] if internal and url.startswith(issuer) else url
 def server_side(url):
  # token and JWKS calls come from this server: HTTPS, or the configured internal URL (Keycloak's dynamic back-channel
  # answers discovery fetched there with its own address); the browser-facing authorization endpoint must be HTTPS
  internal=setting('INTERNAL_URL').rstrip('/')
  return urlsplit(url).scheme in schemes() or bool(internal) and url.startswith(internal+'/')
 def metadata():
  issuer=setting('ISSUER').rstrip('/')
  if urlsplit(issuer).scheme not in schemes():raise HTTPException(503,'OIDC requires an HTTPS issuer.')
  if cache.get('issuer')==issuer and cache.get('expires',0)>time.time():return cache['value']
  try:
   r=httpx.get(back(issuer+'/.well-known/openid-configuration'),timeout=10);r.raise_for_status();m=r.json()
   if m.get('issuer')!=issuer or urlsplit(m.get('authorization_endpoint','')).scheme not in schemes() or not all(server_side(m.get(k,'')) for k in ('token_endpoint','jwks_uri')):raise ValueError()
   cache.update(issuer=issuer,expires=time.time()+3600,value=m);return m
  except Exception:raise HTTPException(503,'Identity provider metadata is unavailable or invalid.')
 @app.get('/api/auth/sso/status')
 def status():return {'enabled':enabled()}
 # Where to land after sign-in: a same-site path, optionally carrying a CRM handoff code (#redeem=...)
 NEXT=re.compile(r'/[A-Za-z0-9_\-/]*(#redeem=[A-Za-z0-9_-]{20,100})?')
 @app.get('/api/auth/sso/login')
 def start(request:Request):
  if not enabled():raise HTTPException(503,'Single sign-on is not configured.')
  nxt=request.query_params.get('next','/');nxt=nxt if NEXT.fullmatch(nxt) and not nxt.startswith('//') else '/'
  m=metadata();state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);nonce=secrets.token_urlsafe(32)
  with db.engine.begin() as c:
   c.execute(delete(states).where(states.c.expires<time.time()));c.execute(states.insert().values(state=sec.digest(state),verifier=verifier,nonce=nonce,expires=time.time()+600))
  args={'client_id':setting('CLIENT_ID'),'response_type':'code','scope':'openid email profile','redirect_uri':origin+'/api/auth/sso/callback','state':state,'nonce':nonce,'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),'code_challenge_method':'S256'}
  response=RedirectResponse(m['authorization_endpoint']+'?'+urlencode(args));response.set_cookie(prefix.lower()+'_oidc_state',state,httponly=True,secure=not db.LOCAL,samesite='lax',max_age=600,path='/api/auth/sso')
  response.set_cookie(prefix.lower()+'_oidc_next',nxt,httponly=True,secure=not db.LOCAL,samesite='lax',max_age=600,path='/api/auth/sso');return response
 @app.get('/api/auth/sso/callback')
 def callback(request:Request):
  if not enabled():raise HTTPException(503,'Single sign-on is not configured.')
  raw=request.query_params.get('state','');cookie=request.cookies.get(prefix.lower()+'_oidc_state','')
  if not raw or not secrets.compare_digest(raw,cookie):raise HTTPException(401,'Identity state validation failed.')
  with db.engine.begin() as c:
   row=c.execute(select(states).where(states.c.state==sec.digest(raw),states.c.expires>time.time())).mappings().first()
   if not row or c.execute(delete(states).where(states.c.state==sec.digest(raw))).rowcount!=1:raise HTTPException(401,'Identity request expired or was already used.')
  m=metadata()
  try:
   from authlib.jose import JsonWebToken
   form={'grant_type':'authorization_code','code':request.query_params.get('code',''),'redirect_uri':origin+'/api/auth/sso/callback','code_verifier':row['verifier']}
   token=httpx.post(back(m['token_endpoint']),data=form,auth=(setting('CLIENT_ID'),setting('CLIENT_SECRET')),timeout=15);token.raise_for_status()
   jwks=httpx.get(back(m['jwks_uri']),timeout=10);jwks.raise_for_status()
   claims=JsonWebToken(['RS256','ES256']).decode(token.json()['id_token'],jwks.json(),claims_options={'iss':{'essential':True,'value':m['issuer']},'aud':{'essential':True,'value':setting('CLIENT_ID')},'exp':{'essential':True},'nonce':{'essential':True,'value':row['nonce']},'sub':{'essential':True}});claims.validate(leeway=60)
   if isinstance(claims['aud'],list) and len(claims['aud'])>1 and claims.get('azp')!=setting('CLIENT_ID'):raise ValueError()
  except Exception:raise HTTPException(401,'The identity provider response could not be verified.')
  with db.engine.begin() as c:
   account=c.execute(select(accounts).where(accounts.c.issuer==m['issuer'],accounts.c.subject==claims['sub'])).mappings().first()
   user=c.execute(select(db.users).where(db.users.c.id==account['user_id'],db.users.c.active==True)).mappings().first() if account else None
   if not user:raise HTTPException(403,'This identity has not been granted access to this application. Ask an administrator to link it.')
   if user.get('reset_required'):
    # CRM-provisioned accounts start with an unknown random password to replace; signing in with SSO makes it moot
    c.execute(db.users.update().where(db.users.c.id==user['id']).values(reset_required=False));user=dict(user,reset_required=False)
   response=session_response(c,dict(user));db.log(c,user,'user.sso_signed_in')
  nxt=request.cookies.get(prefix.lower()+'_oidc_next','/');result=RedirectResponse(nxt if NEXT.fullmatch(nxt) and not nxt.startswith('//') else '/',status_code=303)
  for key,value in response.raw_headers:
   if key.lower()==b'set-cookie':result.raw_headers.append((key,value))
  result.delete_cookie(prefix.lower()+'_oidc_state',path='/api/auth/sso');result.delete_cookie(prefix.lower()+'_oidc_next',path='/api/auth/sso');return result
 @app.post('/api/auth/sso/accounts')
 def link(payload:dict,request:Request):
  with db.engine.begin() as c:
   a=actor_fn(request,c)
   if a['role']!='super_admin':raise HTTPException(403,'Super Admin access required.')
   issuer=setting('ISSUER').rstrip('/');subject=str(payload.get('subject','')).strip();id=payload.get('user_id')
   query=select(db.users.c.id).where(db.users.c.id==id)
   if 'org_id' in db.users.c:query=query.where(db.users.c.org_id==a['org_id'])
   if not issuer or not 1<=len(subject)<=250 or not c.execute(query).first():raise HTTPException(400,'Use the configured issuer, an existing user and the provider subject ID.')
   c.execute(delete(accounts).where(accounts.c.issuer==issuer,accounts.c.subject==subject));c.execute(accounts.insert().values(issuer=issuer,subject=subject,user_id=id));db.log(c,a,'identity.account_linked',detail={'user_id':id});return {'ok':True}
