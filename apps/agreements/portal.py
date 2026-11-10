"""Authenticated TFSC web application. Start with launch.py or uvicorn portal:app."""
from __future__ import annotations
import base64, hashlib, json, os, secrets, time, re, string
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response, FileResponse, RedirectResponse
from sqlalchemy import select, or_, and_, func, delete, text
from sqlalchemy.exc import IntegrityError
import database as db
import security as sec
import server as legacy
import crm_bridge
import companies
import marks
import client_numbers
import client_trash
import entities as entities_mod
import static_assets
from entities import ENTITIES, get_entity

VERSION='5.1.0'
ORIGIN=os.getenv('TFSC_ORIGIN','http://127.0.0.1:8765').rstrip('/')
if not db.LOCAL and (not ORIGIN.startswith('https://') or db.URL.startswith('sqlite')):
    raise RuntimeError('VPS mode requires TFSC_ORIGIN=https://your-domain and DATABASE_URL=postgresql+psycopg://...')

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    # No more request threads than database connections: a thread without a connection would only wait for one.
    if db.engine.dialect.name=='postgresql':
        import anyio.to_thread
        anyio.to_thread.current_default_thread_limiter().total_tokens=db.POOL_SIZE
    yield
app=FastAPI(title='TFSC Compliance',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None)
import logging, uuid
import telemetry
telemetry.configure('tfsc-agreements',VERSION,not db.LOCAL,engine=db.engine,app=app)
access=logging.getLogger('tfsc.access')

@app.middleware('http')
async def boundary(request,call_next):
    from urllib.parse import urlsplit
    started=time.perf_counter();rid=uuid.uuid4().hex;telemetry.request_id.set(rid)
    db.request_meta.set((request.client.host if request.client else '',request.headers.get('user-agent','')[:300]))
    host=request.headers.get('host','')
    allowed={urlsplit(ORIGIN).netloc}
    if db.LOCAL:allowed.add('localhost:'+str(urlsplit(ORIGIN).port or 80))
    if host not in allowed:return JSONResponse({'error':'Invalid application host.'},403)
    if request.method not in ('GET','HEAD'):
        if request.headers.get('origin') not in (None,ORIGIN) or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'error':'Request origin was rejected.'},403)
        # File uploads (base64, up to 15 MB of file) are the only large bodies: documents for an envelope, files for a client
        # profile and attachments a signer adds while signing.
        limit=21_000_000 if re.fullmatch(r'/api/esign/envelopes/[a-f0-9]{32}/documents|/api/clients/[a-f0-9]{32}/files|/sign/r/[a-f0-9]{32}/api/attachment|/api/client-imports/[a-f0-9]{32}/(files|sheet)',request.url.path) else 2_800_000
        try:
            if int(request.headers.get('content-length','0'))>limit:
                return JSONResponse({'error':'Request is too large.'},413)
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>limit:return JSONResponse({'error':'Request is too large.'},413)
                chunks.append(chunk)
            request._body=b''.join(chunks)
        except Exception:return JSONResponse({'error':'Invalid request body.'},400)
    try:response=await call_next(request)
    except Exception as exc:
        response=busy_response(exc)
        if response is None:
            import logging;logging.exception('Application request failed')
            response=JSONResponse({'error':'The request could not be completed. Please try again.'},500)
    touch=getattr(request.state,'touch_session',None)
    if touch and response.status_code<500:
        import anyio.to_thread
        try:await anyio.to_thread.run_sync(extend_session,*touch)
        except Exception:logging.getLogger('tfsc.session').warning('session could not be extended',exc_info=True)
    # Routes may allow private caching (immutable e-signature page images); everything else is never cached.
    if 'cache-control' not in response.headers:response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers['Permissions-Policy']='camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()'
    if not db.LOCAL:response.headers['Strict-Transport-Security']='max-age=31536000; includeSubDomains'
    response.headers['X-Request-Id']=rid
    route=getattr(request.scope.get('route'),'path','unmatched')
    if route!='/api/health':access.info('request',extra={'method':request.method,'route':route,'status':response.status_code,'duration_ms':round((time.perf_counter()-started)*1000,1)})
    return response

def extend_session(kind,token,until):
    """Move a session's idle limit forward after a request (staff sessions and signing sessions)."""
    with db.engine.begin() as c:
        if kind=='signer':
            from esign_db import sign_sessions
            c.execute(sign_sessions.update().where(sign_sessions.c.id==token,sign_sessions.c.expires<until).values(expires=until))
        else:c.execute(db.sessions.update().where(db.sessions.c.token==token,db.sessions.c.expires<until).values(expires=until))
BUSY='The system is busy right now. Please try again in a few seconds.'
def busy_response(exc):
    """A short, honest answer when the database is saturated or stopped a statement, instead of a 30-second wait and a generic error."""
    from sqlalchemy.exc import TimeoutError as PoolTimeout, OperationalError
    if isinstance(exc,PoolTimeout):
        logging.getLogger('tfsc.capacity').warning('no database connection was free within the pool timeout')
    elif isinstance(exc,OperationalError) and type(getattr(exc,'orig',None)).__name__ in ('QueryCanceled','LockNotAvailable','IdleInTransactionSessionTimeout'):
        logging.getLogger('tfsc.capacity').warning('the database stopped a request: %s',type(exc.orig).__name__)
    else:return None
    return JSONResponse({'error':BUSY},503,headers={'Retry-After':'3'})
@app.exception_handler(IntegrityError)
async def conflict(request,exc):
    logging.getLogger('tfsc.conflict').info('conflicting write: %s',str(getattr(exc,'orig',exc))[:200])
    return JSONResponse({'error':'This was saved by another request at the same moment. Reload the page and check before trying again.'},409)
@app.exception_handler(HTTPException)
async def error(request,exc):return JSONResponse({'error':exc.detail},exc.status_code)
NOT_FOUND_PAGE="""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Page not found | TFSC Agreements</title><link rel="stylesheet" href="/styles.css"><link rel="stylesheet" href="/portal.css"><link rel="stylesheet" href="/ui.css"></head><body class="login-page"><main class="login-card"><img src="/assets/tfsc-logo.png" alt="TFSC Agreements" width="260"><h1>We couldn't find that page</h1><p>The address may be mistyped, or the page may have moved.</p><p class="notfound-actions"><a class="button button-primary" href="/">Go to Home</a> <a class="button button-quiet" href="/login">Sign in</a></p></main></body></html>"""
def not_found(request):
    """People get a page they can act on; programs (the API) get JSON."""
    if request.url.path.startswith('/api/') or 'text/html' not in request.headers.get('accept',''):return JSONResponse({'error':'Not found.'},404)
    return HTMLResponse(NOT_FOUND_PAGE,404)
from starlette.exceptions import HTTPException as StarletteHTTPException
@app.exception_handler(StarletteHTTPException)
async def http_error(request,exc):
    if exc.status_code==404 and exc.detail in ('Not Found','Not found.'):return not_found(request)
    return JSONResponse({'error':exc.detail},exc.status_code)
@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'error':str(exc)},400)

def bootstrap_available(c):return c.execute(select(db.users.c.id).limit(1)).first() is None
def session_response(c,user):
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    now=time.time()
    c.execute(db.sessions.insert().values(token=sec.digest(token),user_id=user['id'],csrf=csrf,expires=now+sec.IDLE,created=now))
    response=JSONResponse({'user':sec.public(user),'csrf':csrf,'session':{'idle_seconds':sec.IDLE,'ends':now+sec.IDLE}})
    # Lax (like the CRM): sent on top-level arrivals from the identity provider and from the CRM's "Open Agreements"
    # link; every state change still needs the X-CSRF-Token header, so cross-site requests cannot act.
    response.set_cookie('tfsc_session',token,httponly=True,secure=not db.LOCAL,samesite='lax',max_age=sec.ABSOLUTE,path='/')
    return response

@app.get('/api/health')
def health():
    with db.engine.connect() as c:c.execute(select(1))
    return {'app':'TFSC Compliance','version':VERSION,'app_path':str(db.APP) if db.LOCAL else '', 'mode':'local' if db.LOCAL else 'hosted'}
@app.get('/api/auth/status')
def auth_status():
    with db.engine.connect() as c:return {'setup_required':bootstrap_available(c)}
@app.post('/api/auth/setup')
def setup(payload:dict,request:Request):
    # A local setup key is required even for the very first account.
    keypath=db.DATA/'setup-key.txt'
    with db.engine.begin() as c:
        valid=sec.rate_limit(c,'setup:'+str(request.client.host),5)
    if not valid:raise HTTPException(429,'Too many setup attempts. Try again later.')
    if not keypath.is_file() or not secrets.compare_digest(payload.get('setup_key',''),keypath.read_text().strip()):raise HTTPException(403,'Enter the setup key from the application folder.')
    email=valid_email(payload.get('email'));name=legacy.clean(payload.get('name'),'Name',True,150)
    password=sec.password_hash(payload.get('password'))
    try:
        with db.engine.begin() as c:
            # A unique setting makes first-account creation atomic across processes.
            if not bootstrap_available(c):raise HTTPException(409,'The workspace is already configured.')
            c.execute(db.settings.insert().values(key='bootstrap_complete',value=True))
            user=dict(id=db.uid(),email=email,name=name,password=password,role='super_admin',admin_id=None,
                companies=list(ENTITIES),active=True,reset_required=False,created=time.time())
            c.execute(db.users.insert().values(**user));db.log(c,user,'workspace.initialized')
            response=session_response(c,user)
    except IntegrityError:raise HTTPException(409,'The workspace is already configured.')
    keypath.unlink(missing_ok=True)
    return response

def valid_email(value):
    value=legacy.clean(value,'Email',True,200).casefold()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',value):raise HTTPException(400,'Enter a valid email address.')
    return value

# Sign-in limits count wrong passwords only. A whole office shares one internet address, so correct sign-ins never count
# against it; a password-guesser is still stopped per account (10 wrong tries) and per address (300 wrong tries).
LOGIN_IP_FAILURES=int(os.getenv('TFSC_LOGIN_IP_FAILURES','300'))
@app.post('/api/auth/login')
def login(payload:dict,request:Request):
    email=str(payload.get('email','')).strip().casefold()[:200];ip='login-ip:'+str(request.client.host);password=str(payload.get('password',''))[:201]
    with db.engine.connect() as c:
        blocked=sec.rate_blocked(c,ip,LOGIN_IP_FAILURES) or sec.rate_blocked(c,'login:'+email,10)
        user=None if blocked else c.execute(select(db.users).where(db.users.c.email==email,db.users.c.active==True)).mappings().first()
    if blocked:raise HTTPException(429,'Too many wrong sign-in attempts. Try again in 15 minutes.')
    if not (sec.verify(user['password'],password) if user else sec.verify_absent(password)):
        with db.engine.begin() as c:sec.rate_limit(c,ip,LOGIN_IP_FAILURES);sec.rate_limit(c,'login:'+email,10)
        raise HTTPException(401,'Email or password is incorrect.')
    with db.engine.begin() as c:
        if user['role']=='user' and user['admin_id']:
            parent=c.execute(select(db.users.c.active).where(db.users.c.id==user['admin_id'])).scalar()
            if not parent:raise sec.inactive_admin()
        db.log(c,user,'user.signed_in')
        c.execute(delete(db.throttle).where(db.throttle.c.key==sec.digest('login:'+email)))
        return session_response(c,user)

@app.get('/api/auth/me')
def me(request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c,True);return {'user':sec.public(a),'csrf':a['_csrf'],'session':{'idle_seconds':sec.IDLE,'ends':a['_session_ends']}}
@app.post('/api/auth/logout')
def logout(request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c,True);db.log(c,a,'user.signed_out')
        c.execute(delete(db.sessions).where(db.sessions.c.token==sec.digest(request.cookies['tfsc_session'])))
    r=JSONResponse({'ok':True});r.delete_cookie('tfsc_session');return r
@app.post('/api/auth/password')
def change_password(payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c,True)
        if not sec.verify(a['password'],str(payload.get('current_password',''))):raise HTTPException(400,'Current password is incorrect.')
        encoded=sec.password_hash(payload.get('password'))
        c.execute(db.users.update().where(db.users.c.id==a['id']).values(password=encoded,reset_required=False))
        c.execute(delete(db.sessions).where(db.sessions.c.user_id==a['id']))
        a['reset_required']=False;db.log(c,a,'user.password_changed');return session_response(c,a)

@app.get('/')
def home(request:Request):
    with db.engine.connect() as c:
        try:a=sec.get_actor(request,c)
        except HTTPException:return RedirectResponse('/login')
    return HTMLResponse(static_assets.page('index.html').replace('__CSRF_TOKEN__',a['_csrf']))
@app.get('/login')
def login_page():return HTMLResponse(static_assets.page('login.html'))
@app.get('/favicon.ico')
def favicon():return Response(status_code=204)

@app.get('/api/config')
def config(request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);people=companies.signatories(c);workspace=companies.public_workspace(c)
    # A user may be limited to a subset of services (Admin > Users). Empty / absent means every service is allowed.
    allowed=a.get('services') or None
    def permit(services):
        return services if not allowed else [s for s in services if s.get('id') in allowed]
    def entity_public(e):
        out={k:e[k] for k in ('name','short','key','address','email','governing_state','color','accent','dark')}
        out['logo']=e['logo_url']; out['custom']=e.get('custom',False); out['doc_types']=e.get('doc_types') or ['baa','services','nda']
        # Custom companies carry their own services; built-ins use the standard catalogue. Both are filtered to the user's allowed set.
        out['services']=permit(e['services']) if e.get('services') else None
        out.update(signatory=people[e['name']]['signatory'],title=people[e['name']]['title'],signature_available=people[e['name']]['source'] is not None)
        return out
    entities=[entity_public(e) for e in ENTITIES.values() if a['role']=='super_admin' or e['name'] in a['companies']]
    return dict(catalog=permit(legacy.CATALOG),defaults=legacy.DEFAULTS,states=sorted(legacy.STATES),entities=entities,output_root=str(db.OUTPUT),version=VERSION,
        user=sec.public(a),local=db.LOCAL,can_manage_signatures=a['role']=='super_admin',crm=bool(os.getenv('TFSC_CRM_URL')),workspace=workspace)

def client_allowed(c,a,id):
    row=c.execute(select(db.clients).where(db.clients.c.id==id,sec.scope(a,db.clients.c.owner_id))).mappings().first()
    if not row:raise HTTPException(404,'Client not found.')
    client_trash.refuse_if_trashed(row)
    return row
def save_draft(c,a,data,generating=False):
    # Test agreements: a client first saved by a test run is a test client; a real generation makes it a real client.
    if data.get('id'):
        row=client_allowed(c,a,data['id']);id=row['id'];owner=row['owner_id']
    else:
        # a client the person can already see (their team's included) is reused, never created a second time
        found=clients.visible_client(c,a,data['client_name'])
        row=c.execute(select(db.clients).where(db.clients.c.id==found[0])).mappings().first() if found else None
        id=row['id'] if row else db.uid();owner=row['owner_id'] if row else a['id']
    data=dict(data,id=id)
    values=dict(name=data['client_name'],name_key=sec.digest(data['client_name'].strip().casefold()),address=data['address'],draft=data,updated=time.time())
    if row:c.execute(db.clients.update().where(db.clients.c.id==id).values(**values))
    else:c.execute(db.clients.insert().values(id=id,owner_id=owner,test=data.get('test') is True,**values));client_numbers.assign_one(c,id)
    db.log(c,a,'client.updated' if row else 'client.created',id,{'entity':data['entity']}|({'test':True} if not row and data.get('test') is True else {}))
    if row and generating and data.get('test') is not True:marks.real_activity(c,a,id,'a real agreement was generated')
    return id

@app.get('/api/clients')
def list_clients(request:Request,q:str='',page:int=0):
    page=max(0,min(page,100000))
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);condition=and_(sec.scope(a,db.clients.c.owner_id),client_trash.live())
        if client_numbers.missing(c):client_numbers.ensure_before_reading()
        # a client number ("CL-0012", "cl-12", "12") finds that client as well as names containing the text
        if q:condition=and_(condition,or_(func.lower(db.clients.c.name).contains(q[:200].lower(),autoescape=True),db.clients.c.client_number.in_(client_numbers.search_terms(q))))
        rows=c.execute(select(db.clients.c.id,db.clients.c.name,db.clients.c.address,db.clients.c.updated,db.clients.c.test,db.clients.c.client_number,db.users.c.name.label('owner')).join(db.users,db.users.c.id==db.clients.c.owner_id).where(condition).order_by(db.clients.c.updated.desc(),db.clients.c.id).limit(51).offset(page*50)).mappings().all()
        return {'clients':[dict(id=r['id'],client_name=r['name'],client_number=r['client_number'] or '',address=r['address'],updated_at=r['updated'],owner=r['owner'],test=bool(r['test'])) for r in rows[:50]],'has_more':len(rows)>50,'page':page}
@app.get('/api/clients/{id}')
def get_client(id:str,request:Request,entity:str=''):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);r=client_allowed(c,a,id);d=dict(r['draft'])
        # entity: open the client for another company the person may use ("Send a BAA" from the Signed BAA register)
        if entity:sec.ensure_company(a,entity);d['entity']=entity
        sec.ensure_company(a,d.get('entity'));d.setdefault('client_name',r['name']);d.setdefault('address',r['address']);d['id']=r['id'];return d
@app.post('/api/clients')
def put_client(payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c);d=legacy.validate(payload,True);sec.ensure_company(a,d['entity'])
        try:return {'id':save_draft(c,a,d)}
        except IntegrityError:raise HTTPException(409,'This client already exists. Open their saved record.')

@app.get('/api/clients/{id}/history')
def history(id:str,request:Request,page:int=0):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);client=client_allowed(c,a,id)
        rows=c.execute(select(db.contracts,db.users.c.name.label('owner')).join(db.users,db.users.c.id==db.contracts.c.owner_id).where(db.contracts.c.client_id==id,sec.scope(a,db.contracts.c.owner_id),sec.company_scope(a,db.contracts.c.entity)).order_by(db.contracts.c.created.desc(),db.contracts.c.id).limit(21).offset(max(0,page)*20)).mappings().all()
        result=[]
        for r in rows[:20]:
            docs=c.execute(select(db.documents.c.id,db.documents.c.name,db.documents.c.kind,db.documents.c.agreement,db.documents.c.sha256).where(db.documents.c.contract_id==r['id'])).mappings().all()
            from esign_db import envelopes as env_table
            sent=c.execute(select(env_table.c.id,env_table.c.ref,env_table.c.status,env_table.c.test).where(env_table.c.contract_id==r['id'],env_table.c.status!='template').order_by(env_table.c.created)).mappings().all()
            result.append(dict(id=r['id'],entity=r['entity'],created=r['created'],owner='Legacy record (author unavailable)' if r['legacy'] else r['owner'],files=[dict(x,url='/files/'+x['id']) for x in docs],envelopes=[dict(x) for x in sent],test=bool(r['test'])))
        return {'client':dict(id=client['id'],name=client['name'],address=client['address'],test=bool(client['test'])),'contracts':result,'has_more':len(rows)>20}

@app.post('/api/contracts/mark-test')
def mark_contracts_test(payload:dict,request:Request):
    """Mark generated agreements as test (test: true) or real (test: false): {"ids": [...], "test": bool}. Their envelopes follow."""
    ids,flag=marks.ids_of(payload),marks.flag_of(payload)
    with db.engine.begin() as c:
        if c.dialect.name=='sqlite':c.execute(text('BEGIN IMMEDIATE'))
        a=sec.get_actor(request,c)
        return {'ok':True,'changed':marks.mark_contracts(c,a,ids,flag),'test':flag}

@app.get('/files/{id}')
def download(id:str,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c)
        row=c.execute(select(db.documents,db.contracts.c.client_id).join(db.contracts).where(db.documents.c.id==id,sec.scope(a,db.contracts.c.owner_id),sec.company_scope(a,db.contracts.c.entity))).mappings().first()
        if not row:raise HTTPException(404,'Document not found.')
        crm_bridge.check_download(c,row)
        db.log(c,a,'document.downloaded',row['client_id'],{'document':row['name'],'sha256':row['sha256']})
        body=bytes(row['body']);name=row['name'];kind=row['kind']
    return Response(body,media_type='application/pdf' if kind=='pdf' else 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',headers={'Content-Disposition':'attachment; filename="'+name.replace('"','')+'"'})

def check_services(a,d):
    """A person limited to certain services cannot put others on an agreement: not from the catalogue, and not by typing a
    custom service (unless they were given that permission). The same rule applies to the preview and to generation."""
    if not a.get('services'):return
    blocked=[s.get('name') or s.get('id') for s in d.get('services',[]) if s.get('id') not in a['services']]
    if blocked:raise HTTPException(403,'You are not permitted to offer: '+', '.join(blocked)+'.')
    if d.get('custom_services') and not a.get('custom_services'):
        raise HTTPException(403,'You are not permitted to add custom services. Ask your administrator to allow it under Users & permissions.')
@app.post('/api/preview')
def preview(payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c);d=legacy.validate(payload);sec.ensure_company(a,d['entity']);check_services(a,d)
        if d['id']:client_allowed(c,a,d['id']);db.log(c,a,'contract.previewed',d['id'])
    with db.engine.connect() as c:company=companies.profile(c,d['entity'],with_image=False)
    docs=legacy.build_documents(d,'TFSC-PREVIEW',company)
    return {'documents':[{k:v for k,v in x.items() if k!='data'} for x in docs]}

def output_path(raw,a):
    root=Path(raw).expanduser()
    if not root.is_absolute():raise HTTPException(400,'Enter an absolute folder path.')
    root=root.resolve()
    if not db.LOCAL and not root.is_relative_to(db.OUTPUT):raise HTTPException(403,'Choose a folder inside the configured contract storage.')
    return root

@app.post('/api/generate')
def generate(payload:dict,request:Request):
    if db.LOCAL and (db.DATA/'stop-request.json').exists():raise HTTPException(503,'The application is stopping. Start TFSC Compliance again before generating.')
    key=request.headers.get('idempotency-key','')
    if not re.fullmatch('[a-zA-Z0-9-]{16,64}',key):raise HTTPException(400,'Generation request is missing its unique identifier. Reload the page.')
    try:
        with db.engine.begin() as c:
            # Serialize enqueue decisions per account. SQLite has one writer;
            # acquiring it before reading also avoids a read-to-write upgrade race.
            if c.dialect.name=='sqlite':c.execute(text('BEGIN IMMEDIATE'))
            a=sec.get_actor(request,c)
            if c.dialect.name=='postgresql':
                c.execute(select(db.users.c.id).where(db.users.c.id==a['id']).with_for_update()).first()
            d=legacy.validate(payload);sec.ensure_company(a,d['entity'])
            signer=companies.profile(c,d['entity'],with_image=False)
            if not (signer['signatory'] or '').strip() or not (signer['title'] or '').strip():
                entities_mod.invalidate();signer=companies.profile(c,d['entity'],with_image=False)   # set a moment ago in another process
            if not (signer['signatory'] or '').strip() or not (signer['title'] or '').strip():
                raise HTTPException(400,f"Add who signs for {get_entity(d['entity'])['short']} (name and title) in Admin › Company settings before generating agreements.")
            check_services(a,d)
            d['output_root']=str(output_path(d['output_root'],a))
            old=c.execute(select(db.jobs).where(db.jobs.c.owner_id==a['id'],db.jobs.c.key==key)).mappings().first()
            if old:return {'job_id':old['id'],'state':old['state']}
            pending=c.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.owner_id==a['id'],db.jobs.c.state.in_(['queued','running']))).scalar()
            if pending>=5:raise HTTPException(429,'You already have five pending generations. Wait for one to finish.')
            id=save_draft(c,a,d,generating=True);d['id']=id
            jobid=db.uid();now=time.time()
            c.execute(db.jobs.insert().values(id=jobid,owner_id=a['id'],client_id=id,key=key,state='queued',payload=d,created=now,updated=now,attempts=0))
            crm_bridge.bind_generation(c,a,d,jobid)
            db.log(c,a,'contract.queued',id,{'job_id':jobid,'entity':d['entity']})
    except IntegrityError:
        # Another request may have saved the same client or request first.
        # The failed transaction must be rolled back before looking up its result.
        with db.engine.connect() as c:
            a=sec.get_actor(request,c)
            old=c.execute(select(db.jobs).where(db.jobs.c.owner_id==a['id'],db.jobs.c.key==key)).mappings().first()
            if old:return {'job_id':old['id'],'state':old['state']}
        raise HTTPException(409,'This client was saved by another request. Open the saved client and generate again.')
    return JSONResponse({'job_id':jobid,'state':'queued'},202)
@app.get('/api/jobs')
def list_jobs(request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c)
        company=True if a['role']=='super_admin' else db.jobs.c.payload['entity'].as_string().in_(a['companies'])
        rows=c.execute(select(db.jobs.c.id,db.jobs.c.state,db.jobs.c.created,db.jobs.c.client_id,db.jobs.c.error).where(sec.scope(a,db.jobs.c.owner_id),company).order_by(db.jobs.c.created.desc()).limit(30)).mappings().all()
        return {'jobs':[dict(r) for r in rows]}
@app.get('/api/jobs/{id}')
def get_job(id:str,request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);row=c.execute(select(db.jobs).where(db.jobs.c.id==id,sec.scope(a,db.jobs.c.owner_id))).mappings().first()
        if not row:raise HTTPException(404,'Generation not found.')
        sec.ensure_company(a,row['payload']['entity'])
        # how many generations are ahead in the queue (shown to the person waiting)
        ahead=c.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.state=='queued',db.jobs.c.created<row['created'])).scalar() if row['state']=='queued' else 0
        return {'id':id,'state':row['state'],'result':row['result'],'error':row['error'],'ahead':ahead}

@app.post('/api/folders')
def folders(payload:dict,request:Request):
    with db.engine.connect() as c:a=sec.get_actor(request,c)
    raw=payload.get('path')
    if not raw:
        roots=[str(db.OUTPUT)]
        if db.LOCAL:
            roots += [str(Path.home()),str(Path.home()/'Documents'),str(Path.home()/'Desktop')]
            if os.name=='nt':
                import ctypes
                mask=ctypes.windll.kernel32.GetLogicalDrives()
                roots += [letter+':\\' for i,letter in enumerate(string.ascii_uppercase) if mask & (1<<i)]
        return {'roots':list(dict.fromkeys(roots)),'entries':[],'path':''}
    root=output_path(str(raw),a)
    if not db.LOCAL:
        # Hosted: agreements are filed per client automatically and downloaded from the portal. The storage folder names are
        # client names of every company, so they are never listed.
        return {'path':str(db.OUTPUT),'parent':'','entries':[],'truncated':False}
    try:
        # scandir avoids stat calls on files; bounded responses keep large directories usable.
        entries=[]
        with os.scandir(root) as scan:
            for entry in scan:
                try:
                    if entry.is_dir(follow_symlinks=False):entries.append({'name':entry.name,'path':str(root/entry.name)})
                except OSError:continue
                if len(entries)>=300:break
        return {'path':str(root),'parent':str(root.parent) if db.LOCAL or root.parent.is_relative_to(db.OUTPUT) else '', 'entries':sorted(entries,key=lambda r:r['name'].casefold()),'truncated':len(entries)>=300}
    except OSError:raise HTTPException(400,'This folder is unavailable or cannot be read. Enter another path.')

@app.get('/api/users')
def list_users(request:Request,page:int=0,q:str=''):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c)
        if a['role']=='user':raise HTTPException(403,'Administrator access required.')
        cond=True if a['role']=='super_admin' else or_(db.users.c.id==a['id'],db.users.c.admin_id==a['id'])
        if q:cond=and_(cond,or_(db.users.c.email.contains(q[:200],autoescape=True),db.users.c.name.contains(q[:150],autoescape=True)))
        rows=c.execute(select(db.users).where(cond).order_by(db.users.c.created.desc(),db.users.c.id).limit(51).offset(max(0,page)*50)).mappings().all()
        return {'users':[sec.public(x) for x in rows[:50]],'has_more':len(rows)>50}
@app.get('/api/admins')
def list_admins(request:Request,page:int=0):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c)
        if a['role']!='super_admin':raise HTTPException(403,'Super Admin access required.')
        rows=c.execute(select(db.users.c.id,db.users.c.name,db.users.c.email).where(db.users.c.role=='admin',db.users.c.active==True).order_by(db.users.c.name,db.users.c.id).limit(101).offset(max(0,page)*100)).mappings().all()
        return {'users':[dict(r) for r in rows[:100]],'has_more':len(rows)>100}

@app.post('/api/users')
def create_user(payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c)
        if a['role']=='user':raise HTTPException(403,'Administrator access required.')
        role=payload.get('role','user')
        if role not in ('user','admin','super_admin') or (a['role']=='admin' and role!='user'):raise HTTPException(403,'You cannot create that role.')
        companies=payload.get('companies',[])
        if not isinstance(companies,list) or not companies:raise HTTPException(400,'Select at least one contracting company.')
        for company in companies:sec.ensure_company(a,company)
        admin_id=a['id'] if a['role']=='admin' else payload.get('admin_id') or None
        if role=='user' and admin_id:
            manager=c.execute(select(db.users).where(db.users.c.id==admin_id,db.users.c.role=='admin',db.users.c.active==True)).mappings().first()
            if not manager or not set(companies)<=set(manager['companies']):raise HTTPException(400,'Select an active administrator with the requested company permissions.')
        if role!='user':admin_id=None
        password=secrets.token_urlsafe(15)
        import company_admin
        services=company_admin.clean_user_services(payload.get('services')) if 'services' in payload else None
        row=dict(id=db.uid(),email=valid_email(payload.get('email')),name=legacy.clean(payload.get('name'),'Name',True,150,typography=False),password=sec.password_hash(password),role=role,admin_id=admin_id,companies=list(dict.fromkeys(companies)),active=True,reset_required=True,created=time.time(),services=services,custom_services=payload.get('custom_services') is True)
        try:c.execute(db.users.insert().values(**row))
        except IntegrityError:raise HTTPException(409,'An account with this email already exists.')
        db.log(c,a,'user.created',detail={'user_id':row['id'],'role':role,'companies':companies,'admin_id':admin_id})
        return {'user':sec.public(row),'temporary_password':password}
@app.post('/api/users/{id}')
def update_user(id:str,payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c);target=c.execute(select(db.users).where(db.users.c.id==id)).mappings().first()
        if not target or not sec.can_manage(a,target):raise HTTPException(404,'User not found.')
        if id==a['id']:raise HTTPException(400,'Use your account settings to change your password. You cannot disable yourself.')
        values={};temporary=None
        if 'companies' in payload:
            companies=payload['companies']
            if not isinstance(companies,list):raise HTTPException(400,'Invalid company permissions.')
            for company in companies:sec.ensure_company(a,company)
            if target['role']=='user' and target['admin_id']:
                manager=c.execute(select(db.users).where(db.users.c.id==target['admin_id'])).mappings().one()
                if not set(companies)<=set(manager['companies']):raise HTTPException(400,'Permissions cannot exceed the assigned administrator.')
            values['companies']=list(dict.fromkeys(companies))
        if 'active' in payload:
            if not isinstance(payload['active'],bool):raise HTTPException(400,'Active must be true or false.')
            if not payload['active'] and target['role']=='super_admin':
                raise HTTPException(400,'Super administrator accounts cannot be disabled through this screen.')
            values['active']=payload['active']
            # Disabling an Admin: the people who report to them would be locked out. The Super Admin says who takes them
            # over (another Admin, or nobody: they then answer to the Super Admin directly).
            team=[] if payload['active'] or target['role']!='admin' else c.execute(select(db.users.c.id,db.users.c.name,db.users.c.companies).where(db.users.c.admin_id==id,db.users.c.active==True).order_by(db.users.c.name)).mappings().all()
            if team:
                if 'reassign_to' not in payload:
                    return JSONResponse({'error':f"{target['name']} manages {len(team)} {'person' if len(team)==1 else 'people'}. Choose who takes them over before disabling this account.",'team':[r['name'] for r in team],'needs':'reassign_to'},409)
                successor=payload['reassign_to'] or None
                if successor:
                    manager=c.execute(select(db.users).where(db.users.c.id==successor,db.users.c.role=='admin',db.users.c.active==True,db.users.c.id!=id)).mappings().first()
                    if not manager:raise HTTPException(400,'Choose an active Admin to take over these users.')
                c.execute(db.users.update().where(db.users.c.admin_id==id).values(admin_id=successor))
                c.execute(delete(db.sessions).where(db.sessions.c.user_id.in_([r['id'] for r in team])))
                db.log(c,a,'user.team_reassigned',detail={'from':id,'to':successor,'users':[r['id'] for r in team]})
        if 'services' in payload:
            import company_admin
            values['services']=company_admin.clean_user_services(payload['services'])
        if 'custom_services' in payload:values['custom_services']=payload['custom_services'] is True
        if payload.get('reset_password'):
            temporary=secrets.token_urlsafe(15);values.update(password=sec.password_hash(temporary),reset_required=True)
        if not values:raise HTTPException(400,'No changes supplied.')
        c.execute(db.users.update().where(db.users.c.id==id).values(**values))
        c.execute(delete(db.sessions).where(db.sessions.c.user_id==id))
        db.log(c,a,'user.permissions_changed',detail={'user_id':id,'fields':list(values.keys()),'companies':values.get('companies',target['companies'])})
        return {'ok':True,'temporary_password':temporary}

@app.post('/api/signature')
def signature(payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c)
        if a['role']!='super_admin':raise HTTPException(403,'Only the Super Admin can replace company signatures.')
        sec.ensure_company(a,payload.get('entity'))
        # Saved in Company settings, like an image uploaded there.
        companies.save_profile(c,a,ENTITIES[payload['entity']],{'signature':payload.get('image',''),'signature_kind':'uploaded'});return {'ok':True}

@app.post('/api/import-legacy')
def import_legacy(request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c)
        if a['role']!='super_admin':raise HTTPException(403,'Super Admin access required.')
    from migrate_legacy import import_existing
    return import_existing(a)

crm_bridge.install(app)
import crm_bridge_v2
crm_bridge_v2.install(app)
import oidc_support
oidc_support.install(app,db,sec,session_response,ORIGIN,'TFSC',sec.get_actor)
import esign
esign.install(app)
import clients
clients.install(app)
import client_records
client_records.install(app)
client_trash.install(app)
import client_import
client_import.install(app)
import esign_bulk
esign_bulk.install(app)
import company_admin
company_admin.install(app)   # before companies.install so its literal routes beat /api/admin/companies/{key}
companies.install(app)
import insights
insights.install(app)
import health
health.install(app)
PUBLIC={'styles.css','tfsc.css','portal.css','ui.css','marks.css','login.js','sign.js','sign.css','verify.js','verify.css','resilient.js','session.js'}
PRIVATE={'app.js','format_guard.js','studio.js','portal.js','reviews.js','esign.js','esign.css','clients.js','clients.css','home.js','reports.js','admin.js','records.js','records.css'}
@app.get('/{name}')
def static_file(name:str,request:Request,v:str=''):
    # Scripts and style sheets hold no data (every piece of data comes from /api, which checks the session), so they are
    # served without a database round trip and cached by the browser under the release fingerprint.
    if name not in PRIVATE and name not in PUBLIC:return not_found(request)
    return FileResponse(db.APP/name,headers={'Cache-Control':static_assets.cache_header(v)})
ASSETS=re.compile(r'tfsc-logo\.png|brand/(scribe|ehs|24-7)\.png|fonts/(Poppins-Regular\.ttf|Poppins-SemiBold\.ttf|Inter-var\.woff2|sig-[a-z]+\.ttf)')
@app.get('/assets/{name:path}')
def asset(name:str):
    # Logos and fonts only (signature styles, the interface typeface); never the letterhead templates.
    if not ASSETS.fullmatch(name) or not (db.APP/'assets'/name).is_file():raise HTTPException(404,'Not found.')
    return FileResponse(db.APP/'assets'/name,headers={'Cache-Control':'public, max-age=86400'})
