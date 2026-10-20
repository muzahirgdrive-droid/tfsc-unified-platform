"""Idempotent local migration; historical authors remain explicitly unknown."""
import json, hashlib, time, datetime
from pathlib import Path
from sqlalchemy import select
import database as db
import server as old

def import_existing(actor):
    counts={'clients':0,'contracts':0,'documents':0,'skipped':0};roots={db.OUTPUT};ids={}
    for file in (db.DATA/'clients').glob('*.json'):
        try:d=json.loads(file.read_text(encoding='utf-8'));d=old.validate(d,True)
        except Exception:counts['skipped']+=1;continue
        if d.get('output_root'):roots.add(Path(d['output_root']))
        with db.engine.begin() as c:
            key=hashlib.sha256(d['client_name'].strip().casefold().encode()).hexdigest()
            row=c.execute(select(db.clients).where(db.clients.c.owner_id==actor['id'],db.clients.c.name_key==key)).mappings().first()
            id=row['id'] if row else d.get('id') or db.uid();ids[key]=id
            if not row:
                d['id']=id;c.execute(db.clients.insert().values(id=id,owner_id=actor['id'],name=d['client_name'],name_key=key,address=d['address'],draft=d,updated=file.stat().st_mtime));counts['clients']+=1
                db.log(c,None,'legacy.client_imported',id,{'source':'Local client draft','imported_by':actor['name']})
    for root in roots:
        if not root.is_dir():continue
        if not db.LOCAL and not root.resolve().is_relative_to(db.OUTPUT):continue
        for record in root.glob('**/Records/*.json'):
            try:
                d=json.loads(record.read_text(encoding='utf-8'));cid=d['contract_id'];key=hashlib.sha256(d['client_name'].strip().casefold().encode()).hexdigest()
                files=[]
                for f in d.get('files',[]):
                    p=Path(f['path']).resolve()
                    if not p.is_relative_to(root.resolve()) or not p.is_file():continue
                    body=p.read_bytes();sha=hashlib.sha256(body).hexdigest()
                    if f.get('sha256') and f['sha256']!=sha:continue
                    files.append(dict(id=db.uid(),contract_id=cid,name=p.name,kind=p.suffix[1:],agreement='baa' if p.name.startswith('BAA') else 'services',path=str(p),sha256=sha,size=len(body),body=body))
                if not files:counts['skipped']+=1;continue
                with db.engine.begin() as c:
                    if c.execute(select(db.contracts.c.id).where(db.contracts.c.id==cid)).first():continue
                    id=ids.get(key)
                    if not id:
                        existing=c.execute(select(db.clients).where(db.clients.c.owner_id==actor['id'],db.clients.c.name_key==key)).mappings().first()
                        id=existing['id'] if existing else db.uid()
                        if not existing:
                            d['id']=id;c.execute(db.clients.insert().values(id=id,owner_id=actor['id'],name=d['client_name'],name_key=key,address=d.get('address',''),draft=d,updated=record.stat().st_mtime));counts['clients']+=1
                        ids[key]=id
                    c.execute(db.contracts.insert().values(id=cid,client_id=id,owner_id=actor['id'],entity=d.get('entity','SCRIBE ALIGN LLC'),created=record.stat().st_mtime,payload=d,folder=str(record.parent.parent),version=d.get('template_version','legacy'),legacy=True))
                    for f in files:c.execute(db.documents.insert().values(**f))
                    db.log(c,None,'legacy.contract_imported',id,{'contract_id':cid,'original_author':'Unknown','imported_by':actor['name']})
                counts['contracts']+=1;counts['documents']+=len(files)
            except Exception:counts['skipped']+=1
    return counts
