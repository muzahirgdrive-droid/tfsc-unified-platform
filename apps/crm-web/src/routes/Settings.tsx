import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Plus } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { LegacyImport } from './LegacyImport';

type D = { id: string; name: string; legal_name: string | null; tfsc_entity_key: string | null; active: boolean; sort: number; version: number };
type P = { id: string; name: string | null; domain_id: string | null; owner_user_id: string | null; owner_name: string | null; owner_email: string | null; owner_phone: string | null; status: string; notes: string | null; in_use: boolean; version: number };
const TFSC = ['', 'SCRIBE ALIGN LLC', 'EXTENDED HEALTH SERVICES INC.', '24-7 CONSULTANCY LLC'];

export default function Settings() {
  const { can } = useAuth();
  const [tab, setTab] = useState<'domains' | 'projects' | 'policies' | 'targets' | 'legacy import'>('projects');
  const tabs = ['projects', 'domains', 'targets', 'policies', ...(can('imports.legacy') ? ['legacy import'] as const : [])] as const;
  return (
    <div>
      <PageHeader eyebrow="Settings" title="Domains, projects & policies" subtitle="Every user is registered with a domain (default Scribe Align LLC) and a project. Clients inherit both, so client ↔ project ↔ domain ↔ owner is always traceable." />
      <div className="mb-4 flex flex-wrap gap-1" role="tablist">{tabs.map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={cn('chip h-9 px-3 capitalize', tab === t ? 'bg-navy text-white' : 'border border-line bg-surface')}>{t}</button>)}</div>
      {tab === 'domains' && <Domains />}{tab === 'projects' && <Projects />}{tab === 'targets' && <Targets />}{tab === 'policies' && <Policies />}
      {tab === 'legacy import' && can('imports.legacy') && <LegacyImport />}
    </div>
  );
}

function Domains() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['domains'], queryFn: () => raw<D[]>('/api/settings/domains') });
  const [edit, setEdit] = useState<Partial<D> | null>(null);
  const save = useMutation({ mutationFn: () => edit!.version ? raw(`/api/settings/domains/${edit!.id}`, { method: 'PATCH', body: JSON.stringify({ version: edit!.version, name: edit!.name, legal_name: edit!.legal_name, tfsc_entity_key: edit!.tfsc_entity_key || null, active: edit!.active }) })
    : raw('/api/settings/domains', { method: 'POST', body: JSON.stringify({ id: edit!.id, name: edit!.name, legal_name: edit!.legal_name, tfsc_entity_key: edit!.tfsc_entity_key || null }) }),
    onSuccess: () => { toast.success('Saved'); setEdit(null); qc.invalidateQueries({ queryKey: ['domains'] }); }, onError: (e) => toast.error((e as Error).message) });
  return <Spinnerless loading={q.isLoading}>
    <div className="mb-3"><button className="btn-primary" onClick={() => setEdit({ id: '', name: '', active: true })}><Plus size={16} />Add domain</button></div>
    <div className="card overflow-x-auto"><table className="w-full text-sm"><thead><tr className="table-head"><th className="p-3">Domain</th><th>TFSC contracting company</th><th>Status</th></tr></thead>
      <tbody>{q.data?.map((d) => <tr key={d.id} className="cursor-pointer border-t border-line hover:bg-bg" onClick={() => setEdit(d)}><td className="p-3"><b>{d.name}</b>{d.id === 'scribe_align' && <span className="chip ml-2 bg-info-bg text-info">default</span>}<div className="text-xs text-muted">{d.id}</div></td><td>{d.tfsc_entity_key ?? <span className="text-muted">— (supervisor picks at handoff)</span>}</td><td>{d.active ? 'Active' : 'Inactive'}</td></tr>)}</tbody></table></div>
    {edit && <Dialog open onOpenChange={(o) => !o && setEdit(null)} title={edit.version ? `Edit ${edit.name}` : 'Add domain'} dismissOnOutside={false} footer={<button className="btn-primary" onClick={() => save.mutate()}>Save</button>}>
      <div className="space-y-3">{!edit.version && <div><label className="block"><span className="label">ID (lowercase, underscores)</span><input className="input" value={edit.id} onChange={(e) => setEdit({ ...edit, id: e.target.value })} /></label></div>}
        <div><label className="block"><span className="label">Name</span><input className="input" value={edit.name ?? ''} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label></div>
        <div><label className="block"><span className="label">Legal name</span><input className="input" value={edit.legal_name ?? ''} onChange={(e) => setEdit({ ...edit, legal_name: e.target.value })} /></label></div>
        <div><label className="block"><span className="label">TFSC agreement entity (mapping needs confirmation)</span><select className="input" value={edit.tfsc_entity_key ?? ''} onChange={(e) => setEdit({ ...edit, tfsc_entity_key: e.target.value })}>{TFSC.map((t) => <option key={t} value={t}>{t || '— none —'}</option>)}</select></label></div>
        {edit.version && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} />Active (domains in use are never deleted)</label>}</div>
    </Dialog>}
  </Spinnerless>;
}

function Projects() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['projects'], queryFn: () => raw<P[]>('/api/settings/projects') });
  const domains = useQuery({ queryKey: ['domains'], queryFn: () => raw<D[]>('/api/settings/domains') });
  const users = useQuery({ queryKey: ['users', ''], queryFn: () => raw<{ items: { id: string; display_name: string }[] }>('/api/users?limit=200') });
  const [edit, setEdit] = useState<Partial<P> | null>(null);
  const [summary, setSummary] = useState<Record<string, number> | null>(null);
  const save = useMutation({ mutationFn: () => {
    const body = { name: edit!.name || null, domain_id: edit!.domain_id || null, owner_user_id: edit!.owner_user_id || null, owner_name: edit!.owner_name || null, owner_email: edit!.owner_email || null, owner_phone: edit!.owner_phone || null, status: edit!.status, notes: edit!.notes || null };
    return edit!.version ? raw(`/api/settings/projects/${edit!.id}`, { method: 'PATCH', body: JSON.stringify({ version: edit!.version, ...body }) }) : raw('/api/settings/projects', { method: 'POST', body: JSON.stringify({ id: edit!.id, ...body }) });
  }, onSuccess: () => { toast.success('Saved'); setEdit(null); qc.invalidateQueries({ queryKey: ['projects'] }); }, onError: (e) => toast.error((e as Error).message) });
  const open = async (p: Partial<P>) => { setEdit(p); setSummary(null); if (p.version) setSummary(await raw<Record<string, number>>(`/api/settings/projects/${p.id}/summary`)); };
  return <Spinnerless loading={q.isLoading}>
    <div className="mb-3"><button className="btn-primary" onClick={async () => { const n = await raw<{ id: string }>('/api/settings/projects/next-id'); open({ id: n.id, status: 'pending_configuration' }); }}><Plus size={16} />Add project</button></div>
    <div className="card overflow-x-auto"><table className="w-full min-w-[640px] text-sm"><thead><tr className="table-head"><th className="p-3">Project</th><th>Domain</th><th>Owner</th><th>Status</th><th>In use</th></tr></thead>
      <tbody>{q.data?.map((p) => <tr key={p.id} className="cursor-pointer border-t border-line hover:bg-bg" onClick={() => open(p)}><td className="p-3"><b>{p.id}</b>{p.name && p.name !== p.id && <span className="text-muted"> · {p.name}</span>}</td><td>{domains.data?.find((d) => d.id === p.domain_id)?.name ?? '—'}</td><td>{p.owner_name ?? <span className="text-warn">needs owner</span>}</td><td><span className={cn('chip', p.status === 'active' ? 'bg-success-bg text-success' : p.status === 'pending_configuration' ? 'bg-warn-bg text-warn' : 'bg-bg text-muted')}>{p.status.replace('_', ' ')}</span></td><td>{p.in_use ? 'Yes' : '—'}</td></tr>)}</tbody></table></div>
    {edit && <Dialog open onOpenChange={(o) => !o && setEdit(null)} title={edit.version ? `Project ${edit.id}` : 'Add project'} dismissOnOutside={false} footer={<button className="btn-primary" onClick={() => save.mutate()}>Save</button>} wide>
      {summary && <div className="mb-3 flex flex-wrap gap-2">{Object.entries(summary).filter(([k]) => k !== 'project_id').map(([k, v]) => <span key={k} className="chip bg-bg">{k}: {v}</span>)}</div>}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div><label className="block"><span className="label">Project ID (P0000; immutable once used)</span><input className="input" value={edit.id ?? ''} disabled={!!edit.version} onChange={(e) => setEdit({ ...edit, id: e.target.value.toUpperCase() })} /></label></div>
        <div><label className="block"><span className="label">Name</span><input className="input" value={edit.name ?? ''} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label></div>
        <div><label className="block"><span className="label">Domain</span><select className="input" value={edit.domain_id ?? ''} onChange={(e) => setEdit({ ...edit, domain_id: e.target.value })}><option value="">—</option>{domains.data?.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label></div>
        <div><label className="block"><span className="label">Status</span><select className="input" value={edit.status} onChange={(e) => setEdit({ ...edit, status: e.target.value })}>{['active', 'paused', 'closed', 'pending_configuration'].map((s) => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}</select></label></div>
        <div><label className="block"><span className="label">Owner (existing user)</span><select className="input" value={edit.owner_user_id ?? ''} onChange={(e) => setEdit({ ...edit, owner_user_id: e.target.value })}><option value="">— or enter external owner below —</option>{users.data?.items.map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label></div>
        <div><label className="block"><span className="label">External owner name</span><input className="input" value={edit.owner_name ?? ''} onChange={(e) => setEdit({ ...edit, owner_name: e.target.value })} /></label></div>
        <div><label className="block"><span className="label">Owner email</span><input className="input" value={edit.owner_email ?? ''} onChange={(e) => setEdit({ ...edit, owner_email: e.target.value })} /></label></div>
        <div><label className="block"><span className="label">Owner phone</span><input className="input" value={edit.owner_phone ?? ''} onChange={(e) => setEdit({ ...edit, owner_phone: e.target.value })} /></label></div>
        <div className="sm:col-span-2"><label className="block"><span className="label">Notes</span><textarea className="input h-20 py-2" value={edit.notes ?? ''} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></label></div>
      </div>
    </Dialog>}
  </Spinnerless>;
}

function Targets() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['targets'], queryFn: () => raw<{ effective_date: string; call_target: number; conn_target: number; bands: Record<string, number> }[]>('/api/settings/targets') });
  const [v, setV] = useState({ call_target: 170, conn_target: 60 });
  const save = useMutation({ mutationFn: () => raw('/api/settings/targets', { method: 'POST', body: JSON.stringify({ call_target: v.call_target, conn_target: v.conn_target / 100 }) }), onSuccess: () => { toast.success('Saved — applies from the next Pacific date'); qc.invalidateQueries({ queryKey: ['targets'] }); } });
  return <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
    <div className="card p-5"><h3 className="mb-3 font-semibold">New targets</h3><div className="grid grid-cols-2 gap-3">
      <div><label className="block"><span className="label">Calls per day</span><input type="number" className="input" value={v.call_target} onChange={(e) => setV({ ...v, call_target: +e.target.value })} /></label></div>
      <div><label className="block"><span className="label">Connection target %</span><input type="number" className="input" value={v.conn_target} onChange={(e) => setV({ ...v, conn_target: +e.target.value })} /></label></div></div>
      <p className="mt-2 text-xs text-muted">Effective-dated: a change applies from tomorrow (Pacific) and never rewrites past days.</p><button className="btn-primary mt-3" onClick={() => save.mutate()}>Save targets</button></div>
    <div className="card p-5"><h3 className="mb-3 font-semibold">History</h3><ul className="space-y-1 text-sm">{q.data?.map((t) => <li key={t.effective_date}>{t.effective_date}: {t.call_target} calls/day · {(t.conn_target * 100).toFixed(0)}% connection</li>)}</ul></div>
  </div>;
}

function Policies() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['settings'], queryFn: () => raw<{ values: Record<string, unknown> }>('/api/settings') });
  const save = useMutation({ mutationFn: (v: { key: string; value: unknown }) => raw('/api/settings', { method: 'PATCH', body: JSON.stringify(v) }), onSuccess: () => { toast.success('Saved'); qc.invalidateQueries({ queryKey: ['settings'] }); qc.invalidateQueries({ queryKey: ['config'] }); }, onError: (e) => toast.error((e as Error).message) });
  const s = q.data?.values as Record<string, Record<string, unknown>> | undefined;
  const [lw, setLw] = useState<number | null>(null); const [tpl, setTpl] = useState<string | null>(null); const [dial, setDial] = useState<{ mode: string; template: string; verified: boolean } | null>(null);
  if (!s) return <div className="skeleton h-40" />;
  const lock = s.lock_windows as { manual_minutes: number; import_minutes: number };
  const cel = s.celebration as { template: string; seconds: number };
  const d = (dial ?? { mode: String((s.dialer as Record<string, unknown>).mode ?? 'tel'), template: String((s.dialer as Record<string, unknown>).template ?? ''), verified: !!(s.dialer as Record<string, unknown>).verified });
  return <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
    <div className="card p-5"><h3 className="font-semibold">Lock windows</h3><p className="text-xs text-muted">Manual entries: {lock.manual_minutes} min. Paste/import: 10–20 min.</p>
      <label className="label mt-3">Import lock window: {lw ?? lock.import_minutes} min</label><input type="range" min={10} max={20} value={lw ?? lock.import_minutes} onChange={(e) => setLw(+e.target.value)} className="w-full" />
      <button className="btn-primary mt-3" onClick={() => save.mutate({ key: 'lock_windows', value: { manual_minutes: lock.manual_minutes, import_minutes: lw ?? lock.import_minutes } })}>Save</button></div>
    <div className="card p-5"><h3 className="font-semibold">Celebration text</h3><p className="text-xs text-muted">Use {'{agent}'} and {'{supervisor}'}. No client details are ever shown.</p>
      <textarea className="input mt-3 h-20 py-2" value={tpl ?? cel.template} onChange={(e) => setTpl(e.target.value)} />
      <button className="btn-primary mt-3" onClick={() => save.mutate({ key: 'celebration', value: { template: tpl ?? cel.template, seconds: cel.seconds } })}>Save</button></div>
    <div className="card p-5"><h3 className="font-semibold">Dialer</h3><p className="text-xs text-muted">tel: links open the Vonage Business Communications app when it is the registered handler. A custom URI is only used after it is verified with your Vonage product.</p>
      <select className="input mt-3" value={d.mode} onChange={(e) => setDial({ ...d, mode: e.target.value })}><option value="tel">tel: (default)</option><option value="template">Custom URI template</option><option value="vbc_api">Vonage VBC API (needs credentials)</option></select>
      {d.mode === 'template' && <><input className="input mt-2" placeholder="vonage://call?number={e164}" value={d.template} onChange={(e) => setDial({ ...d, template: e.target.value })} /><label className="mt-2 flex items-center gap-2 text-sm"><input type="checkbox" checked={d.verified} onChange={(e) => setDial({ ...d, verified: e.target.checked })} />Verified on our Vonage product</label></>}
      <button className="btn-primary mt-3" onClick={() => save.mutate({ key: 'dialer', value: d })}>Save</button></div>
    <div className="card p-5"><h3 className="font-semibold">Names & leads</h3>
      <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={!!(s.names as Record<string, unknown>).suffix_comma} onChange={(e) => save.mutate({ key: 'names', value: { suffix_comma: e.target.checked } })} />Comma before legal suffix (Holistic Behavioral Health, LLC)</label>
      <label className="label mt-3">Lead protection window (days)</label><input type="number" className="input w-32" defaultValue={Number(s.lead_protection_days)} onBlur={(e) => save.mutate({ key: 'lead_protection_days', value: +e.target.value })} /></div>
  </div>;
}
