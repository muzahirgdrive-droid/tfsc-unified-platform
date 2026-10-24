"""Loopback-only contract generation service. No cloud services or API keys."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import unicodedata
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from revised_content import CATALOG, build_baa, build_services
from nda_content import build_nda
from signature_store import save_signature
from entities import ENTITIES, get_entity, signature_path
from document_engine import generate_docx, convert_to_pdf

APP = Path(__file__).resolve().parent
VERSION = '3.1.0'
# The governing state defaults to the contracting company's own state (entities.py) and applies to every agreement.
DEFAULTS = dict(provider_address='9436 W. Lake Mead Blvd Ste 5 PMB 1034, Las Vegas, NV 89134',
                provider_email='', notice_days=30, payment_days=15, cure_days=30,
                nda_purpose='evaluating, negotiating and performing a business relationship for healthcare administrative, revenue cycle, credentialing, staffing and related business services',
                nda_term_years=2, nda_survival_years=3)
DOCUMENT_TITLES = {'baa': 'Business Associate Agreement', 'services': 'Services Agreement', 'nda': 'Mutual Nondisclosure Agreement'}
DOCUMENT_STEMS = {'baa': 'BAA', 'services': 'Services', 'nda': 'NDA'}
TOKEN = secrets.token_urlsafe(32)
FILES = {}
GENERATE_LOCK = threading.Lock()
STORE_LOCK = threading.Lock()
DATA_DIR = APP / '.data'
OUTPUT_ROOT = APP.parent / 'Client Contracts'
CATALOG_MAP = {s['id']: s for s in CATALOG}
STATES = {'Alabama','Alaska','Arizona','Arkansas','California','Colorado','Connecticut','Delaware',
          'District of Columbia','Florida','Georgia','Hawaii','Idaho','Illinois','Indiana','Iowa','Kansas',
          'Kentucky','Louisiana','Maine','Maryland','Massachusetts','Michigan','Minnesota','Mississippi',
          'Missouri','Montana','Nebraska','Nevada','New Hampshire','New Jersey','New Mexico','New York',
          'North Carolina','North Dakota','Ohio','Oklahoma','Oregon','Pennsylvania','Rhode Island',
          'South Carolina','South Dakota','Tennessee','Texas','Utah','Vermont','Virginia','Washington',
          'West Virginia','Wisconsin','Wyoming'}


def clean(value, label, required=False, limit=500):
    if value is None:
        value = ''
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError(f'{label} must be text.')
    value = unicodedata.normalize('NFC', str(value)).strip()
    value = value.replace('\u2014', ', ').replace('\u2013', '-').replace('--', ', ')
    value = ''.join(c for c in value if c in '\n\t' or ord(c) >= 32)
    if required and not value:
        raise ValueError(f'{label} is required.')
    if len(value) > limit:
        raise ValueError(f'{label} must be {limit} characters or fewer.')
    return value


def number(value, label, default=None, minimum=0.01, maximum=1000000000):
    if value in ('', None):
        value = default
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise ValueError(f'Enter a valid {label}.') from None
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f'{label} must be between {minimum:g} and {maximum:g}.')
    return result


def date_value(value, label):
    value = clean(value, label, True, 10)
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f'{label} must be a valid date.') from None


def service_value(item, custom=False):
    if not isinstance(item, dict):
        raise ValueError('Each selected service must contain its pricing details.')
    out = {}
    if custom:
        for field in ('name', 'scope', 'responsibilities', 'exclusions'):
            out[field] = clean(item.get(field), f'Custom service {field}', True, 200 if field == 'name' else 2500)
        out['id'] = 'custom_' + hashlib.sha256(out['name'].encode()).hexdigest()[:10]
        out['category'] = item.get('category', 'on_demand')
        if out['category'] not in ('recurring', 'on_demand'):
            raise ValueError('Select a valid custom service category.')
        allowed = ('fte','pte','unit','fixed','consultancy') if out['category'] == 'recurring' else ('one_time','two_installments')
    else:
        service_id = item.get('id')
        if service_id not in CATALOG_MAP:
            raise ValueError('An unknown service was selected. Reload the application.')
        spec = CATALOG_MAP[service_id]
        out.update(id=service_id, name=spec['name'], category=spec['category'])
        allowed = [m['id'] for m in spec['models']]
    out['model'] = item.get('model')
    if out['model'] not in allowed:
        raise ValueError(f"Select a valid pricing model for {out['name']}.")
    if out['model']=='rate_card':
        out['government_rate']=number(item.get('government_rate'),'Government payer application rate')
        out['commercial_rate']=number(item.get('commercial_rate'),'Commercial payer application rate')
        out['rate']=out['government_rate']
    else:
        out['rate'] = number(item.get('rate'), f"rate for {out['name']}", maximum=100 if out['model']=='percentage' else 1000000000)
    if out['id']=='website_development':
        out['website_kind']=item.get('website_kind','wordpress')
        if out['website_kind'] not in ('wordpress','custom'):
            raise ValueError('Choose WordPress or custom website development.')
    out['quantity'] = number(item.get('quantity'), f"quantity for {out['name']}", default=1, maximum=1000000)
    out['hours'] = number(item.get('hours'), 'weekly hours per resource', default=40 if out['model']=='fte' else 20, maximum=168)
    out['unit'] = clean(item.get('unit'), 'Unit description', out['model']=='unit', 100)
    out['details'] = clean(item.get('details'), 'Service details', not custom and out['id']=='other', 2500)
    out['milestone1'] = clean(item.get('milestone1'), 'First invoice milestone', out['model']=='two_installments', 300)
    out['milestone2'] = clean(item.get('milestone2'), 'Second invoice milestone', out['model']=='two_installments', 300)
    out['installment_percent'] = number(item.get('installment_percent'), 'First installment percentage', default=50, maximum=99.99)
    return out


def validate(payload, draft=False):
    if not isinstance(payload, dict):
        raise ValueError('The submitted form is not valid.')
    entity=get_entity(payload.get('entity'))
    data = {}
    for field, label, required, limit in (
        ('client_name','Client legal name',True,200), ('address','Client legal address',not draft,500),
        ('client_signatory','Client signatory',False,150),('client_title','Client title',False,150),
        ('client_email','Client notice email',False,200), ('provider_address','Scribe Align address',not draft,500),
        ('provider_email','Scribe Align notice email',False,200),('governing_state','Governing state',not draft,50),
        ('special_terms','Additional agreed terms',False,6000),('nda_purpose','NDA purpose',False,600)):
        default={'provider_address':entity['address'],'provider_email':entity['email'],'governing_state':entity['governing_state']}.get(field,DEFAULTS.get(field,''))
        data[field] = clean(payload.get(field, default), label, required, limit)
    data['governing_state'] = next((x for x in STATES if x.casefold()==data['governing_state'].casefold()), data['governing_state'])
    data['nda_purpose'] = data['nda_purpose'] or DEFAULTS['nda_purpose']
    for key in ('client_email','provider_email'):
        if data[key] and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', data[key]):
            raise ValueError('Enter a valid notice email address or leave it blank.')
    if not draft and data['governing_state'] not in STATES:
        raise ValueError('Use the full name of a US state or District of Columbia for governing law.')
    for field in ('notice_days','payment_days','cure_days'):
        data[field] = int(number(payload.get(field), field.replace('_',' '), default=DEFAULTS[field],minimum=1,maximum=180))
    for field,label in (('nda_term_years','NDA term'),('nda_survival_years','NDA confidentiality period')):
        data[field] = int(number(payload.get(field), label+' (years)', default=DEFAULTS[field],minimum=1,maximum=10))
    data['effective_date'] = date_value(payload.get('effective_date'), 'Effective date')
    data['baa_effective_date'] = date_value(payload.get('baa_effective_date') or data['effective_date'], 'BAA effective date')
    types = payload.get('document_types', [])
    if not isinstance(types,list) or any(x not in DOCUMENT_TITLES for x in types):
        raise ValueError('Select the Business Associate Agreement, the Services Agreement, the Mutual NDA, or a combination.')
    data['document_types'] = list(dict.fromkeys(types))
    if not data['document_types'] and not draft:
        raise ValueError('Select at least one agreement to generate.')
    raw_services, raw_custom = payload.get('services',[]), payload.get('custom_services',[])
    if not isinstance(raw_services,list) or not isinstance(raw_custom,list) or len(raw_services)+len(raw_custom)>35:
        raise ValueError('Select no more than 35 services.')
    if draft:
        # Incomplete pricing is allowed in a saved draft; generation always validates it.
        data['services'] = raw_services
        data['custom_services'] = raw_custom
    elif 'services' in data['document_types']:
        data['services'] = [service_value(s) for s in raw_services]
        data['custom_services'] = [service_value(s,True) for s in raw_custom]
        if not data['services'] and not data['custom_services']:
            raise ValueError('Select at least one service for a Services Agreement.')
        ids = [s['id'] for s in data['services'] + data['custom_services']]
        if len(ids) != len(set(ids)):
            raise ValueError('Each service may be selected only once per agreement.')
    else:
        data['services'], data['custom_services'] = [], []
    data['output_root'] = clean(payload.get('output_root') or str(OUTPUT_ROOT),'Output folder',True,500)
    data['id'] = clean(payload.get('id'), 'Draft identifier',False,50)
    if data['id'] and not re.fullmatch(r'[a-f0-9]{32}',data['id']):
        raise ValueError('Invalid saved client identifier.')
    data['entity'] = entity['name']
    data['include_signature'] = payload.get('include_signature', True) is True
    return data


def client_path(client_id):
    if not re.fullmatch('[a-f0-9]{32}',client_id):
        raise ValueError('Invalid client identifier.')
    return DATA_DIR/'clients'/f'{client_id}.json'


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def save_client(data):
    data = dict(data)
    data['id'] = data.get('id') or uuid.uuid4().hex
    data['updated_at'] = dt.datetime.now().astimezone().isoformat(timespec='seconds')
    with STORE_LOCK:
        write_json(client_path(data['id']), data)
    return data['id']


def build_documents(data, contract_id, company=None):
    # company: who signs and with which signature (Company settings in the web portal); without it, the signature image
    # saved on this computer is used.
    documents = []
    for kind in data['document_types']:
        current = dict(data)
        sig_path=signature_path(DATA_DIR,data['entity'])
        # the size the company signature prints at (Company settings; otherwise the company default)
        current['_signature_scale']=(company or {}).get('scale') or get_entity(data['entity'])['signature_scale']
        if company is not None:
            current['_signatory']=company['signatory']; current['_signatory_title']=company['title']
            if data.get('include_signature',True) and company.get('png'):
                current['_signature_bytes']=company['png']; current['_signature_crop']=company.get('crop')
        elif data.get('include_signature',True) and sig_path.is_file():
            current['_signature_path']=str(sig_path)
            meta=sig_path.with_suffix('.json')
            if meta.exists(): current['_signature_crop']=json.loads(meta.read_text()).get('crop')
        if kind=='baa':
            current['effective_date'] = data['baa_effective_date']
        builder = {'baa':build_baa,'services':build_services,'nda':build_nda}[kind]
        documents.append(dict(type=kind,title=DOCUMENT_TITLES[kind],blocks=builder(current,contract_id),data=current))
    return documents


def folder_slug(name):
    # A short hash avoids collisions from punctuation, case or truncation.
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', ' ', name)
    safe = re.sub(r'\s+',' ',safe).strip(' .')[:45].rstrip(' .') or 'Client'
    if safe.upper() in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(10)],*[f'LPT{i}' for i in range(10)]}:
        safe = 'Client '+safe
    digest = hashlib.sha256(name.strip().casefold().encode()).hexdigest()[:7]
    return f'{safe} ({digest})'


def generate(data):
    root = Path(data['output_root']).expanduser()
    if not root.is_absolute():
        raise ValueError('Choose an absolute output folder, such as C:\\Contracts.')
    root = root.resolve()
    client_folder = root/folder_slug(data['client_name'])
    if data['entity']!='SCRIBE ALIGN LLC':
        client_folder=client_folder/get_entity(data['entity'])['key']
    if len(str(client_folder))>180:
        raise ValueError('Choose a shorter output path so Word can save all contract files reliably.')
    contract_id = 'SA-'+dt.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+secrets.token_hex(2).upper()
    documents = build_documents(data,contract_id)
    root.mkdir(parents=True,exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix='.scribe-align-',dir=root))
    results = []
    published = []
    try:
        for doc in documents:
            subfolder = temp_dir/doc['title']
            subfolder.mkdir()
            stem = DOCUMENT_STEMS[doc['type']]+' '+contract_id
            docx_path = subfolder/(stem+'.docx')
            pdf_path = subfolder/(stem+'.pdf')
            generate_docx(doc['blocks'],doc['data'],contract_id,docx_path)
            convert_to_pdf(docx_path,pdf_path)
            for path in (docx_path,pdf_path):
                if not path.is_file() or path.stat().st_size<1000:
                    raise RuntimeError('Word did not create a complete document. No contract package was published.')
        # All Word and PDF files must succeed before the package is published.
        client_folder.mkdir(parents=True,exist_ok=True)
        for doc in documents:
            destination = client_folder/doc['title']
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.mkdir(exist_ok=True)
            for source in sorted((temp_dir/doc['title']).iterdir()):
                path = destination/source.name
                if path.exists():
                    raise RuntimeError('A revision with this identifier already exists. Generate again.')
                shutil.move(str(source),str(path))
                published.append(path)
                file_id = secrets.token_urlsafe(20)
                FILES[file_id] = path
                results.append(dict(name=path.name,type=path.suffix[1:],agreement=doc['type'],url='/files/'+file_id))
        record = dict(data,contract_id=contract_id,template_version=VERSION,generated_at=dt.datetime.now().astimezone().isoformat(),
                      files=[dict(path=str(FILES[x['url'].split('/')[-1]]),sha256=hashlib.sha256(FILES[x['url'].split('/')[-1]].read_bytes()).hexdigest()) for x in results])
        write_json(client_folder/'Records'/(contract_id+'.json'),record)
    except Exception:
        # Never report success for a partial generation or overwrite earlier revisions.
        for file in published:
            file.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(temp_dir,ignore_errors=True)
    warnings = []
    if data.get('include_signature',True) and not signature_path(DATA_DIR,data['entity']).is_file():
        warnings.append('No saved signature for '+get_entity(data['entity'])['signatory']+'. The signing line is blank.')
    try:
        client_id = save_client(data)
    except OSError:
        client_id = data.get('id','')
        warnings.append('Documents were saved, but the client draft could not be saved. Check the application folder permissions.')
    return dict(ok=True,contract_id=contract_id,id=client_id,folder=str(client_folder),files=results,warnings=warnings)


class Handler(BaseHTTPRequestHandler):
    server_version = 'TFSCCompliance/3.1.0'

    def log_message(self,fmt,*args):
        logging.info('%s %s',self.command,urlsplit(self.path).path)

    def trusted(self):
        host = self.headers.get('Host','')
        allowed = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        return host in allowed

    def respond(self,value,status=200):
        body = json.dumps(value,ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted():
            return self.respond({'error':'Only the local application may access this service.'},403)
        path = urlsplit(self.path).path
        if path=='/api/health':
            return self.respond(dict(app='TFSC Compliance',version=VERSION,app_path=str(APP)))
        if path=='/api/config':
            public_entities=[{k:e[k] for k in ('name','short','key','signatory','title','address','email','governing_state')} | {'signature_available':signature_path(DATA_DIR,e['name']).is_file()} for e in ENTITIES.values()]
            return self.respond(dict(catalog=CATALOG,defaults=DEFAULTS,states=sorted(STATES),entities=public_entities,output_root=str(OUTPUT_ROOT),version=VERSION))
        if path=='/api/clients':
            clients=[]
            for f in (DATA_DIR/'clients').glob('*.json'):
                try:
                    c=json.loads(f.read_text(encoding='utf-8'))
                    clients.append({k:c.get(k,'') for k in ('id','client_name','address','updated_at')})
                except (OSError,ValueError):
                    continue
            return self.respond({'clients':sorted(clients,key=lambda x:x['updated_at'],reverse=True)})
        if path.startswith('/api/clients/'):
            try:
                data=json.loads(client_path(path.rsplit('/',1)[1]).read_text(encoding='utf-8'))
                return self.respond(data)
            except (OSError,ValueError):
                return self.respond({'error':'Saved client was not found.'},404)
        if path.startswith('/files/'):
            file=FILES.get(path.split('/')[-1])
            if not file or not file.is_file():
                return self.respond({'error':'File link expired. Open the saved output folder to find the documents.'},404)
            return self.send_file(file,download=True)
        allowed={'/':APP/'index.html','/index.html':APP/'index.html','/styles.css':APP/'styles.css','/app.js':APP/'app.js','/premium.css':APP/'premium.css','/studio.js':APP/'studio.js'}
        for weight in (400,700): allowed[f'/assets/fonts/Outfit-{weight}.woff2']=APP/'assets/fonts'/f'Outfit-{weight}.woff2'
        allowed['/tfsc.css']=APP/'tfsc.css'
        allowed['/assets/tfsc-logo.png']=APP/'assets/tfsc-logo.png'
        for font in ('Poppins-Regular.ttf','Poppins-SemiBold.ttf'):
            allowed['/assets/fonts/'+font]=APP/'assets/fonts'/font
        file=allowed.get(path)
        if file and file.is_file():
            return self.send_file(file)
        return self.respond({'error':'Not found.'},404)

    def send_file(self,path,download=False):
        body=path.read_bytes()
        if path.name=='index.html':
            body=body.replace(b'__CSRF_TOKEN__',TOKEN.encode())
        self.send_response(200)
        self.send_header('Content-Type',mimetypes.guess_type(path.name)[0] or 'application/octet-stream')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if download:
            self.send_header('Content-Disposition','attachment; filename="'+path.name+'"')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        origin=self.headers.get('Origin')
        if not self.trusted() or not secrets.compare_digest(self.headers.get('X-CSRF-Token',''),TOKEN) or (origin and origin not in (f'http://127.0.0.1:{self.server.server_port}',f'http://localhost:{self.server.server_port}')):
            return self.respond({'error':'This request is not from the local contract form. Reload the application.'},403)
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size>2800000 or size<0:
                return self.respond({'error':'Form is too large.'},413)
            payload=json.loads(self.rfile.read(size) or '{}')
            path=urlsplit(self.path).path
            if path=='/api/signature':
                entity=get_entity(payload.get('entity'))
                with STORE_LOCK:
                    folder=DATA_DIR/'signatures'
                    save_signature(folder,payload.get('image',''))
                    target=signature_path(DATA_DIR,entity['name'])
                    (folder/'company-signature.image').replace(target)
                    target.with_suffix('.json').unlink(missing_ok=True)
                return self.respond({'ok':True})
            if path=='/api/clients':
                return self.respond({'id':save_client(validate(payload,draft=True))})
            if path=='/api/preview':
                data=validate(payload)
                docs=build_documents(data,'SA-PREVIEW')
                return self.respond({'documents':[{k:v for k,v in d.items() if k!='data'} for d in docs]})
            if path=='/api/generate':
                data=validate(payload)
                if not GENERATE_LOCK.acquire(blocking=False):
                    return self.respond({'error':'A contract is already being generated. Wait for it to finish.'},409)
                try:
                    return self.respond(generate(data))
                finally:
                    GENERATE_LOCK.release()
            if path=='/api/pick-folder':
                cmd=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-STA','-File',str(APP/'pick_folder.ps1')]
                result=subprocess.run(cmd,capture_output=True,timeout=300,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                if result.returncode:
                    raise RuntimeError('The folder picker could not open. Enter or paste the output path instead.')
                return self.respond({'path':result.stdout.decode('utf-8-sig',errors='replace').strip()})
            if path=='/api/shutdown':
                if GENERATE_LOCK.locked():
                    return self.respond({'error':'Wait for the current generation to finish before stopping the application.'},409)
                self.respond({'ok':True})
                threading.Thread(target=self.server.shutdown,daemon=True).start()
                return
            return self.respond({'error':'Not found.'},404)
        except (ValueError,TypeError) as exc:
            return self.respond({'error':str(exc)},400)
        except PermissionError:
            return self.respond({'error':'Cannot write to that folder. Choose a writable folder and close any open output documents.'},400)
        except Exception as exc:
            logging.exception('Operation failed')
            return self.respond({'error':'Generation could not finish. '+str(exc)},500)


def main():
    global DATA_DIR,OUTPUT_ROOT
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--output-root',type=Path)
    args=parser.parse_args()
    if args.data_dir:
        DATA_DIR=args.data_dir.resolve()
    if args.output_root:
        OUTPUT_ROOT=args.output_root.resolve()
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    (DATA_DIR/'clients').mkdir(exist_ok=True)
    logging.basicConfig(filename=DATA_DIR/'application.log',level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    print(f'http://127.0.0.1:{server.server_port}',flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    # Keep the legacy composition helpers, but never expose the old unauthenticated API.
    from launch import main as authenticated_main
    authenticated_main()
