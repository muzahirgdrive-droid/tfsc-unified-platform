(() => {
 const $=id=>document.getElementById(id);let setup=false,change=false,csrf='';
 const error=message=>{$('login-error').textContent=message;$('login-error').hidden=false;};
 async function post(url,data){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const d=await r.json();if(!r.ok)throw Error(d.error||'Request failed');return d;}
 function changeMode(){change=true;$('login-title').textContent='Set your password.';$('login-description').textContent='Replace your temporary password to continue.';$('new-password-field').hidden=false;$('new-password').required=true;$('email').disabled=true;$('login-submit').textContent='Save password';}
 $('login-form').addEventListener('submit',async e=>{e.preventDefault();$('login-submit').disabled=true;$('login-error').hidden=true;try{
   let d;if(change)d=await post('/api/auth/password',{current_password:$('password').value,password:$('new-password').value});
   else d=await post(setup?'/api/auth/setup':'/api/auth/login',{email:$('email').value,password:$('password').value,name:$('account-name').value,setup_key:$('setup-key').value});
   csrf=d.csrf;if(d.user.reset_required){changeMode();}else location.href='/';
 }catch(e){error(e.message);}finally{$('login-submit').disabled=false;}});
 fetch('/api/auth/status').then(r=>r.json()).then(d=>{setup=d.setup_required;if(setup){$('setup-fields').hidden=false;$('setup-key').required=true;$('account-name').required=true;$('login-title').textContent='Create your workspace.';$('login-description').textContent='Set up the first Super Admin account.';$('login-submit').textContent='Create Super Admin';$('password').autocomplete='new-password';}}).catch(()=>error('The application is not responding. Start TFSC Compliance and try again.'));
})();
