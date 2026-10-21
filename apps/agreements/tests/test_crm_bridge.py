import os,sys,time,json,hmac,hashlib,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import test_portal as baseline
import portal, database as db, worker, crm_bridge as bridge
from sqlalchemy import select

class BridgeTests(unittest.TestCase):
 add=baseline.PortalTests.add
 login=baseline.PortalTests.login
 post=baseline.PortalTests.post
 payload=baseline.PortalTests.payload
 save=baseline.PortalTests.save
 def setUp(self):
  baseline.PortalTests.setUp(self);self.env=patch.dict(os.environ,{'TFSC_REQUIRE_APPROVAL':'1','TFSC_CRM_SHARED_SECRET':'synthetic-secret-'+('a'*48)});self.env.start()
 def tearDown(self):
  self.env.stop();baseline.PortalTests.tearDown(self)
 def test_review_page_sends_signed_out_visitors_to_login_and_back(self):
  self.api.cookies.clear();r=self.api.get('/reviews',follow_redirects=False)
  self.assertEqual((r.status_code,r.headers['location']),(303,'/login?next=/reviews'))
  self.login(self.u1);self.assertEqual(self.api.get('/reviews',follow_redirects=False).status_code,200)
  js=(Path(db.APP)/'login.js').read_text(encoding='utf-8');self.assertIn('location.href=target()',js);self.assertIn("!n.startsWith('//')",js)
 def submitted(self):
  self.login(self.u1);cid=self.save();r=self.post('/api/reviews/submit',{'client_id':cid});self.assertEqual(r.status_code,200,r.text);return cid,r.json()
 def approve(self,r):
  self.login(self.admin);out=self.post('/api/reviews/'+r['id']+'/decision',{'decision':'approve','version':r['version'],'reason':'Client details and fees reviewed.'});self.assertEqual(out.status_code,200,out.text)
 def test_unapproved_generation_and_self_approval_blocked(self):
  cid,r=self.submitted();p=self.payload();p['id']=cid
  denied=self.post('/api/generate',p,**{'Idempotency-Key':db.uid()});self.assertEqual(denied.status_code,409,denied.text)
  self.assertEqual(self.post('/api/reviews/'+r['id']+'/decision',{'decision':'approve','version':1,'reason':'Self'}).status_code,403)
  self.login(self.other);self.assertEqual(self.post('/api/reviews/'+r['id']+'/decision',{'decision':'approve','version':1,'reason':'Out of scope'}).status_code,403)
 def test_approved_generation_manifest_and_download(self):
  cid,r=self.submitted();self.approve(r);self.login(self.u1)
  generated=self.post('/api/reviews/'+r['id']+'/generate',{},**{'Idempotency-Key':db.uid()});self.assertEqual(generated.status_code,202,generated.text)
  job=worker.claim()
  def word(blocks,data,cid,path):Path(path).write_bytes(b'synthetic-docx'*150)
  def pdf(word,path):Path(path).write_bytes(b'synthetic-pdf'*150)
  with patch.object(worker,'generate_docx',side_effect=word),patch.object(worker,'convert_to_pdf',side_effect=pdf):result=worker.process(job)
  self.assertIsNotNone(result)
  review=self.api.get('/api/reviews').json()['reviews'][0];self.assertEqual(review['state'],'issued');self.assertEqual(len(review['manifest']),4)
  self.assertEqual(self.api.get(result['files'][0]['url']).status_code,200)
 def test_failed_generation_hands_the_approval_back_for_an_explicit_retry(self):
  cid,r=self.submitted();self.approve(r);self.login(self.u1)
  self.assertEqual(self.post('/api/reviews/'+r['id']+'/generate',{},**{'Idempotency-Key':db.uid()}).status_code,202)
  job=worker.claim()
  with patch.object(worker,'generate_docx',side_effect=RuntimeError('renderer crashed')):self.assertIsNone(worker.process(job))
  review=self.api.get('/api/reviews').json()['reviews'][0];self.assertEqual((review['state'],review['job_id']),('approved',None))
  with db.engine.connect() as c:self.assertEqual(c.execute(select(db.jobs.c.state,db.jobs.c.error).where(db.jobs.c.id==job['id'])).one()[0],'failed')
  retry=self.post('/api/reviews/'+r['id']+'/generate',{},**{'Idempotency-Key':db.uid()});self.assertEqual(retry.status_code,202,retry.text)
 def test_abandoned_generation_lease_also_hands_the_approval_back(self):
  cid,r=self.submitted();self.approve(r);self.login(self.u1)
  self.post('/api/reviews/'+r['id']+'/generate',{},**{'Idempotency-Key':db.uid()});job=worker.claim()
  with db.engine.begin() as c:c.execute(db.jobs.update().where(db.jobs.c.id==job['id']).values(lease_until=time.time()-1))
  self.assertIsNone(worker.claim())  # the expired job is failed, nothing else is queued
  self.assertEqual(self.api.get('/api/reviews').json()['reviews'][0]['state'],'approved')
 def test_changed_content_cannot_reuse_approval(self):
  cid,r=self.submitted();self.approve(r);self.login(self.u1);p=self.payload();p.update(id=cid,special_terms='Changed after approval.')
  response=self.post('/api/generate',p,**{'Idempotency-Key':db.uid()});self.assertEqual(response.status_code,409,response.text)
 def test_changed_assets_and_revoked_reviewer_block_release(self):
  cid,r=self.submitted();self.approve(r);self.login(self.u1)
  with patch.object(bridge,'asset_hash',return_value='new-template'):
   response=self.post('/api/reviews/'+r['id']+'/generate',{},**{'Idempotency-Key':db.uid()});self.assertEqual(response.status_code,409,response.text)
  with db.engine.begin() as c:c.execute(db.users.update().where(db.users.c.id==self.admin['id']).values(active=False))
  response=self.post('/api/reviews/'+r['id']+'/generate',{},**{'Idempotency-Key':db.uid()});self.assertIn(response.status_code,[401,403])
 def signed_post(self,body,nonce=None,path='/api/integrations/v1/crm/handoffs'):
  raw=json.dumps(body,separators=(',',':')).encode();stamp=str(int(time.time()));nonce=nonce or db.uid();value='\n'.join([stamp,nonce,'POST',path,hashlib.sha256(raw).hexdigest()]);signature=hmac.new(os.environ['TFSC_CRM_SHARED_SECRET'].encode(),value.encode(),hashlib.sha256).hexdigest()
  return self.api.post(path,content=raw,headers={'Content-Type':'application/json','X-TFSC-Timestamp':stamp,'X-TFSC-Nonce':nonce,'X-TFSC-Signature':signature})
 def test_signed_handoff_mapping_replay_and_single_use(self):
  self.login(self.super);crm_id=db.uid();mapped=self.post('/api/integrations/crm/actors',{'crm_actor_id':crm_id,'tfsc_user_id':self.u1['id']});self.assertEqual(mapped.status_code,200)
  body={'crm_actor_id':crm_id,'crm_client_id':db.uid(),'idempotency_key':db.uid(),'entity':list(portal.ENTITIES)[0],'client':{'client_name':'Synthetic CRM client','address':'100 Example Street, New York, NY 10001'},'document_types':['baa'],'effective_date':'2026-10-15'}
  nonce=db.uid();r=self.signed_post(body,nonce);self.assertEqual(r.status_code,200,r.text);result=r.json()
  self.assertEqual(self.signed_post(body,nonce).status_code,409)
  self.login(self.u2);self.assertEqual(self.post('/api/handoffs/redeem',{'token':result['redeem_token']}).status_code,404)
  self.login(self.u1);self.assertEqual(self.post('/api/handoffs/redeem',{'token':result['redeem_token']}).status_code,200)
  self.assertEqual(self.post('/api/handoffs/redeem',{'token':result['redeem_token']}).status_code,404)
  again=self.signed_post(body);self.assertEqual(again.status_code,200);self.assertEqual(again.json()['tfsc_client_id'],result['tfsc_client_id'])
  submitted=self.post('/api/reviews/submit',{'client_id':result['tfsc_client_id']});self.assertEqual(submitted.status_code,200,submitted.text)
  status=self.signed_post({'crm_actor_id':crm_id,'crm_client_id':body['crm_client_id']},path='/api/integrations/v1/crm/status');self.assertEqual(status.status_code,200,status.text)
  self.assertEqual(status.json()['items'][0]['state'],'submitted');self.assertNotIn('payload',status.text)
  wrong=self.signed_post({'crm_actor_id':crm_id,'crm_client_id':'different-client'},path='/api/integrations/v1/crm/status');self.assertEqual(wrong.json()['items'],[])
  body['client']['client_name']='Changed';self.assertEqual(self.signed_post(body).status_code,409)

class ManageLinkTests(unittest.TestCase):
 """`manage.py link-crm-admin`, run on the server by deploy/vps/tfsc.sh link-admins, exactly as the installer calls it."""
 def setUp(self):
  import tempfile;self.tmp=tempfile.TemporaryDirectory(prefix='tfsc-manage-');self.url='sqlite:///'+self.tmp.name+'/m.db'
 def tearDown(self):self.tmp.cleanup()
 def manage(self,*args):
  import subprocess
  env={**os.environ,'TFSC_MODE':'local','TFSC_DATA_DIR':self.tmp.name,'DATABASE_URL':self.url}
  return subprocess.run([sys.executable,'manage.py',*args],cwd=db.APP,env=env,capture_output=True,text=True,timeout=120)
 def test_link_crm_admin_maps_the_first_super_admin_and_audits_it(self):
  crm_id=db.uid();r=self.manage('link-crm-admin',crm_id)
  self.assertNotEqual(r.returncode,0);self.assertIn('Set up the Agreements Super Admin first',r.stderr)
  e=db.make_engine(self.url)
  try:
   with e.begin() as c:
    for i,role in enumerate(('admin','super_admin','super_admin')):
     c.execute(db.users.insert().values(id=str(i)*32,email='u'+str(i)+'@example.com',name='User '+str(i),password='x',role=role,companies=[],active=True,reset_required=False,created=1000.0+i))
   bad=self.manage('link-crm-admin','not-a-crm-id');self.assertNotEqual(bad.returncode,0);self.assertIn('Use the CRM actor ID.',bad.stderr)
   r=self.manage('link-crm-admin',crm_id.upper());self.assertEqual(r.returncode,0,r.stderr);self.assertIn('u1@example.com',r.stdout)
   with e.connect() as c:
    self.assertEqual(c.execute(select(bridge.actors.c.user_id).where(bridge.actors.c.crm_actor_id==crm_id)).scalar_one(),'1'*32)
    self.assertEqual(c.execute(select(db.audit.c.actor_id).where(db.audit.c.action=='crm.actor_linked')).scalars().all(),['1'*32])
  finally:e.dispose()

if __name__=='__main__':unittest.main()
