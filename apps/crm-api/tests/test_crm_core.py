import os, sys, tempfile, pathlib, time
import pytest
from fastapi.testclient import TestClient
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TEMP=tempfile.TemporaryDirectory(prefix='tfsc-crm-test-')
os.environ['CRM_DATA_DIR']=TEMP.name
os.environ['CRM_DATABASE_URL']='sqlite:///'+str(pathlib.Path(TEMP.name)/'test.db')
os.environ['CRM_ORIGIN']='http://testserver'
os.environ['CRM_MODE']='local'
import crm_app as app
import crm_db as db
from sqlalchemy import select
PASSWORD='Synthetic-test-password-512!'

def login(email):
 c=TestClient(app.app);r=c.post('/api/auth/login',json={'email':email,'password':PASSWORD});assert r.status_code==200,r.text;c.headers['X-CSRF-Token']=r.json()['csrf'];return c

def workspace_fixture():
 with TestClient(app.app) as admin:
  key=(db.DATA/'setup-key.txt').read_text()
  r=admin.post('/api/auth/setup',json={'setup_key':key,'name':'Test Super','email':'admin@example.test','password':PASSWORD});assert r.status_code==200,r.text
  admin.headers['X-CSRF-Token']=r.json()['csrf'];root=r.json()['user']
  def user(name,role,parent):
   r=admin.post('/api/users',json={'name':name,'email':name+'@example.test','password':PASSWORD,'role':role,'manager_id':parent,'reset_required':False});assert r.status_code==200,r.text;return r.json()
  manager=user('manager','manager',root['id']);supervisor=user('supervisor','supervisor',manager['id']);a=user('agent1','sales_agent',supervisor['id']);b=user('agent2','sales_agent',supervisor['id']);other=user('other','sales_agent',root['id'])
  clients={'admin':admin,'manager':login('manager@example.test'),'supervisor':login('supervisor@example.test'),'a':login('agent1@example.test'),'b':login('agent2@example.test'),'other':login('other@example.test'),'a_id':a['id'],'b_id':b['id'],'sup_id':supervisor['id']}
  yield clients
  for value in clients.values():
   if isinstance(value,TestClient):value.close()
  db.engine.dispose()

def activity(c,**fields):
 p={'provider_name':'Synthetic Practice','phone':'202-555-0123',**fields};r=c.post('/api/activities',json=p);assert r.status_code==200,r.text;return r.json()

def test_private_metadata_never_in_agent(workspace):
 c=workspace['a']
 for path in ['/api/config','/api/auth/me','/api/activities','/api/audit']:
  r=c.get(path);assert r.status_code==200
  for field in ['domain_id','project_id','owner_email','"domains"','"projects"']:assert field not in r.text,(path,field)
 for path in ['/api/users','/api/settings','/api/meetings','/api/conflicts']:assert c.get(path).status_code==403

def test_research_and_no_attempt_rules(workspace):
 r=activity(workspace['a']);assert r['first_committed_at'] is None and r['quality'] is None
 r=workspace['a'].patch('/api/activities/'+r['id'],json={'version':r['version'],'called':False}).json()
 assert r['first_committed_at'] and r['quality'] is None and r['stage']=='No call'
 old_deadline=r['edit_deadline'];r=workspace['a'].patch('/api/activities/'+r['id'],json={'version':r['version'],'called':None}).json();assert r['edit_deadline']==old_deadline

def test_cas_and_locked_correction(workspace):
 c=workspace['a'];r=activity(c,called=True,connected=False)
 assert r['quality']==10
 assert c.patch('/api/activities/'+r['id'],json={'version':99,'remarks':'stale'}).status_code==409
 with db.engine.begin() as conn:conn.execute(db.activities.update().where(db.activities.c.id==r['id']).values(edit_deadline=time.time()-1))
 assert c.patch('/api/activities/'+r['id'],json={'version':1,'remarks':'late'}).status_code==403
 assert workspace['supervisor'].patch('/api/activities/'+r['id'],json={'version':1,'remarks':'fix'}).status_code==400
 corrected=workspace['supervisor'].patch('/api/activities/'+r['id'],json={'version':1,'remarks':'fix','reason':'Supervisor verified the record.'});assert corrected.status_code==200,corrected.text
 assert workspace['other'].patch('/api/activities/'+r['id'],json={'version':2,'remarks':'unauthorized'}).status_code==404

def test_inconsistent_outcomes_rejected(workspace):
 assert workspace['a'].post('/api/activities',json={'provider_name':'Invalid','called':False,'connected':True}).status_code==400

def test_import_idempotence_and_atomic_validation(workspace):
 c=workspace['a'];payload={'idempotency_key':'test-import-batch-1','rows':[{'provider_name':'Import practice','called':True}]}
 first=c.post('/api/import',json=payload);assert first.status_code==200,first.text
 second=c.post('/api/import',json=payload);assert second.json()==first.json()
 r=first.json()['items'][0];assert 899<=r['edit_deadline']-r['first_committed_at']<=901
 assert c.post('/api/import',json={**payload,'rows':[{'provider_name':'Different'}]}).status_code==409
 assert c.post('/api/import',json={'idempotency_key':'bad-batch','rows':[{'provider_name':'Valid'},{'provider_name':''}]}).status_code==400

def test_cross_agent_duplicate_and_ownership(workspace):
 a=activity(workspace['a'],phone='2125550198',called=True,next_followup=app.today())
 b=activity(workspace['b'],phone='+1 (212) 555-0198',called=True,next_followup=app.today());assert b['duplicate_severity']=='follow_up'
 sup=workspace['supervisor'];leads=sup.get('/api/leads').json()['items'];la=next(x for x in leads if x['activity_id']==a['id']);lb=next(x for x in leads if x['activity_id']==b['id'])
 assert workspace['a'].post('/api/leads/'+la['id']+'/transition',json={'version':1,'action':'confirm_strong','reason':'test'}).status_code==403
 for lead in [la,lb]:
  r=sup.post('/api/leads/'+lead['id']+'/transition',json={'version':1,'action':'confirm_strong','reason':'Verified conversation'});assert r.status_code==200,r.text
 first=sup.post('/api/leads/'+la['id']+'/transition',json={'version':2,'action':'qualify','reason':'Qualified'});assert first.status_code==200,first.text
 second=sup.post('/api/leads/'+lb['id']+'/transition',json={'version':2,'action':'qualify','reason':'Competing owner'});assert second.status_code==409,second.text

def test_meetings_hidden(workspace):
 c=workspace['a'];assert c.get('/api/meetings').status_code==403
 assert c.post('/api/meetings',json={'title':'Unauthorized'}).status_code==403

def test_scoring_ladder_and_no_double_touch():
 base={'called':True,'connected':False,'text_sent':False,'email_sent':False,'first_committed_at':1}
 assert app.stage_quality(base)[1]==10
 base['connected']=True;assert app.stage_quality(base)[1]==20
 base.update(text_sent=True,email_sent=True);assert app.stage_quality(base)[1]==30
 assert app.stage_quality(base,'strong_follow_up_confirmed')[1]==50
 assert app.stage_quality(base,'signed_up')[1]==100
 assert app.calc_metrics(0,0,0,170,.6)['band']=='No activity'

def test_dates_and_phone_refinement(workspace):
 r=workspace['a'].post('/api/refine',json={'rows':[{'provider_name':"O'Neil ENT, LLC",'phone':'+1 (202) 555-0199','email':' Name@EXAMPLE.COM '}]});assert r.status_code==200
 v=r.json()['items'][0]['refined'];assert v['provider_name']=="O'Neil ENT, LLC" and v['email']=='Name@example.com'
 clocks=workspace['a'].get('/api/clocks').json();assert len(clocks['items'])==8
 assert next(x for x in clocks['items'] if x['zone']=='Asia/Karachi')['pakistan_difference_hours']==0
 assert app.iso_date('10/14/2026')=='2026-10-14'
 assert app.iso_date(46309)=='2026-10-14'

def test_supervisor_metadata_permission_and_scope(workspace):
 c=workspace['supervisor'];r=c.get('/api/config');assert '"projects"' not in r.text
 users=c.get('/api/users').json()['items'];assert all(x['id']!=workspace['admin'].get('/api/auth/me').json()['user']['id'] for x in users)
 assert c.post('/api/users',json={}).status_code==403

def test_csrf_origin_and_host(workspace):
 c=TestClient(app.app)
 assert c.get('/api/health',headers={'Host':'attacker.example'}).status_code==403
 assert c.post('/api/auth/login',json={},headers={'Origin':'https://attacker.example'}).status_code==403
 c.cookies.update(workspace['a'].cookies)
 assert c.post('/api/activities',json={'provider_name':'No CSRF'}).status_code==403

def test_catalog_and_dashboard(workspace):
 settings=workspace['admin'].get('/api/settings').json();assert len(settings['domains'])==9 and len(settings['projects'])==28
 assert all(not x['owner_name'] for x in settings['projects'])
 r=workspace['a'].get('/api/dashboard');assert r.status_code==200,r.text
 assert r.json()['metrics']['attempts']>=1
 assert 'domain_id' not in r.text and 'project_id' not in r.text
