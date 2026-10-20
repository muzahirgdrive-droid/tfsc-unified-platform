"""Optional Google Calendar/Meet adapter. Never sends invitations implicitly."""
import os,time,secrets,hashlib,base64,json
from urllib.parse import urlencode
from datetime import datetime,timedelta,timezone
from sqlalchemy import Table,Column,String,Text,Float,ForeignKey,select,delete
from fastapi import HTTPException,Request
from fastapi.responses import RedirectResponse
from cryptography.fernet import Fernet,InvalidToken
import httpx
import crm_db as db
import crm_security as sec
states=Table('crm_google_oauth_states',db.meta,Column('state',String(64),primary_key=True),Column('user_id',String(32),ForeignKey('crm_users.id'),nullable=False),Column('verifier',String(100),nullable=False),Column('expires',Float,nullable=False))
connections=Table('crm_google_connections',db.meta,Column('user_id',String(32),ForeignKey('crm_users.id'),primary_key=True),Column('encrypted_token',Text,nullable=False),Column('updated',Float,nullable=False))
def configured():return bool(os.getenv('CRM_GOOGLE_CLIENT_ID') and os.getenv('CRM_GOOGLE_CLIENT_SECRET') and (db.LOCAL or os.getenv('CRM_ENCRYPTION_KEY')))
def cipher():
 key=os.getenv('CRM_ENCRYPTION_KEY')
 if not key and db.LOCAL:
  path=db.DATA/'integration-key.key'
  if not path.exists():
   try:
    with path.open('xb') as f:f.write(Fernet.generate_key())
   except FileExistsError:pass
  key=path.read_bytes()
 if not key:raise HTTPException(503,'Configure the integration encryption key.')
 try:return Fernet(key.encode() if isinstance(key,str) else key)
 except ValueError:raise HTTPException(503,'The integration encryption key is invalid.')
def token(user_id):
 with db.engine.connect() as c:r=c.execute(select(connections).where(connections.c.user_id==user_id)).mappings().first()
 if not r:raise HTTPException(409,'Connect your Google organizer account first.')
 try:t=json.loads(cipher().decrypt(r['encrypted_token'].encode()))
 except (InvalidToken,ValueError):raise HTTPException(503,'The saved Google connection could not be decrypted.')
 if t.get('expires_at',0)<time.time()+60:
  if not t.get('refresh_token'):raise HTTPException(409,'Reconnect Google to refresh organizer permission.')
  try:
   response=httpx.post('https://oauth2.googleapis.com/token',data={'grant_type':'refresh_token','refresh_token':t['refresh_token'],'client_id':os.getenv('CRM_GOOGLE_CLIENT_ID'),'client_secret':os.getenv('CRM_GOOGLE_CLIENT_SECRET')},timeout=15);response.raise_for_status();t.update(response.json());t['expires_at']=time.time()+t.get('expires_in',3600)
  except httpx.HTTPError:raise HTTPException(503,'Google access could not be refreshed. Reconnect the account if permission was revoked.')
  with db.engine.begin() as c:c.execute(connections.update().where(connections.c.user_id==user_id).values(encrypted_token=cipher().encrypt(json.dumps(t).encode()).decode(),updated=time.time()))
 return t['access_token']
def install(app,origin,get_record,clean):
 @app.get('/api/integrations/google/start')
 def start(request:Request):
  if not configured():raise HTTPException(503,'Google OAuth is not configured on this server.')
  with db.engine.begin() as c:
   a=sec.actor(request,c);sec.require(a);state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(48);c.execute(delete(states).where(states.c.expires<time.time()));c.execute(states.insert().values(state=sec.digest(state),user_id=a['id'],verifier=verifier,expires=time.time()+600))
  args={'client_id':os.getenv('CRM_GOOGLE_CLIENT_ID'),'redirect_uri':origin+'/api/integrations/google/callback','response_type':'code','scope':'https://www.googleapis.com/auth/calendar.events.owned','access_type':'offline','prompt':'consent','state':state,'code_challenge':base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode(),'code_challenge_method':'S256'}
  return RedirectResponse('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(args))
 @app.get('/api/integrations/google/callback')
 def callback(request:Request):
  with db.engine.begin() as c:
   a=sec.actor(request,c);sec.require(a);state=sec.digest(request.query_params.get('state',''));r=c.execute(select(states).where(states.c.state==state,states.c.user_id==a['id'],states.c.expires>time.time())).mappings().first()
   if not r or c.execute(delete(states).where(states.c.state==state)).rowcount!=1:raise HTTPException(401,'Google authorization expired or did not match this account.')
  try:
   response=httpx.post('https://oauth2.googleapis.com/token',data={'client_id':os.getenv('CRM_GOOGLE_CLIENT_ID'),'client_secret':os.getenv('CRM_GOOGLE_CLIENT_SECRET'),'grant_type':'authorization_code','code':request.query_params.get('code',''),'code_verifier':r['verifier'],'redirect_uri':origin+'/api/integrations/google/callback'},timeout=15);response.raise_for_status();t=response.json();t['expires_at']=time.time()+t.get('expires_in',3600)
  except httpx.HTTPError:raise HTTPException(502,'Google authorization could not be completed.')
  with db.engine.begin() as c:
   current=sec.actor(request,c);sec.require(current);c.execute(delete(connections).where(connections.c.user_id==a['id']));c.execute(connections.insert().values(user_id=a['id'],encrypted_token=cipher().encrypt(json.dumps(t).encode()).decode(),updated=time.time()));db.log(c,a,'google.connected')
  return RedirectResponse('/',status_code=303)
 @app.get('/api/integrations/google/status')
 def status(request:Request):
  with db.engine.connect() as c:
   a=sec.actor(request,c);sec.require(a);return {'configured':configured(),'connected':bool(c.execute(select(connections.c.user_id).where(connections.c.user_id==a['id'])).first())}
 @app.post('/api/integrations/google/disconnect')
 def disconnect(request:Request):
  with db.engine.begin() as c:
   a=sec.actor(request,c);sec.require(a);c.execute(delete(connections).where(connections.c.user_id==a['id']));db.log(c,a,'google.disconnected');return {'ok':True}
 @app.post('/api/meetings/{id}/google')
 def schedule(id:str,request:Request):
  if not configured():raise HTTPException(503,'Google Calendar is not configured.')
  with db.engine.connect() as c:a=sec.actor(request,c);sec.require(a);meeting=get_record(c,a,db.meetings,id)
  if meeting['provider_id'] and not meeting['provider_id'].startswith(a['id']+':'):raise HTTPException(403,'Only the connected organizer can update this Google meeting.')
  # A deterministic UUID-derived Google ID prevents retry-created duplicates. No attendees means no outbound invitations.
  access=token(a['id']);event_id='tfsc'+meeting['id'];headers={'Authorization':'Bearer '+access};base='https://www.googleapis.com/calendar/v3/calendars/primary/events'
  start=datetime.fromisoformat(meeting['starts_at']);end=start+timedelta(minutes=30)
  event={'id':event_id,'summary':meeting['title'],'start':{'dateTime':start.isoformat(),'timeZone':meeting['timezone']},'end':{'dateTime':end.isoformat(),'timeZone':meeting['timezone']},'visibility':'private','guestsCanInviteOthers':False,'guestsCanSeeOtherGuests':False,'conferenceData':{'createRequest':{'requestId':meeting['id'],'conferenceSolutionKey':{'type':'hangoutsMeet'}}},'extendedProperties':{'private':{'crm_meeting_id':meeting['id']}}}
  try:
   result=httpx.get(base+'/'+event_id,headers=headers,timeout=15)
   if result.status_code==404:result=httpx.post(base,headers=headers,params={'conferenceDataVersion':1,'sendUpdates':'none'},json=event,timeout=15)
   if result.status_code==409:result=httpx.get(base+'/'+event_id,headers=headers,timeout=15)
   result.raise_for_status();remote=result.json()
  except httpx.HTTPError:raise HTTPException(502,'Google could not save this meeting. Retry safely or review your organizer connection.')
  link=remote.get('hangoutLink');status='google_scheduled' if link else 'google_conference_pending'
  with db.engine.begin() as c:
   current=sec.actor(request,c);sec.require(current);get_record(c,current,db.meetings,id)
   c.execute(db.meetings.update().where(db.meetings.c.id==id).values(provider_id=a['id']+':'+event_id,join_url=link,status=status,ends_at=end.isoformat()));db.log(c,a,'meeting.google_created',id,{'status':status},meeting['owner_id'])
  return {'id':id,'status':status,'join_url':link,'invitations_sent':False}
 @app.post('/api/meetings/{id}/sync')
 def sync(id:str,request:Request):
  with db.engine.connect() as c:a=sec.actor(request,c);sec.require(a);meeting=get_record(c,a,db.meetings,id)
  if not meeting['provider_id']:raise HTTPException(409,'This meeting has not been created through Google.')
  if not meeting['provider_id'].startswith(a['id']+':'):raise HTTPException(403,'Only the connected organizer can synchronize this meeting.')
  try:
   r=httpx.get('https://www.googleapis.com/calendar/v3/calendars/primary/events/'+meeting['provider_id'].split(':',1)[1],headers={'Authorization':'Bearer '+token(a['id'])},timeout=15);r.raise_for_status();remote=r.json()
  except httpx.HTTPError:raise HTTPException(502,'Meeting synchronization failed. Verify the organizer account.')
  v={'status':'cancelled' if remote.get('status')=='cancelled' else 'google_scheduled' if remote.get('hangoutLink') else 'google_conference_pending','join_url':remote.get('hangoutLink')}
  if remote.get('start',{}).get('dateTime'):v['starts_at']=remote['start']['dateTime']
  if remote.get('end',{}).get('dateTime'):v['ends_at']=remote['end']['dateTime']
  with db.engine.begin() as c:
   current=sec.actor(request,c);sec.require(current);get_record(c,current,db.meetings,id);c.execute(db.meetings.update().where(db.meetings.c.id==id).values(**v));db.log(c,current,'meeting.synchronized',id,{'status':v['status']},meeting['owner_id'])
  return {'id':id,**v}
