"""Durable database queue worker. Run separately from the API in production."""
import json, os, shutil, tempfile, time, hashlib, logging
from pathlib import Path
from sqlalchemy import select, and_, or_
import database as db
import security as sec
import server as legacy
import crm_bridge
from entities import get_entity,signature_path
from document_engine import generate_docx,convert_to_pdf

def claim():
    now=time.time()
    with db.engine.begin() as c:
        # Expired jobs require an explicit retry; never duplicate a possibly active export.
        expired=[r[0] for r in c.execute(select(db.jobs.c.id).where(db.jobs.c.state=='running',db.jobs.c.lease_until<now))]
        c.execute(db.jobs.update().where(db.jobs.c.state=='running',db.jobs.c.lease_until<now).values(state='failed',error='Worker stopped before completing this job. Review history and generate a new revision.',updated=now))
        crm_bridge.release_failed(c,expired)
        query=select(db.jobs).where(db.jobs.c.state=='queued').order_by(db.jobs.c.created).limit(1)
        if c.dialect.name=='postgresql':query=query.with_for_update(skip_locked=True)
        row=c.execute(query).mappings().first()
        if not row:return None
        lease=db.uid()
        changed=c.execute(db.jobs.update().where(db.jobs.c.id==row['id'],db.jobs.c.state=='queued').values(state='running',lease=lease,lease_until=now+1800,attempts=db.jobs.c.attempts+1,updated=now))
        if changed.rowcount!=1:return None
        return dict(row,lease=lease)

def process(job):
    payload=dict(job['payload']);cid='TFSC-'+job['id'].upper();published=[];stage=None
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
        root=Path(payload['output_root']).resolve()
        if not db.LOCAL and not root.is_relative_to(db.OUTPUT):raise ValueError('Output destination is outside managed storage.')
        folder=root/legacy.folder_slug(payload['client_name'])/get_entity(payload['entity'])['key']/actor['id'][:8]
        folder.mkdir(parents=True,exist_ok=True)
        stage=Path(tempfile.mkdtemp(prefix='.tfsc-',dir=root))
        # All worker processes use the configured signature store, never request-specific globals.
        legacy.DATA_DIR=db.DATA
        docs=legacy.build_documents(payload,cid);files=[]
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
        if payload.get('include_signature') and not signature_path(db.DATA,payload['entity']).is_file():warnings.append('No company signature was available. Signing lines are blank.')
        result=dict(ok=True,contract_id=cid,id=job['client_id'],folder=str(folder),warnings=warnings,
            files=[dict(name=f['name'],type=f['kind'],agreement=f['agreement'],url='/files/'+f['id']) for f in files])
        with db.engine.begin() as c:
            owns=c.execute(select(db.jobs.c.state).where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease']).with_for_update()).scalar()
            if owns!='running':raise ValueError('Generation lease expired; output was not published.')
            crm_bridge.check_worker(c,job)
            c.execute(db.contracts.insert().values(id=cid,client_id=job['client_id'],owner_id=job['owner_id'],entity=payload['entity'],created=time.time(),payload=payload,folder=str(folder),version='4.0.0',legacy=False))
            for f in files:
                c.execute(db.documents.insert().values(**{k:v for k,v in f.items() if k!='source'}))
                dest=Path(f['path']);dest.parent.mkdir(parents=True,exist_ok=True)
                if dest.exists():
                    if hashlib.sha256(dest.read_bytes()).hexdigest()!=f['sha256']:raise ValueError('A different file already exists at this revision path.')
                else:os.replace(f['source'],dest);published.append(dest)
            c.execute(db.jobs.update().where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease']).values(state='succeeded',result=result,updated=time.time(),error=None))
            crm_bridge.finish_worker(c,job,files)
            db.log(c,actor,'contract.generated',job['client_id'],{'contract_id':cid,'entity':payload['entity'],'files':[{'name':f['name'],'sha256':f['sha256']} for f in files]})
        return result
    except Exception as exc:
        # Only remove files created by this attempt, never existing revisions.
        for path in published:path.unlink(missing_ok=True)
        message=str(getattr(exc,'detail',exc))[:1500]
        with db.engine.begin() as c:
            c.execute(db.jobs.update().where(db.jobs.c.id==job['id'],db.jobs.c.lease==job['lease']).values(state='failed',error=message,updated=time.time()))
            crm_bridge.release_failed(c,[job['id']])
            user=c.execute(select(db.users).where(db.users.c.id==job['owner_id'])).mappings().first()
            db.log(c,user,'contract.failed',job['client_id'],{'job_id':job['id'],'reason':message})
        logging.exception('Generation failed: %s',job['id'])
        return None
    finally:
        if stage:shutil.rmtree(stage,ignore_errors=True)

def run():
    db.init_db()
    import esign_worker
    esign_worker.start()  # email, envelope completion, reminders and inbox reading run beside document generation
    while True:
        try:
            if db.LOCAL and (db.DATA/'stop-request.json').exists():return
            job=claim()
            if job:process(job)
            else:time.sleep(1)
        except Exception:logging.exception('Worker queue error');time.sleep(3)
if __name__=='__main__':
    import telemetry
    telemetry.configure('tfsc-agreements-worker','4.0.0',not db.LOCAL,engine=db.engine)
    run()
