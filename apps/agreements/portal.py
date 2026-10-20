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
from entities import ENTITIES, signature_path
from signature_store import image_info

VERSION='4.0.0'
ORIGIN=os.getenv('TFSC_ORIGIN','http://127.0.0.1:8765').rstrip('/')
if not db.LOCAL and (not ORIGIN.startswith('https://') or db.URL.startswith('sqlite')):
    raise RuntimeError('VPS mode requires TFSC_ORIGIN=https://your-domain and DATABASE_URL=postgresql+psycopg://...')

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    yield
app=FastAPI(title='TFSC Compliance',version=VERSION,lifespan=lifespan,docs_url=None,redoc_url=None)

@app.middleware('http')
async def boundary(request,call_next):
    from urllib.parse import urlsplit
    host=request.headers.get('host','')
    allowed={urlsplit(ORIGIN).netloc}
    if db.LOCAL:allowed.add('localhost:'+str(urlsplit(ORIGIN).port or 80))
    if host not in allowed:return JSONResponse({'error':'Invalid application host.'},403)
    if request.method not in ('GET','HEAD'):
        if request.headers.get('origin') not in (None,ORIGIN) or request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'error':'Request origin was rejected.'},403)
        try:
            if int(request.headers.get('content-length','0'))>2_800_000:
                return JSONResponse({'error':'Request is too large.'},413)
            chunks=[];size=0
            async for chunk in request.stream():
                size+=len(chunk)
                if size>2_800_000:return JSONResponse({'error':'Request is too large.'},413)
                chunks.append(chunk)
            request._body=b''.join(chunks)
        except Exception:return JSONResponse({'error':'Invalid request body.'},400)
    try:response=await call_next(request)
    except Exception:
        import logging;logging.exception('Application request failed')
        response=JSONResponse({'error':'The request could not be completed. Please try again.'},500)
    response.headers['Cache-Control']='no-store'
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    if not db.LOCAL:response.headers['Strict-Transport-Security']='max-age=31536000'
    return response

@app.exception_handler(HTTPException)
async def error(request,exc):return JSONResponse({'error':exc.detail},exc.status_code)
@app.exception_handler(ValueError)
async def invalid(request,exc):return JSONResponse({'error':str(exc)},400)

def bootstrap_available(c):return c.execute(select(db.users.c.id).limit(1)).first() is None
def session_response(c,user):
    token=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
    c.execute(delete(db.sessions).where(db.sessions.c.expires<time.time()))
    c.execute(db.sessions.insert().values(token=sec.digest(token),user_id=user['id'],csrf=csrf,expires=time.time()+28800))
    response=JSONResponse({'user':sec.public(user),'csrf':csrf})
    # Lax (like the CRM): sent on top-level arrivals from the identity provider and from the CRM's "Open Agreements"
    # link; every state change still needs the X-CSRF-Token header, so cross-site requests cannot act.
    response.set_cookie('tfsc_session',token,httponly=True,secure=not db.LOCAL,samesite='lax',max_age=28800,path='/')
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

@app.post('/api/auth/login')
def login(payload:dict,request:Request):
    email=str(payload.get('email','')).strip().casefold()[:200]
    with db.engine.begin() as c:
        allowed=sec.rate_limit(c,'login-ip:'+str(request.client.host),50) and sec.rate_limit(c,'login:'+email,10)
    if not allowed:raise HTTPException(429,'Too many sign-in attempts. Try again in 15 minutes.')
    with db.engine.begin() as c:
        user=c.execute(select(db.users).where(db.users.c.email==email,db.users.c.active==True)).mappings().first()
        if not user or not sec.verify(user['password'],str(payload.get('password',''))[:201]):
            raise HTTPException(401,'Email or password is incorrect.')
        db.log(c,user,'user.signed_in')
        c.execute(delete(db.throttle).where(db.throttle.c.key==sec.digest('login:'+email)))
        return session_response(c,user)

@app.get('/api/auth/me')
def me(request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c,True);return {'user':sec.public(a),'csrf':a['_csrf']}
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
    return HTMLResponse((db.APP/'index.html').read_text(encoding='utf-8').replace('__CSRF_TOKEN__',a['_csrf']))
@app.get('/login')
def login_page():return FileResponse(db.APP/'login.html')

@app.get('/api/config')
def config(request:Request):
    with db.engine.connect() as c:a=sec.get_actor(request,c)
    entities=[{k:e[k] for k in ('name','short','key','signatory','title','address','email')} | {'signature_available':signature_path(db.DATA,e['name']).is_file()} for e in ENTITIES.values() if a['role']=='super_admin' or e['name'] in a['companies']]
    return dict(catalog=legacy.CATALOG,defaults=legacy.DEFAULTS,entities=entities,output_root=str(db.OUTPUT),version=VERSION,
        user=sec.public(a),local=db.LOCAL,can_manage_signatures=a['role']=='super_admin')

def client_allowed(c,a,id):
    row=c.execute(select(db.clients).where(db.clients.c.id==id,sec.scope(a,db.clients.c.owner_id))).mappings().first()
    if not row:raise HTTPException(404,'Client not found.')
    return row
def save_draft(c,a,data):
    if data.get('id'):
        row=client_allowed(c,a,data['id']);id=row['id'];owner=row['owner_id']
    else:
        owner=a['id'];key=sec.digest(data['client_name'].strip().casefold())
        row=c.execute(select(db.clients).where(db.clients.c.owner_id==owner,db.clients.c.name_key==key)).mappings().first()
        id=row['id'] if row else db.uid()
    data=dict(data,id=id)
    values=dict(name=data['client_name'],name_key=sec.digest(data['client_name'].strip().casefold()),address=data['address'],draft=data,updated=time.time())
    if row:c.execute(db.clients.update().where(db.clients.c.id==id).values(**values))
    else:c.execute(db.clients.insert().values(id=id,owner_id=owner,**values))
    db.log(c,a,'client.updated' if row else 'client.created',id,{'entity':data['entity']})
    return id

@app.get('/api/clients')
def list_clients(request:Request,q:str='',page:int=0):
    page=max(0,min(page,100000))
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);condition=sec.scope(a,db.clients.c.owner_id)
        if q:condition=and_(condition,func.lower(db.clients.c.name).contains(q[:200].lower(),autoescape=True))
        rows=c.execute(select(db.clients.c.id,db.clients.c.name,db.clients.c.address,db.clients.c.updated,db.users.c.name.label('owner')).join(db.users,db.users.c.id==db.clients.c.owner_id).where(condition).order_by(db.clients.c.updated.desc(),db.clients.c.id).limit(51).offset(page*50)).mappings().all()
        return {'clients':[dict(id=r['id'],client_name=r['name'],address=r['address'],updated_at=r['updated'],owner=r['owner']) for r in rows[:50]],'has_more':len(rows)>50,'page':page}
@app.get('/api/clients/{id}')
def get_client(id:str,request:Request):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);r=client_allowed(c,a,id);d=dict(r['draft']);sec.ensure_company(a,d['entity']);return d
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
            result.append(dict(id=r['id'],entity=r['entity'],created=r['created'],owner='Legacy record (author unavailable)' if r['legacy'] else r['owner'],files=[dict(x,url='/files/'+x['id']) for x in docs]))
        return {'client':dict(id=client['id'],name=client['name'],address=client['address']),'contracts':result,'has_more':len(rows)>20}
@app.get('/api/clients/{id}/activity')
def activity(id:str,request:Request,page:int=0):
    with db.engine.connect() as c:
        a=sec.get_actor(request,c);client_allowed(c,a,id)
        rows=c.execute(select(db.audit).where(db.audit.c.client_id==id).order_by(db.audit.c.created.desc(),db.audit.c.id).limit(51).offset(max(0,page)*50)).mappings().all()
        return {'activity':[dict(r) for r in rows[:50]],'has_more':len(rows)>50}

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

@app.post('/api/preview')
def preview(payload:dict,request:Request):
    with db.engine.begin() as c:
        a=sec.get_actor(request,c);d=legacy.validate(payload);sec.ensure_company(a,d['entity'])
        if d['id']:client_allowed(c,a,d['id']);db.log(c,a,'contract.previewed',d['id'])
    docs=legacy.build_documents(d,'TFSC-PREVIEW')
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
            d['output_root']=str(output_path(d['output_root'],a))
            old=c.execute(select(db.jobs).where(db.jobs.c.owner_id==a['id'],db.jobs.c.key==key)).mappings().first()
            if old:return {'job_id':old['id'],'state':old['state']}
            pending=c.execute(select(func.count()).select_from(db.jobs).where(db.jobs.c.owner_id==a['id'],db.jobs.c.state.in_(['queued','running']))).scalar()
            if pending>=5:raise HTTPException(429,'You already have five pending generations. Wait for one to finish.')
            id=save_draft(c,a,d);d['id']=id
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
        return {'id':id,'state':row['state'],'result':row['result'],'error':row['error']}

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
        row=dict(id=db.uid(),email=valid_email(payload.get('email')),name=legacy.clean(payload.get('name'),'Name',True,150),password=sec.password_hash(password),role=role,admin_id=admin_id,companies=list(dict.fromkeys(companies)),active=True,reset_required=True,created=time.time())
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
        try:raw=base64.b64decode(payload.get('image',''),validate=True)
        except Exception:raise HTTPException(400,'Invalid image.')
        if not 1<=len(raw)<=2_000_000:raise HTTPException(400,'Image must be smaller than 2 MB.')
        image_info(raw);path=signature_path(db.DATA,payload['entity']);path.parent.mkdir(parents=True,exist_ok=True)
        temp=path.with_name(db.uid()+'.tmp');temp.write_bytes(raw);temp.replace(path);path.with_suffix('.json').unlink(missing_ok=True)
        db.log(c,a,'signature.updated',detail={'entity':payload['entity']});return {'ok':True}

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
PUBLIC={'styles.css','tfsc.css','portal.css','login.js'}
PRIVATE={'app.js','studio.js','portal.js','reviews.js'}
@app.get('/{name}')
def static_file(name:str,request:Request):
    if name in PRIVATE:
        with db.engine.connect() as c:sec.get_actor(request,c)
    elif name not in PUBLIC:raise HTTPException(404,'Not found.')
    return FileResponse(db.APP/name)
@app.get('/assets/{name:path}')
def asset(name:str):
    if name not in ('tfsc-logo.png','fonts/Poppins-Regular.ttf','fonts/Poppins-SemiBold.ttf'):raise HTTPException(404,'Not found.')
    return FileResponse(db.APP/'assets'/name)
