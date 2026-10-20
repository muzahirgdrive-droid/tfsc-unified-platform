import { useEffect, useState } from 'react';
import { send, type Data } from './api';
import { Alert, Field, Loading, Modal, useResource } from './components';
export default function FollowupTask({lead,onClose,onSaved}:{lead:Data;onClose:()=>void;onSaved:()=>void}) {
  const {data,loading,error}=useResource('/leads/'+lead.id+'/followup-task');
  const [form,setForm]=useState<Data>({}),[failure,setFailure]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{if(data)setForm({...data,due_date:data.due_date||lead.next_followup?.slice(0,10)||''});},[data]);
  return <Modal title="Schedule follow-up" subtitle={lead.provider_name} onClose={onClose}>
    <p>This updates the next action. The original call record and lead ownership remain in their audit history.</p>
    {loading?<Loading/>:<form onSubmit={async e=>{e.preventDefault();setBusy(true);setFailure('');try{await send('/leads/'+lead.id+'/followup-task',form);onSaved();onClose();}catch(e:any){setFailure(e.message);}finally{setBusy(false);}}}>
      <Field label="Next follow-up date (Pacific)"><input type="date" required value={form.due_date||''} onInput={e=>{const value=e.currentTarget.value;setForm(f=>({...f,due_date:value}));}} onChange={e=>{const value=e.target.value;setForm(f=>({...f,due_date:value}));}}/></Field>
      <Field label="Follow-up notes"><textarea required rows={4} value={form.notes||''} onChange={e=>setForm({...form,notes:e.target.value})}/></Field>
      {(failure||error)&&<Alert>{failure||error}</Alert>}<div className="form-actions"><button className="btn" disabled={busy}>{busy?'Saving…':'Save follow-up'}</button></div>
    </form>}
  </Modal>;
}
