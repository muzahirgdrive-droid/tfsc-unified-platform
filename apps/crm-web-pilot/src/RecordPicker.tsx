import {useState} from 'react';
import {usePagedResource,CursorControls,Alert,SearchBox} from './components';
import {rows,nameOf} from './api';
import type {Data} from './api';

export default function RecordPicker({path,value,onChange,caption,initial,filter}:{path:string,value:string,onChange:(id:string)=>void,caption:string,initial?:Data,filter?:(item:Data)=>boolean}){
  const [search,setSearch]=useState(''),[chosen,setChosen]=useState<Data|undefined>(initial);
  const paging=usePagedResource(path+(path.includes('?')?'&':'?')+'q='+encodeURIComponent(search));
  const options=rows(paging.data).filter(filter||(()=>true));
  const selected=options.find(r=>r.id===value)||chosen?.id===value&&chosen||initial?.id===value&&initial;
  return <div className="record-picker"><div className="field"><span>{caption}</span><SearchBox value={search} onChange={setSearch} placeholder={'Find '+caption.toLowerCase()+'…'}/><select aria-label={caption} value={value||''} onChange={e=>{const item=options.find(r=>r.id===e.target.value);if(item)setChosen(item);onChange(e.target.value);}}><option value="">Select {caption.toLowerCase()}</option>{value&&!options.some(r=>r.id===value)&&<option value={value}>{selected?nameOf(selected):'Current selection'}</option>}{options.map(r=><option key={r.id} value={r.id}>{nameOf(r)}{r.role?' · '+r.role.replace(/_/g,' '):''}</option>)}</select></div>{paging.error&&<Alert>{paging.error}</Alert>}<CursorControls paging={paging}/></div>;
}
