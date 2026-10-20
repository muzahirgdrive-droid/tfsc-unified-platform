import sys,tempfile,unittest,time,json,hashlib,os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from sqlalchemy import select
import database as db,portal,security as sec,worker
from test_contract_studio import data

class PortalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='tfsc-tests-');self.root=Path(self.tmp.name).resolve()
        self.old=(db.engine,db.DATA,db.OUTPUT)
        db.DATA=self.root;db.OUTPUT=self.root/'outputs'
        url=os.getenv('TEST_DATABASE_URL','sqlite:///'+str(self.root/'test.db'))
        if not url.startswith('sqlite:'):
            assert url.endswith('/tfsc_test'), 'Use the isolated tfsc_test database only.'
        db.engine=db.make_engine(url)
        if not url.startswith('sqlite:'):db.meta.drop_all(db.engine)
        db.init_db()
        self.api=TestClient(portal.app,base_url='http://127.0.0.1:8765')
        self.password='Only-a-test-password-29!'
        self.hash=sec.password_hash(self.password)
        self.super=self.add('super_admin',list(portal.ENTITIES));self.admin=self.add('admin',[list(portal.ENTITIES)[0]])
        self.u1=self.add('user',[list(portal.ENTITIES)[0]],self.admin['id']);self.u2=self.add('user',[list(portal.ENTITIES)[0]],self.admin['id'])
        self.other=self.add('admin',list(portal.ENTITIES))
    def tearDown(self):
        self.api.close();db.engine.dispose();db.engine,db.DATA,db.OUTPUT=self.old;self.tmp.cleanup()
    def add(self,role,companies,parent=None):
        id=db.uid();u=dict(id=id,email=id+'@example.com',name=role+' '+id[:5],password=self.hash,role=role,admin_id=parent,companies=companies,active=True,reset_required=False,created=time.time())
        with db.engine.begin() as c:c.execute(db.users.insert().values(**u))
        return u
    def login(self,u):
        self.api.cookies.clear();r=self.api.post('/api/auth/login',json={'email':u['email'],'password':self.password});self.assertEqual(r.status_code,200,r.text);self.csrf=r.json()['csrf']
    def post(self,path,body,**headers):return self.api.post(path,json=body,headers={'X-CSRF-Token':self.csrf,**headers})
    def payload(self):return dict(data(),output_root=str(db.OUTPUT),include_signature=False)
    def save(self):
        r=self.post('/api/clients',self.payload());self.assertEqual(r.status_code,200,r.text);return r.json()['id']
    def test_auth_csrf_company_and_client_isolation(self):
        self.assertEqual(self.api.get('/api/clients').status_code,401)
        self.login(self.u1)
        self.assertEqual(self.api.post('/api/clients',json=self.payload()).status_code,403)
        bad=self.payload();bad['entity']=list(portal.ENTITIES)[1]
        self.assertEqual(self.post('/api/clients',bad).status_code,403)
        cid=self.save();self.login(self.u2)
        self.assertEqual(self.api.get('/api/clients/'+cid).status_code,404)
        self.assertEqual(self.api.get('/api/clients/'+cid+'/activity').status_code,404)
        self.assertEqual(self.api.get('/api/clients').json()['clients'],[])
        self.login(self.admin);self.assertEqual(self.api.get('/api/clients/'+cid).status_code,200)
        self.login(self.other);self.assertEqual(self.api.get('/api/clients/'+cid).status_code,404)
        self.login(self.super);self.assertEqual(self.api.get('/api/clients/'+cid).status_code,200)
    def test_admin_cannot_escalate_roles_or_companies(self):
        self.login(self.admin)
        p=dict(name='Test User',email='one@example.com',role='super_admin',companies=[list(portal.ENTITIES)[0]])
        self.assertEqual(self.post('/api/users',p).status_code,403)
        p.update(role='user',companies=[list(portal.ENTITIES)[1]])
        self.assertEqual(self.post('/api/users',p).status_code,403)
        p['companies']=[list(portal.ENTITIES)[0]]
        r=self.post('/api/users',p);self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()['user']['admin_id'],self.admin['id']);self.assertTrue(r.json()['user']['reset_required'])
        self.assertEqual(self.post('/api/users/'+self.other['id'],{'active':False}).status_code,404)
        self.login(self.u1);self.assertEqual(self.api.get('/api/users').status_code,403)
    def test_permission_revocation_and_disabled_parent(self):
        self.login(self.u1);token=self.api.cookies.get('tfsc_session')
        with db.engine.begin() as c:c.execute(db.users.update().where(db.users.c.id==self.admin['id']).values(companies=[]))
        self.assertEqual(self.post('/api/clients',self.payload()).status_code,403)
        with db.engine.begin() as c:c.execute(db.users.update().where(db.users.c.id==self.admin['id']).values(active=False))
        self.assertEqual(self.api.get('/api/config').status_code,401)
    def test_job_idempotency_storage_download_and_audit(self):
        self.login(self.u1);p=self.payload();key=db.uid()
        r=self.post('/api/generate',p,**{'Idempotency-Key':key});self.assertEqual(r.status_code,202,r.text)
        id=r.json()['job_id'];self.assertEqual(self.post('/api/generate',p,**{'Idempotency-Key':key}).json()['job_id'],id)
        job=worker.claim();self.assertEqual(job['id'],id);self.assertIsNone(worker.claim())
        def word(blocks,data,cid,path):Path(path).write_bytes(b'docx-test'*200)
        def pdf(word,path):Path(path).write_bytes(b'pdf-test'*200)
        with patch.object(worker,'generate_docx',side_effect=word),patch.object(worker,'convert_to_pdf',side_effect=pdf):result=worker.process(job)
        self.assertIsNotNone(result);status=self.api.get('/api/jobs/'+id).json();self.assertEqual(status['state'],'succeeded',status)
        cid=result['id'];history=self.api.get('/api/clients/'+cid+'/history').json();self.assertEqual(len(history['contracts']),1)
        self.assertEqual(len(history['contracts'][0]['files']),4)
        with db.engine.connect() as c:
            for row in c.execute(select(db.documents)).mappings():
                self.assertEqual(Path(row['path']).read_bytes(),bytes(row['body']))
                Path(row['path']).unlink()
        # Database copies remain accessible after original disk copies are removed.
        url=result['files'][0]['url'];self.assertEqual(self.api.get(url).status_code,200)
        actions=[r['action'] for r in self.api.get('/api/clients/'+cid+'/activity').json()['activity']]
        self.assertIn('contract.generated',actions);self.assertIn('document.downloaded',actions)
        self.login(self.u2);self.assertEqual(self.api.get(url).status_code,404);self.assertEqual(self.api.get('/api/jobs/'+id).status_code,404)
        self.login(self.admin);self.assertEqual(self.api.get(url).status_code,200)
        self.login(self.other);self.assertEqual(self.api.get(url).status_code,404)
    def test_failed_export_has_no_database_documents(self):
        self.login(self.u1);r=self.post('/api/generate',self.payload(),**{'Idempotency-Key':db.uid()});job=worker.claim()
        with patch.object(worker,'generate_docx',side_effect=RuntimeError('Simulated renderer failure')):worker.process(job)
        self.assertEqual(self.api.get('/api/jobs/'+r.json()['job_id']).json()['state'],'failed')
        with db.engine.connect() as c:self.assertIsNone(c.execute(select(db.documents)).first());self.assertIsNone(c.execute(select(db.contracts)).first())
    def test_folder_picker_local_and_hosted_boundary(self):
        self.login(self.u1);r=self.post('/api/folders',{'path':str(self.root)});self.assertEqual(r.status_code,200,r.text)
        db.OUTPUT.mkdir(exist_ok=True)
        with patch.object(db,'LOCAL',False):
            self.assertEqual(self.post('/api/folders',{'path':str(self.root)}).status_code,403)
            self.assertEqual(self.post('/api/folders',{'path':str(db.OUTPUT)}).status_code,200)
    def test_password_change_revokes_previous_session(self):
        self.login(self.u1);old=self.api.cookies.get('tfsc_session')
        r=self.post('/api/auth/password',{'current_password':self.password,'password':'A-new-test-password-42!'})
        self.assertEqual(r.status_code,200,r.text);self.assertNotEqual(old,self.api.cookies.get('tfsc_session'))
        self.api.cookies.clear();self.api.cookies.set('tfsc_session',old)
        self.assertEqual(self.api.get('/api/auth/me').status_code,401)
    def concurrent_generations(self,keys,payload):
        token=self.api.cookies.get('tfsc_session');csrf=self.csrf;start=threading.Barrier(len(keys))
        def submit(key):
            client=TestClient(portal.app,base_url='http://127.0.0.1:8765')
            client.cookies.set('tfsc_session',token)
            try:
                start.wait(timeout=10)
                return client.post('/api/generate',json=payload,headers={'X-CSRF-Token':csrf,'Idempotency-Key':key})
            finally:client.close()
        with ThreadPoolExecutor(max_workers=len(keys)) as pool:return list(pool.map(submit,keys))
    def test_concurrent_duplicate_generation_returns_one_job(self):
        self.login(self.u1);key=db.uid()
        responses=self.concurrent_generations([key,key],self.payload())
        self.assertEqual(sorted(r.status_code for r in responses),[200,202],[r.text for r in responses])
        self.assertEqual(len({r.json()['job_id'] for r in responses}),1)
        with db.engine.connect() as c:
            self.assertEqual(len(c.execute(select(db.jobs)).all()),1)
            self.assertEqual(len(c.execute(select(db.clients)).all()),1)
    def test_concurrent_generation_preserves_pending_limit(self):
        self.login(self.u1);payload=self.payload();payload['id']=self.save()
        responses=self.concurrent_generations([db.uid() for _ in range(6)],payload)
        self.assertEqual(sorted(r.status_code for r in responses),[202]*5+[429],[r.text for r in responses])
        with db.engine.connect() as c:self.assertEqual(len(c.execute(select(db.jobs)).all()),5)
    def test_conflicting_client_rename_returns_conflict_without_job(self):
        self.login(self.u1);first=self.save();payload=self.payload();payload['client_name']='Another existing client'
        self.assertEqual(self.post('/api/clients',payload).status_code,200)
        payload['id']=first
        response=self.post('/api/generate',payload,**{'Idempotency-Key':db.uid()})
        self.assertEqual(response.status_code,409,response.text)
        with db.engine.connect() as c:
            self.assertIsNone(c.execute(select(db.jobs)).first())
            self.assertEqual(c.execute(select(db.clients.c.name).where(db.clients.c.id==first)).scalar(),self.payload()['client_name'])

if __name__=='__main__':unittest.main()
