(()=>{'use strict';let csrf='';let me;const $=id=>document.getElementById(id);
const LABELS={submitted:'Waiting for review',approved:'Approved',changes_requested:'Changes requested',queued:'Generating documents',issued:'Issued',superseded:'Superseded'};
async function api(path,data,headers={}){const r=await fetch(path,{method:data?'POST':'GET',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,...headers},body:data?JSON.stringify(data):undefined});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
function say(text){$('status').textContent=text;$('error').textContent='';}
function err(e){$('error').textContent=e.message;$('status').textContent='';}
function el(tag,text,cls){const e=document.createElement(tag);if(text)e.textContent=text;if(cls)e.className=cls;return e;}
function button(text,cls,fn){const b=el('button',text,'button '+cls);b.type='button';b.onclick=()=>{b.disabled=true;Promise.resolve(fn()).catch(err).finally(()=>{b.disabled=false;});};return b;}
function decision(r){
  // Inline reason instead of window.prompt(): the reviewer's reason is part of the audit record (required by the server).
  const box=el('div','','review-decision'),field=el('div','','field'),label=el('label','Reason for your decision'),text=el('textarea'),hint=el('p','Recorded in the audit log with your decision.','field-note'),row=el('div','','review-buttons');
  text.id='reason-'+r.id;text.maxLength=2000;text.rows=3;label.htmlFor=text.id;hint.id=text.id+'-hint';text.setAttribute('aria-describedby',hint.id);
  field.append(label,text,hint);
  const decide=d=>async()=>{const reason=text.value.trim();if(!reason){text.setAttribute('aria-invalid','true');text.focus();throw Error('Enter a reason before recording a decision.');}
    text.removeAttribute('aria-invalid');await api('/api/reviews/'+r.id+'/decision',{decision:d,version:r.version,reason});say(d==='approve'?'Revision approved.':'Changes requested.');await refresh();};
  row.append(button('Approve exact revision','button-primary',decide('approve')),button('Request changes','button-quiet',decide('reject')));
  box.append(field,row);return box;}
async function refresh(){const result=await api('/api/reviews');const list=$('reviews');list.replaceChildren();
  if(!result.reviews.length){list.append(el('p','No reviews yet. Submit a saved draft to start one.','empty-state'));return;}
  for(const r of result.reviews){const box=el('article','','review-card');box.dataset.state=r.state;
    const head=el('div','','review-card-head');head.append(el('h3',r.payload.client_name+' · '+r.state),el('span',LABELS[r.state]||r.state,'review-badge'));
    box.append(head,el('p',r.entity+' · '+new Date(r.created*1000).toLocaleString(),'review-meta'));
    const hash=el('p','Approved content hash: ','review-hash');hash.append(el('code',r.payload_hash));box.append(hash);
    if(r.reason)box.append(el('p','Reviewer’s reason: '+r.reason,'review-reason'));
    const details=el('details');details.append(el('summary','Review exact draft details'),el('pre',JSON.stringify(r.payload,null,2)));box.append(details);
    if(r.state==='submitted'&&me.role!=='user'&&me.id!==r.owner_id)box.append(decision(r));
    else if(r.state==='submitted')box.append(el('p','Waiting for an independent reviewer. You cannot approve your own draft.','review-note'));
    if(r.state==='approved'&&me.id===r.owner_id){const row=el('div','','review-buttons');row.append(button('Generate approved documents','button-primary',async()=>{await api('/api/reviews/'+r.id+'/generate',{},{'Idempotency-Key':crypto.randomUUID()});say('Documents queued for generation.');await refresh();}));box.append(row);}
    if(r.state==='changes_requested'&&me.id===r.owner_id)box.append(el('p','Update the draft in the builder, then submit it again.','review-note'));
    if(r.state==='issued')box.append(el('p','Released documents are available in the client contract history.','review-note'));
    list.append(box);}}
async function start(){const auth=await api('/api/auth/me');csrf=auth.csrf;me=auth.user;$('mapping').hidden=me.role!=='super_admin';const list=await api('/api/clients');for(const c of list.clients){const o=el('option',c.client_name);o.value=c.id;$('clients').append(o);}await refresh();const m=location.hash.match(/^#redeem=([A-Za-z0-9_-]{20,100})$/);if(m){history.replaceState(null,'',location.pathname);$('code').value=m[1];$('redeem').click();}}
$('redeem').onclick=()=>api('/api/handoffs/redeem',{token:$('code').value.trim()}).then(r=>{$('code').value='';$('error').textContent='';$('redeemed').textContent='Received '+r.client_name+'. Open the saved client in the builder to finish services and pricing.';const a=el('a',' Open client draft');a.href='/?client='+encodeURIComponent(r.client_id);$('redeemed').append(a);}).catch(err);
$('submit').onclick=()=>api('/api/reviews/submit',{client_id:$('clients').value}).then(()=>{say('Draft submitted for independent review.');return refresh();}).catch(err);
$('refresh').onclick=()=>refresh().catch(err);
$('link').onclick=()=>api('/api/integrations/crm/actors',{crm_actor_id:$('crm-actor').value.trim(),tfsc_user_id:$('tfsc-user').value.trim()}).then(()=>say('Account mapping saved.')).catch(err);
start().catch(err);})();
