import {useState} from 'react';
import {Camera,KeyRound,ShieldCheck} from 'lucide-react';
import {send,initials,label} from './api';
import type {Data} from './api';
import {Alert,Field,Modal} from './components';

export default function Profile({user,onClose,onUpdated,required=false}:{user:Data,onClose:()=>void,onUpdated:()=>void,required?:boolean}) {
  const [current,setCurrent]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState('');
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState(''),[photoVersion,setPhotoVersion]=useState(0);
  return <Modal title={required?'Set your personal password':'Your profile & security'} subtitle={required?'Replace your temporary password before opening the workspace.':'Manage your photo and keep your account secure.'} onClose={required?()=>{}:onClose}>
    {!required&&<div className="profile-editor"><div className="avatar profile-photo">{user.has_photo||photoVersion?<img src={'/api/profile/photo/'+user.id+'?v='+photoVersion} alt={user.name}/>:initials(user.name)}</div><div><h3>{user.name}</h3><p>{user.email}</p><small>{label(user.role)}{user.agent_id?' · '+user.agent_id:''}</small><label className="btn secondary small"><Camera size={16}/>Update photo<input hidden type="file" accept="image/png,image/jpeg" disabled={busy} onChange={async e=>{
      const file=e.target.files?.[0];if(!file)return;setError('');setMessage('');
      if(file.size>2_000_000){setError('Choose a PNG or JPEG smaller than 2 MB.');return;}
      setBusy(true);try {const image=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=()=>reject(new Error('This photo could not be read.'));reader.readAsDataURL(file);});await send('/profile/photo',{image});setPhotoVersion(Date.now());setMessage('Your profile photo has been updated.');onUpdated();}catch(e:any){setError(e.message);}finally{setBusy(false);}
    }}/></label></div></div>}
    <form onSubmit={async e=>{e.preventDefault();setError('');setMessage('');if(password!==confirm){setError('The new passwords do not match.');return;}if(password===current){setError('Choose a new password that differs from your current password.');return;}setBusy(true);try{await send('/auth/password',{current_password:current,password});setCurrent('');setPassword('');setConfirm('');setMessage('Password updated. Other active sessions have been signed out.');onUpdated();if(required)onClose();}catch(e:any){setError(e.message);}finally{setBusy(false);}}}>
      <div className="panel-heading"><h3><KeyRound size={17}/> Change password</h3><ShieldCheck size={20}/></div>
      <Field label={required?'Temporary password':'Current password'}><input type="password" autoComplete="current-password" required value={current} onChange={e=>setCurrent(e.target.value)}/></Field>
      <Field label="New password" hint="Use at least 12 characters. A unique passphrase is recommended."><input type="password" autoComplete="new-password" minLength={12} required value={password} onChange={e=>setPassword(e.target.value)}/></Field>
      <Field label="Confirm new password"><input type="password" autoComplete="new-password" minLength={12} required value={confirm} onChange={e=>setConfirm(e.target.value)}/></Field>
      {error&&<Alert>{error}</Alert>}{message&&<Alert success>{message}</Alert>}
      <div className="form-actions"><button className="btn" disabled={busy}>{busy?'Saving…':'Update password'}</button></div>
    </form>
  </Modal>
}
