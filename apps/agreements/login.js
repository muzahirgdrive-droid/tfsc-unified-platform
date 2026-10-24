(() => {
 const $=id=>document.getElementById(id);let setup=false,change=false,csrf='';
 // Return to a same-site page (`?next=/reviews`) with its #fragment (a CRM handoff code); anything else goes home.
 const target=()=>{const n=new URLSearchParams(location.search).get('next')||'';return /^\/[A-Za-z0-9_\-\/]*$/.test(n)&&!n.startsWith('//')?n+location.hash:'/';};
 const error=message=>{$('login-error').textContent=message;$('login-error').hidden=false;};
 async function post(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
 function changeMode(){change=true;$('login-title').textContent='Set your password';$('login-description').textContent='Replace your temporary password to continue.';$('new-password-field').hidden=false;$('new-password').required=true;$('email').disabled=true;$('login-submit').textContent='Save password';}
 $('login-form').addEventListener('submit',async e=>{e.preventDefault();$('login-submit').disabled=true;$('login-error').hidden=true;try{
   let d;if(change)d=await post('/api/auth/password',{current_password:$('password').value,password:$('new-password').value});
   else d=await post(setup?'/api/auth/setup':'/api/auth/login',{email:$('email').value,password:$('password').value,name:$('account-name').value,setup_key:$('setup-key').value});
   csrf=d.csrf;if(d.user.reset_required){changeMode();}else location.href=target();
 }catch(e){error(e.message);}finally{$('login-submit').disabled=false;}});
 // Company SSO (Keycloak) when configured, never during first setup; it returns to the same target, handoff code included.
 const sso=()=>fetch('/api/auth/sso/status').then(r=>r.json()).then(d=>{if(d.enabled){$('login-sso').href='/api/auth/sso/login?next='+encodeURIComponent(target());$('login-sso').hidden=false;}}).catch(()=>{});
 fetch('/api/public/workspace').then(r=>r.json()).then(w=>{if(w.help_email){$('login-help-email').textContent=w.help_email;$('login-help-email').href='mailto:'+w.help_email;}$('login-copyright').textContent=`© ${w.year} ${w.copyright_holder}. All rights reserved.`;}).catch(()=>{});
 fetch('/api/auth/status').then(r=>r.json()).then(d=>{setup=d.setup_required;if(setup){$('setup-fields').hidden=false;$('setup-key').required=true;$('account-name').required=true;$('login-title').textContent='Create your workspace';$('login-description').textContent='Set up the first Super Admin account.';$('login-submit').textContent='Create Super Admin';$('password').autocomplete='new-password';}else sso();}).catch(()=>error('The application is not responding. Please try again in a moment.'));
})();
