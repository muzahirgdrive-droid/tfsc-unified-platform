import { useState } from 'react';
import { send, type Data, dateTime } from './api';
import { Alert, Badge, Empty, Loading, Modal, useResource } from './components';

export default function ClientHistory({lead,onClose}:{lead:Data;onClose:()=>void}) {
  const resource=useResource('/clients/'+lead.id+'/agreements');
  const [synced,setSynced]=useState<Data|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false);
  const data=synced||resource.data;
  return <Modal title="Agreement history" subtitle={lead.provider_name} onClose={onClose}>
    <p>Review status and released files from your mapped TFSC account. Files open in TFSC and require your agreements sign-in.</p>
    <div className="form-actions"><button className="btn" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{setSynced(await send('/clients/'+lead.id+'/agreements/sync',{}));}catch(e:any){setError(e.message);}finally{setBusy(false);}}}>{busy?'Synchronizing…':'Refresh from TFSC'}</button></div>
    {(error||resource.error)&&<Alert>{error||resource.error}</Alert>}
    {resource.loading?<Loading/>:data?.items?.length?<div className="timeline">{data.items.map((r:Data)=><div className="timeline-row" key={r.id}><span className="timeline-dot"/><div><strong>{r.entity}</strong><p><Badge value={r.state}/> · {dateTime(r.updated)} Pacific</p>{r.files.map((f:Data)=><p key={f.id}><a href={new URL(f.download_path,data.agreements_url).toString()} target="_blank" rel="noreferrer">{f.name}</a></p>)}</div></div>)}</div>:<Empty title="No synchronized agreements" detail="Create a handoff, submit a draft for review in TFSC, then refresh this history."/>}
    {data?.updated&&<small>Last synchronized {dateTime(data.updated)} Pacific. This is a status snapshot.</small>}
  </Modal>;
}
