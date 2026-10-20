"""Isolated synthetic fixtures; provider requests are mocked, never sent."""
import io, json, time, base64
from urllib.parse import urlsplit, parse_qs
from datetime import date, timedelta
from unittest.mock import patch
import httpx
from sqlalchemy import select
from test_crm_core import activity, app, db, PASSWORD

def test_group_leader_followups_do_not_unlock_calls(workspace):
    a,b,sup=workspace['a'],workspace['b'],workspace['supervisor']
    team=sup.post('/api/teams',json={'name':'Synthetic group','member_ids':[workspace['a_id'],workspace['b_id']],'leader_id':workspace['a_id']})
    assert team.status_code==200,team.text
    record=activity(b,phone='2025550150',called=True,next_followup=app.today())
    with db.engine.begin() as c:c.execute(db.activities.update().where(db.activities.c.id==record['id']).values(edit_deadline=time.time()-1))
    lead=next(r for r in a.get('/api/followups').json()['items'] if r['activity_id']==record['id'])
    saved=a.post('/api/leads/'+lead['id']+'/followup-task',json={'version':0,'due_date':app.today(),'notes':'Group leader arranged the next follow-up.'})
    assert saved.status_code==200,saved.text
    assert a.patch('/api/activities/'+record['id'],json={'version':1,'remarks':'Cannot edit teammate call'}).status_code==404
    assert a.post('/api/leads/'+lead['id']+'/transition',json={'version':1,'action':'confirm_strong','reason':'No permission'}).status_code in (403,404)
    assert workspace['other'].get('/api/leads/'+lead['id']+'/followup-task').status_code==404
    assert a.post('/api/leads/'+lead['id']+'/followup-task',json={'version':0,'due_date':app.today(),'notes':'Stale'}).status_code==409

def test_target_versions_and_idle_roster(workspace):
    c=workspace['admin'];r=c.get('/api/dashboard').json()
    names={x['name'] for x in r['leaderboard']}
    assert 'other' in names  # Idle agents remain in the team target denominator.
    start=app.today();before=c.get('/api/dashboard',params={'from':start,'to':start}).json()['metrics']['target']
    update=c.patch('/api/settings',json={'call_target':250});assert update.status_code==200
    assert update.json()['targets_effective_date']>start
    after=c.get('/api/dashboard',params={'from':start,'to':start}).json()['metrics']['target'];assert after==before
    with db.engine.connect() as conn:
        future=(date.fromisoformat(start)+timedelta(days=7)).isoformat()
        assert conn.execute(select(app.reporting.versions.c.call_target).order_by(app.reporting.versions.c.version.desc())).first()[0]==250

def test_xlsx_preview_and_photo_scope(workspace):
    from openpyxl import Workbook
    from PIL import Image
    book=Workbook();book.active.title='Dashboard';book.active.append(['Not data'])
    sheet=book.create_sheet('Synthetic Agent');sheet.append(['Name','Phone','Email','Date']);sheet.append(['Example Clinic','2025550120','Example@EXAMPLE.COM','10/14/2026'])
    out=io.BytesIO();book.save(out)
    c=workspace['a'];r=c.post('/api/import/file-preview',files={'file':('sample.xlsx',out.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert r.status_code==200,r.text
    assert r.json()['source_sheet']=='Synthetic Agent' and 'Example Clinic' in r.json()['tsv']
    image=io.BytesIO();Image.new('RGB',(40,50),'blue').save(image,format='PNG')
    r=c.post('/api/profile/photo',json={'image':base64.b64encode(image.getvalue()).decode()});assert r.status_code==200
    assert c.get('/api/profile/photo/'+workspace['a_id']).headers['content-type']=='image/jpeg'
    assert workspace['other'].get('/api/profile/photo/'+workspace['a_id']).status_code==404
    assert c.post('/api/profile/photo',json={'image':'not an image'}).status_code==400

def test_oidc_requires_bound_state_and_explicit_subject(workspace,monkeypatch):
    from authlib.jose import JsonWebToken, JsonWebKey
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization
    issuer='https://identity.example.test';monkeypatch.setenv('CRM_OIDC_ISSUER',issuer);monkeypatch.setenv('CRM_OIDC_CLIENT_ID','synthetic-client');monkeypatch.setenv('CRM_OIDC_CLIENT_SECRET','synthetic-secret')
    pem=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
    key=JsonWebKey.import_key(pem,{'kid':'synthetic'});jwks={'keys':[key.as_dict(is_private=False)]}
    c=workspace['a'];admin=workspace['admin'];state={}
    def response(url,obj):return httpx.Response(200,json=obj,request=httpx.Request('GET',url))
    def get(url,**kw):return response(url,jwks if url.endswith('/keys') else {'issuer':issuer,'authorization_endpoint':issuer+'/authorize','token_endpoint':issuer+'/token','jwks_uri':issuer+'/keys'})
    def post(url,**kw):
        token=JsonWebToken(['RS256']).encode({'alg':'RS256','kid':'synthetic'},{'iss':issuer,'aud':'synthetic-client','sub':'immutable-subject','exp':time.time()+300,'iat':time.time(),'nonce':state['nonce']},key).decode()
        return response(url,{'id_token':token})
    with patch('oidc_support.httpx.get',side_effect=get),patch('oidc_support.httpx.post',side_effect=post):
        def start():
            r=c.get('/api/auth/sso/login',follow_redirects=False);assert r.status_code==307
            state.update({k:v[0] for k,v in parse_qs(urlsplit(r.headers['location']).query).items()});return state['state']
        raw=start();assert c.get('/api/auth/sso/callback?state=wrong&code=code').status_code==401
        assert c.get('/api/auth/sso/callback',params={'state':raw,'code':'code'}).status_code==403
        assert admin.post('/api/auth/sso/accounts',json={'subject':'immutable-subject','user_id':workspace['a_id']}).status_code==200
        raw=start();r=c.get('/api/auth/sso/callback',params={'state':raw,'code':'code'},follow_redirects=False);assert r.status_code==303,r.text
        assert c.get('/api/auth/sso/callback',params={'state':raw,'code':'code'}).status_code==401
        # A successful SSO session rotates the CSRF token.
        c.headers['X-CSRF-Token']=c.get('/api/auth/me').json()['csrf']

def test_google_idempotent_meeting_no_invitations(workspace,monkeypatch):
    import google_calendar as google
    monkeypatch.setenv('CRM_GOOGLE_CLIENT_ID','synthetic-client');monkeypatch.setenv('CRM_GOOGLE_CLIENT_SECRET','synthetic-secret')
    c=workspace['supervisor'];activity_row=activity(workspace['a'],phone='2025550149',called=True,next_followup=app.today())
    lead=next(r for r in c.get('/api/leads').json()['items'] if r['activity_id']==activity_row['id'])
    meeting=c.post('/api/meetings',json={'lead_id':lead['id'],'starts_at':app.today()+'T10:00:00-07:00','title':'Synthetic meeting'});assert meeting.status_code==200
    mid=meeting.json()['id']
    with db.engine.begin() as conn:conn.execute(google.connections.insert().values(user_id=workspace['sup_id'],encrypted_token=google.cipher().encrypt(json.dumps({'access_token':'synthetic','expires_at':time.time()+3600}).encode()).decode(),updated=time.time()))
    stored={};calls=[]
    def get(url,**kw):return httpx.Response(200 if stored else 404,json=stored or {},request=httpx.Request('GET',url))
    def post(url,**kw):
        calls.append(kw);stored.update(kw['json']);stored['hangoutLink']='https://meet.google.com/aaa-bbbb-ccc'
        return httpx.Response(200,json=stored,request=httpx.Request('POST',url))
    with patch.object(google.httpx,'get',side_effect=get),patch.object(google.httpx,'post',side_effect=post):
        for _ in range(2):
            r=c.post('/api/meetings/'+mid+'/google');assert r.status_code==200,r.text;assert r.json()['invitations_sent'] is False
        assert len(calls)==1 and calls[0]['params']['sendUpdates']=='none' and 'attendees' not in calls[0]['json']
        assert c.post('/api/meetings/'+mid+'/sync').status_code==200
    assert workspace['a'].get('/api/integrations/google/status').status_code==403
    assert workspace['other'].post('/api/clients/'+lead['id']+'/agreements/sync').status_code in (403,503)
