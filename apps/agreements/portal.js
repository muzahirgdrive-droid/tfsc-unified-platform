/* App shell: navigation and routing (#/home, #/agreements, #/clients, …), account menu, help, copyright, users and folder browsing. */
(() => {
 const $=id=>document.getElementById(id);let config,user,usersPage=0,editing=null;
 const elem=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
 const csrf=()=>document.querySelector('meta[name="csrf-token"]').content;
 async function api(path,body,signal){const r=await fetch(path,{method:body===undefined?'GET':'POST',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf()},body:body===undefined?undefined:JSON.stringify(body),signal});const d=await r.json();if(r.status===401){location.href='/login';throw Error('Please sign in again.');}if(!r.ok)throw Error(d.error||'Request failed');return d;}
 function error(e){$('app-error').textContent=e.message;$('app-error').hidden=false;}
 function action(text,fn,cls='button button-quiet'){const b=elem('button',text,cls);b.type='button';b.addEventListener('click',()=>Promise.resolve().then(fn).catch(error));return b;}
 // ------------------------------------------------------------------ views and navigation
 const SECTIONS={home:'home-view',builder:'builder-view',history:'history-view',users:'users-view',esign:'esign-view',mailboxes:'mailbox-view',reports:'reports-view',activity:'activity-view',admin:'admin-view'};
 const NAV={home:'home',builder:'create',history:'clients',users:'admin',esign:'agreements',mailboxes:'admin',reports:'reports',activity:'activity',admin:'admin'};
 const TITLES={home:'Home',builder:'Create agreement',history:'Clients',users:'Users & permissions',esign:'Agreements',mailboxes:'Company mailboxes',reports:'Reports',activity:'Activity log',admin:'Admin'};
 function closeMenus(){document.querySelectorAll('.menu-list').forEach(m=>{m.hidden=true;});document.querySelectorAll('[aria-haspopup]').forEach(b=>b.setAttribute('aria-expanded','false'));$('topnav').classList.remove('open');$('nav-toggle').setAttribute('aria-expanded','false');}
 function view(mode,nav){if(mode!=='esign'&&document.body.dataset.view==='esign'&&window.esignLeave&&!window.esignLeave())return false;
  document.body.dataset.view=mode;document.title=`${TITLES[mode]||'TFSC Agreements'} · TFSC Agreements`;
  for(const [key,id] of Object.entries(SECTIONS))$(id).hidden=key!==mode;
  if(mode==='builder'){$('contract-form').hidden=false;if(window.studioActive)window.studioNavigate(window.studioActive());}
  const current=nav||NAV[mode];document.querySelectorAll('[data-nav]').forEach(a=>{if(a.dataset.nav===current)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  closeMenus();$('app-error').hidden=true;window.scrollTo({top:0});return true;}
 window.portalView=view;
 // Location hash mirrors the screen, so Back/Forward and bookmarks work. mark() records a screen without re-opening it.
 let quiet=null;
 window.portalMark=hash=>{if(location.hash!==hash){quiet=hash;location.hash=hash;}};
 function route(){const hash=location.hash||'#/home';const [,top='home',a='',b='']=hash.split('/');
  const go=(mode,nav,fn)=>{if(view(mode,nav))Promise.resolve().then(fn).catch(error);};
  if(top==='agreements')return go('esign','agreements',()=>window.esignShowList?.(a||'action'));
  if(top==='templates')return go('esign','templates',()=>window.esignShowList?.('templates'));
  if(top==='envelope'&&/^[a-f0-9]{32}$/.test(a))return go('esign','agreements',()=>window.esignOpen?.(a));
  if(top==='send')return go('esign','agreements',()=>window.esignStart?.());
  if(top==='clients')return go('history','clients',()=>window.clientsShow?.(/^[a-f0-9]{32}$/.test(a)?a:undefined));
  if(top==='create')return go('builder','create');
  if(top==='reports')return go('reports','reports',()=>window.reportsShow?.(a||'insights'));
  if(top==='activity'&&user.role!=='user')return go('activity','activity',()=>window.activityShow?.());
  if(top==='admin'&&user.role!=='user'){
   if(a==='users')return go('users','admin',loadUsers);
   if(user.role==='super_admin'){if(a==='mailboxes')return go('mailboxes','admin',()=>window.esignShowMailboxes?.());if(['companies','email-trust','workspace'].includes(a))return go('admin','admin',()=>window.adminShow?.(a));}
   return go('users','admin',loadUsers);}
  go('home','home',()=>window.homeShow?.());void b;}
 window.addEventListener('hashchange',()=>{if(quiet===location.hash){quiet=null;return;}quiet=null;if(config)route();});
 window.portalRoute=hash=>{if(location.hash===hash)route();else location.hash=hash;};
 window.showBuilder=()=>window.portalRoute('#/create');
 document.querySelectorAll('[data-step]').forEach(b=>b.addEventListener('click',()=>{if(document.body.dataset.view!=='builder')window.portalRoute('#/create');}));
 // Menus: the admin menu, the account menu and the mobile navigation.
 document.querySelectorAll('[aria-haspopup="true"]').forEach(button=>button.addEventListener('click',event=>{event.stopPropagation();const list=button.nextElementSibling;const open=list.hidden;closeMenus();list.hidden=!open;button.setAttribute('aria-expanded',String(open));if(open)list.querySelector('a,button')?.focus();}));
 document.addEventListener('click',event=>{if(!event.target.closest('.menu-list'))closeMenus();});
 document.addEventListener('keydown',event=>{if(event.key==='Escape')closeMenus();});
 document.querySelectorAll('.menu-list a').forEach(a=>a.addEventListener('click',closeMenus));
 $('nav-toggle').addEventListener('click',event=>{event.stopPropagation();const open=!$('topnav').classList.contains('open');closeMenus();$('topnav').classList.toggle('open',open);$('nav-toggle').setAttribute('aria-expanded',String(open));});
 $('footer-help').onclick=()=>$('help-dialog').showModal();
 const ROLE={user:'User',admin:'Admin',super_admin:'Super Admin'};
 window.portalReady=c=>{config=c;user=c.user;const ws=c.workspace||{};
  $('account-name').textContent=user.name;$('account-role').textContent=ROLE[user.role]||user.role;$('account-email').textContent=user.email;
  $('user-avatar').textContent=user.name.split(/\s+/).filter(Boolean).slice(0,2).map(x=>x[0].toUpperCase()).join('')||'·';
  $('nav-admin').hidden=user.role==='user';$('nav-activity').hidden=user.role==='user';
  document.querySelectorAll('#nav-admin [data-super]').forEach(a=>{a.hidden=user.role!=='super_admin';});
  $('signature-settings-link').hidden=user.role!=='super_admin';
  if(ws.help_email){$('help-email-link').textContent=ws.help_email;$('help-email-link').href='mailto:'+ws.help_email;$('help-email-button').href='mailto:'+ws.help_email+'?subject='+encodeURIComponent((ws.portal_name||'TFSC Agreements')+' support');}
  $('copyright').textContent=`© ${ws.year||new Date().getFullYear()} ${ws.copyright_holder||'TFSC Agreements'}. All rights reserved.`;
  if(c.crm&&!$('nav-review')){const review=elem('a','Agreement review');review.href='/reviews';review.id='nav-review';$('topnav').insertBefore(review,$('nav-admin'));}
  window.clientsReady?.(c);window.esignReady?.(c);window.homeReady?.(c);window.reportsReady?.(c);window.adminReady?.(c);
  if(!c.local){$('folder-explanation').textContent='Choose a folder in managed server storage. Download completed documents to save a copy on your computer.';const note=document.querySelector('.privacy-note p');if(note)note.textContent='Agreements are kept in your secure workspace and its managed storage.';}
  const params=new URLSearchParams(location.search);const envelope=params.get('envelope');
  if(envelope&&/^[a-f0-9]{32}$/.test(envelope))history.replaceState(null,'','/#/envelope/'+envelope);
  else if(params.get('view')==='signatures')history.replaceState(null,'','/#/agreements');
  if(!params.get('client'))setTimeout(route);};
 $('logout').onclick=async()=>{try{await api('/api/auth/logout',{});location.href='/login';}catch(e){error(e);}};
 $('account-password').onclick=()=>{closeMenus();$('password-dialog').showModal();};
 $('password-form').onsubmit=async e=>{e.preventDefault();try{const d=await api('/api/auth/password',{current_password:$('current-password').value,password:$('account-new-password').value});document.querySelector('meta[name="csrf-token"]').content=d.csrf;$('password-form').reset();$('password-dialog').close();}catch(e){$('password-message').textContent=e.message;}};
 window.waitForGeneration=async(id,progress)=>{for(;;){const job=await api('/api/jobs/'+id);if(job.state==='succeeded')return job.result;if(job.state==='failed')throw Error(job.error||'Generation failed. See the client profile.');progress(job.state==='queued'?'Queued for generation…':'Creating Word & PDF…');await new Promise(r=>setTimeout(r,1500));}};
 // ------------------------------------------------------------------ output folder browser (builder step 5)
 let folderParent='',browseController;
 async function browse(path){if(browseController)browseController.abort();browseController=new AbortController();const controller=browseController,timer=setTimeout(()=>controller.abort(),8000);$('folder-status').textContent='Loading folders…';try{const d=await api('/api/folders',{path},controller.signal);folderParent=d.parent||'';$('folder-up').disabled=!folderParent;if(d.path)$('folder-path').value=d.path;$('folder-list').replaceChildren();for(const f of d.roots||[]){$('folder-list').append(action(f,()=>browse(f),'folder-entry'));}for(const f of d.entries||[]){$('folder-list').append(action('▱ '+f.name,()=>browse(f.path),'folder-entry'));}$('folder-status').textContent=d.truncated?'Showing the first 300 folders. Enter a more specific path.':d.path?'Select this folder, open a subfolder, or enter a new path.':'Choose a location.';}catch(e){if(controller===browseController)$('folder-status').textContent=e.name==='AbortError'?'This folder took too long to respond. Enter a path directly or choose another location.':e.message;}finally{clearTimeout(timer);}}
 window.chooseOutputFolder=()=>{$('folder-path').value=$('output_root').value;$('folder-dialog').showModal();browse($('output_root').value);};$('folder-go').onclick=()=>browse($('folder-path').value);$('folder-up').onclick=()=>browse(folderParent);$('folder-roots').onclick=()=>browse('');$('folder-path').onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();browse($('folder-path').value);}};$('folder-select').onclick=()=>{const value=$('folder-path').value.trim();if(!value){$('folder-status').textContent='Enter a folder path first.';return;}$('output_root').value=value;$('output_root').dispatchEvent(new Event('input',{bubbles:true}));$('folder-dialog').close();};$('folder-dialog').addEventListener('close',()=>browseController?.abort());
 // ------------------------------------------------------------------ users and permissions
 const chip=(text,tone)=>elem('span',text,'chip chip-'+tone);
 async function loadUsers(){const d=await api('/api/users?q='+encodeURIComponent($('users-search').value)+'&page='+usersPage);$('users-table').replaceChildren();for(const u of d.users){const row=elem('tr');const name=elem('td');const who=elem('div','','person');who.append(elem('span',u.name.split(/\s+/).slice(0,2).map(x=>x[0]||'').join('').toUpperCase(),'avatar avatar-sm'));const text=elem('div');text.append(elem('strong',u.name),elem('small',u.email));who.append(text);name.append(who);const role=elem('td');role.append(chip(ROLE[u.role]||u.role,u.role==='user'?'neutral':'info'));const status=elem('td');status.append(chip(u.active?'Active':'Disabled',u.active?'success':'muted'));row.append(name,role,elem('td',u.role==='super_admin'?'All companies':u.companies.join(', ')),status);const actions=elem('td','','row-actions');if(u.id!==user.id&&(user.role==='super_admin'||u.admin_id===user.id)){actions.append(action('Permissions',()=>editUser(u)),action(u.active?'Disable':'Enable',async()=>{await api('/api/users/'+u.id,{active:!u.active});await loadUsers();}),action('Reset password',async()=>{if(!confirm('Reset this user’s password and sign them out?'))return;const d=await api('/api/users/'+u.id,{reset_password:true});showSecret(u.email,d.temporary_password);}));}row.append(actions);$('users-table').append(row);}$('users-prev').disabled=usersPage===0;$('users-next').disabled=!d.has_more;$('users-page').textContent='Page '+(usersPage+1);}
 function permissions(selected=[]){$('company-permissions').replaceChildren();for(const company of config.entities){const label=elem('label'),input=elem('input');input.type='checkbox';input.value=company.name;input.checked=selected.includes(company.name);label.append(input,document.createTextNode(company.name));$('company-permissions').append(label);}}
 async function editUser(u){editing=u||null;$('user-form').reset();$('user-message').replaceChildren();$('user-save').hidden=false;$('user-dialog-title').textContent=u?'Company permissions':'Create user';$('new-user-fields').hidden=!!u;permissions(u?.companies||[]);$('user-name').required=!u;$('user-email').required=!u;$('user-role').replaceChildren();for(const role of user.role==='super_admin'?['user','admin','super_admin']:['user']){const option=elem('option',ROLE[role]);option.value=role;$('user-role').append(option);}$('manager-field').hidden=user.role!=='super_admin';$('user-manager').replaceChildren(new Option('No assigned Admin',''));if(user.role==='super_admin'&&!u){let page=0;for(;;){const d=await api('/api/admins?page='+page);for(const a of d.users)$('user-manager').append(new Option(a.name+' · '+a.email,a.id));if(!d.has_more)break;page++;}}$('user-dialog').showModal();}
 function showSecret(email,password){$('user-dialog-title').textContent='Temporary sign-in details';$('new-user-fields').hidden=true;$('company-permissions').replaceChildren();$('user-save').hidden=true;$('user-message').replaceChildren(elem('p',`Email: ${email}\nTemporary password: ${password}\n\nShare privately. The user must change this password at first sign-in.`, 'secret-result'));if(!$('user-dialog').open)$('user-dialog').showModal();}
 $('add-user').onclick=()=>editUser(null).catch(error);$('user-form').onsubmit=async e=>{e.preventDefault();$('user-save').disabled=true;try{const companies=[...$('company-permissions').querySelectorAll('input:checked')].map(i=>i.value);const d=await api(editing?'/api/users/'+editing.id:'/api/users',editing?{companies}:{name:$('user-name').value,email:$('user-email').value,role:$('user-role').value,admin_id:$('user-manager').value,companies});if(d.temporary_password)showSecret(d.user.email,d.temporary_password);else $('user-dialog').close();await loadUsers();}catch(e){$('user-message').textContent=e.message;}finally{$('user-save').disabled=false;}};
 $('users-refresh').onclick=()=>{usersPage=0;loadUsers().catch(error);};$('users-search').onkeydown=e=>{if(e.key==='Enter')$('users-refresh').click();};$('users-prev').onclick=()=>{usersPage--;loadUsers().catch(error);};$('users-next').onclick=()=>{usersPage++;loadUsers().catch(error);};
})();
