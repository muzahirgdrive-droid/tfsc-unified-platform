"""Durable database queue worker. Run separately from the API in production."""
import json, os, shutil, signal, sys, tempfile, threading, time, hashlib, logging
from pathlib import Path
from sqlalchemy import select, and_, or_
import database as db
import security as sec
import server as legacy
import crm_bridge
import companies
import entities as entmod
from entities import get_entity
from document_engine import generate_docx,convert_to_pdf

LEASE=180          # seconds a generation may go without a sign of life before another worker takes it over
MAX_TRIES=3        # an interrupted generation is started again by itself this many times before it is reported as failed
stopping=threading.Event()

def claim():
    now=time.time()
    with db.engine.begin() as c:
        # A job whose worker stopped giving signs of life (a crash, a restart, a power cut) goes back in line by itself:
        # generating again is safe, because documents are published in one step under the job's own contract number.
        lost=c.execute(select(db.jobs.c.id,db.jobs.c.attempts).where(db.jobs.c.state=='running',db.jobs.c.lease_until<now)).all()
        retry=[r[0] for r in lost if r[1]<MAX_TRIES];failed=[r[0] for r in lost if r[1]>=MAX_TRIES]
        if retry:c.execute(db.jobs.update().where(db.jobs.c.id.in_(retry),db.jobs.c.state=='running',db.jobs.c.lease_until<now).values(state='queued',lease=None,lease_until=None,updated=now))
        if failed:
            c.execute(db.jobs.update().where(db.jobs.c.id.in_(failed),db.jobs.c.state=='running',db.jobs.c.lease_until<now).values(state='failed',error='The documents could not be created after several tries. Generate the agreement again; if it keeps happening, tell your Super Admin.',updated=now))
            crm_bridge.release_failed(c,failed)
        query=select(db.jobs).where(db.jobs.c.state=='queued').order_by(db.jobs.c.created).limit(1)
        if c.dialect.name=='postgresql':query=query.with_for_update(skip_locked=True)
        row=c.execute(query).mappings().first()
        if not row:return None
        lease=db.uid()
        changed=c.execute(db.jobs.update().where(db.jobs.c.id==row['id'],db.jobs.c.state=='queued').values(state='running',lease=lease,lease_until=now+LEASE,attempts=db.jobs.c.attempts+1,updated=now))
        if changed.rowcount!=1:return None
        return dict(row,lease=lease,attempts=row['attempts']+1)

def keep_alive(job,done):
    """While a generation runs, its lease is renewed every half minute; it lapses within three minutes of the worker dying."""
    while not done.wait(30):
        try:
            with db.engine.begin() as c:c.execute(db.jobs.update().where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease'],db.jobs.c.state=='running').values(lease_until=time.time()+LEASE))
        except Exception:logging.warning('lease of generation %s could not be renewed',job['id'],exc_info=True)

def friendly(exc):
    """What the person who clicked Generate reads. Technical detail (commands, paths) stays in the server log."""
    import subprocess
    if isinstance(exc,subprocess.TimeoutExpired):return 'The document took too long to convert to PDF. Please generate it again.'
    if isinstance(exc,subprocess.CalledProcessError):return 'The document could not be converted to PDF. Please generate it again; if it keeps happening, tell your Super Admin.'
    if isinstance(exc,(ValueError,)) or hasattr(exc,'detail'):return str(getattr(exc,'detail',exc))[:1500]
    if isinstance(exc,OSError):return 'The documents could not be saved to storage. Please try again; if it keeps happening, tell your Super Admin.'
    return 'The documents could not be created because of an unexpected problem. Please try again; if it keeps happening, tell your Super Admin.'

def process(job):
    payload=dict(job['payload']);cid='TFSC-'+job['id'].upper();published=[];stage=None
    done=threading.Event();threading.Thread(target=keep_alive,args=(job,done),name='tfsc-lease',daemon=True).start()
    try:
        with db.engine.begin() as c:
            actor=c.execute(select(db.users).where(db.users.c.id==job['owner_id'],db.users.c.active==True)).mappings().first()
            if not actor:raise ValueError('The account that requested this generation is inactive.')
            actor=dict(actor)
            if actor['admin_id']:
                manager=c.execute(select(db.users).where(db.users.c.id==actor['admin_id'])).mappings().first()
                if not manager or not manager['active']:raise ValueError('The assigned administrator is inactive.')
                actor['companies']=[x for x in actor['companies'] if x in manager['companies']]
            sec.ensure_company(actor,payload['entity'])
            crm_bridge.check_worker(c,job)
            db.log(c,actor,'contract.generation_started',job['client_id'],{'job_id':job['id']})
            entmod.invalidate()   # company details edited a moment ago apply to this agreement (other processes cache them briefly)
            company=companies.profile(c,payload['entity'])
        root=Path(payload['output_root']).resolve()
        if not db.LOCAL and not root.is_relative_to(db.OUTPUT):raise ValueError('Output destination is outside managed storage.')
        folder=root/legacy.folder_slug(payload['client_name'])/get_entity(payload['entity'])['key']/actor['id'][:8]
        root.mkdir(parents=True,exist_ok=True)   # the client's own folder is made only when the documents are published
        stage=Path(tempfile.mkdtemp(prefix='.tfsc-',dir=root))
        # All worker processes use the configured signature store, never request-specific globals.
        legacy.DATA_DIR=db.DATA
        docs=legacy.build_documents(payload,cid,company);files=[]
        for doc in docs:
            sub=stage/doc['title'];sub.mkdir()
            stem=legacy.DOCUMENT_STEMS[doc['type']]+' '+cid
            word=sub/(stem+'.docx');pdf=sub/(stem+'.pdf')
            generate_docx(doc['blocks'],doc['data'],cid,word);convert_to_pdf(word,pdf)
            for path in (word,pdf):
                body=path.read_bytes()
                if len(body)<1000:raise ValueError('Document output is incomplete.')
                files.append(dict(id=db.uid(),contract_id=cid,name=path.name,kind=path.suffix[1:],agreement=doc['type'],path=str(folder/doc['title']/path.name),sha256=hashlib.sha256(body).hexdigest(),size=len(body),body=body,source=path))
        warnings=[]
        if payload.get('include_signature') and not company['png']:warnings.append('No company signature is saved for '+company['signatory']+'. The company signing line is blank. A Super Admin can add it under Admin > Company settings.')
        payload=dict(payload,company_signatory={'name':company['signatory'],'title':company['title'],'signature':company['source'] or 'none'})
        result=dict(ok=True,contract_id=cid,id=job['client_id'],folder=str(folder),warnings=warnings,
            files=[dict(name=f['name'],type=f['kind'],agreement=f['agreement'],url='/files/'+f['id']) for f in files])
        with db.engine.begin() as c:
            owns=c.execute(select(db.jobs.c.state).where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease']).with_for_update()).scalar()
            if owns!='running':raise ValueError('Generation lease expired; output was not published.')
            crm_bridge.check_worker(c,job)
            c.execute(db.contracts.insert().values(id=cid,client_id=job['client_id'],owner_id=job['owner_id'],entity=payload['entity'],created=time.time(),payload=payload,folder=str(folder),version='4.0.0',legacy=False,test=payload.get('test') is True))
            for f in files:
                c.execute(db.documents.insert().values(**{k:v for k,v in f.items() if k!='source'}))
                dest=Path(f['path']);dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=f['sha256']:
                    # Only an earlier, interrupted try of this same job can have left a file here (the name carries the job's
                    # contract number, and that contract is only being recorded now): it is replaced.
                    if job.get('attempts',1)<=1:raise ValueError('A different file already exists at this revision path.')
                    dest.unlink()
                if not dest.exists():os.replace(f['source'],dest);published.append(dest)
            c.execute(db.jobs.update().where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease']).values(state='succeeded',result=result,updated=time.time(),error=None))
            crm_bridge.finish_worker(c,job,files)
            db.log(c,actor,'contract.generated',job['client_id'],{'contract_id':cid,'entity':payload['entity'],'files':[{'name':f['name'],'sha256':f['sha256']} for f in files]})
        return result
    except Exception as exc:
        # Only remove files created by this attempt, never existing revisions.
        for path in published:path.unlink(missing_ok=True)
        message=friendly(exc)
        with db.engine.begin() as c:
            c.execute(db.jobs.update().where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease']).values(state='failed',error=message,updated=time.time()))
            crm_bridge.release_failed(c,[job['id']])
            user=c.execute(select(db.users).where(db.users.c.id==job['owner_id'])).mappings().first()
            db.log(c,user,'contract.failed',job['client_id'],{'job_id':job['id'],'reason':message})
        logging.exception('Generation failed: %s',job['id'])
        return None
    finally:
        done.set()
        if stage:shutil.rmtree(stage,ignore_errors=True)

def generation_loop(stop=stopping):
    import esign_worker
    state={}
    while not stop.is_set():
        try:
            if db.LOCAL and (db.DATA/'stop-request.json').exists():return
            esign_worker.heartbeat('generate',state)
            job=claim()
            if job:process(job)
            else:stop.wait(1)
        except Exception:logging.exception('Worker queue error');stop.wait(3)

def run():
    """Everything in one process: the local edition, and any installation that sets TFSC_WORKER_PROCESSES=0."""
    db.init_db()
    import esign_worker
    esign_worker.start(stop=stopping)  # email, envelope completion, reminders and inbox reading run beside document generation
    generation_loop()

# ---------------------------------------------------------------- production: one process per lane, supervised
def lanes():
    """What runs in the worker container. Document generation and signed copies are processor work, so they get a process
    each (Python runs one thing at a time per process); email, reminders and inbox reading wait on the network."""
    count=lambda name,default:max(0,int(os.getenv(name,default)))
    return ['generate']*count('TFSC_GENERATION_WORKERS','2')+['complete']*count('TFSC_COMPLETION_WORKERS','2')+['mail','sweep']

def child(lane):
    """One lane in its own process. SIGTERM (a stop, a restart, an update) lets the task in hand finish, then the process ends."""
    signal.signal(signal.SIGTERM,lambda *_:stopping.set());signal.signal(signal.SIGINT,lambda *_:stopping.set())
    import telemetry
    telemetry.configure('tfsc-agreements-worker','5.1.0',not db.LOCAL,engine=db.engine)
    logging.getLogger('tfsc.worker').info('worker lane %s started (pid %s)',lane,os.getpid())
    if lane=='generate':generation_loop()
    else:
        import esign_worker
        esign_worker.loop(lane,stopping)

def supervise():
    import multiprocessing as mp
    db.init_db()
    ctx=mp.get_context('spawn');wanted=lanes();running={}   # process -> lane
    signal.signal(signal.SIGTERM,lambda *_:stopping.set());signal.signal(signal.SIGINT,lambda *_:stopping.set())
    def launch(lane):
        p=ctx.Process(target=child,args=(lane,),name='tfsc-'+lane,daemon=False);p.start();running[p]=lane
    for lane in wanted:launch(lane)
    crashes=[]
    while not stopping.is_set():
        stopping.wait(2)
        for p,lane in list(running.items()):
            if not p.is_alive() and not stopping.is_set():
                del running[p];now=time.time();crashes=[t for t in crashes if now-t<300]+[now]
                logging.error('worker lane %s stopped unexpectedly (exit %s); starting it again',lane,p.exitcode)
                if len(crashes)>20:logging.critical('worker lanes keep stopping; leaving it to the container to restart');stopping.set();break
                time.sleep(min(30,len(crashes)));launch(lane)
    # finish what is in hand, then stop (the container allows about two minutes)
    for p in running:
        if p.is_alive():p.terminate()
    deadline=time.time()+float(os.getenv('TFSC_WORKER_STOP_SECONDS','110'))
    for p in running:p.join(max(0.1,deadline-time.time()))
    for p in running:
        if p.is_alive():p.kill()
    sys.exit(0)

if __name__=='__main__':
    import telemetry
    logging.basicConfig(level=logging.INFO) if not logging.getLogger().handlers else None
    if db.LOCAL or db.engine.dialect.name!='postgresql' or os.getenv('TFSC_WORKER_PROCESSES','1')=='0':
        telemetry.configure('tfsc-agreements-worker','5.1.0',not db.LOCAL,engine=db.engine)
        signal.signal(signal.SIGTERM,lambda *_:stopping.set())
        run()
    else:supervise()
