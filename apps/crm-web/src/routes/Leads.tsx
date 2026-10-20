import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowRight, CalendarPlus, ExternalLink, Lock, PartyPopper, ShieldCheck, Undo2, UserCog, XCircle } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { Empty, PageHeader, PhoneLink, Spinnerless } from '@/components/ui/bits';
import { raw, type Schemas } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { businessDate, dual, ptToUtc } from '@/lib/time';
import { asText } from '@/lib/text';

type L = Schemas['LeadOut'] & Partial<Schemas['LeadOut']>;
type Protection = { window_days: number; last_activity_at: string; days_inactive: number; review_pending: boolean };
const STAGES: [string, string][] = [['', 'Active'], ['qualified_lead', 'Lead'], ['meeting', 'Meeting'], ['signed_up', 'Signed up'], ['agreement_in_progress', 'Agreement'], ['client', 'Client'], ['lost', 'Lost']];
const STAGE_STYLE: Record<string, string> = { qualified_lead: 'bg-info-bg text-info', meeting: 'bg-royal/10 text-royal', with_management: 'bg-royal/10 text-royal', signed_up: 'bg-success-bg text-success', agreement_in_progress: 'bg-warn-bg text-warn', client: 'bg-success text-white', lost: 'bg-bg text-muted' };

export default function Leads() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const [stage, setStage] = useState('');
  const [open, setOpen] = useState<string | null>(() => new URLSearchParams(window.location.search).get('lead')); // deep link from the Flags Center
  const q = useQuery({ queryKey: ['leads', stage], queryFn: () => raw<{ items: L[] }>(`/api/leads${stage ? `?stage=${stage}` : ''}`), refetchInterval: 60_000 });
  return (
    <div>
      <PageHeader eyebrow="Leads" title="Owned opportunities" subtitle={rank >= 1 ? 'Every lead is owned and locked to one agent. Sales are confirmed by a supervisor and celebrated on every screen.' : 'Your leads. Meetings are handled by management.'}
        actions={<div className="flex flex-wrap gap-1">{STAGES.map(([v, l]) => <button key={v} onClick={() => setStage(v)} className={cn('chip h-9 px-3', stage === v ? 'bg-navy text-white' : 'border border-line bg-surface')}>{l}</button>)}</div>} />
      <Spinnerless loading={q.isLoading}>
        {q.data?.items.length === 0 ? <Empty title="No leads here">Confirmed strong follow-ups become leads.</Empty> : (
          <div className="card divide-y divide-line">
            {q.data?.items.map((l) => (
              // The name is the button; its ::after covers the row so the whole row opens the lead, while the phone link
              // stays a separate control above it (no link nested inside a button).
              <div key={l.id} className="relative flex w-full flex-wrap items-center gap-3 p-4 hover:bg-bg">
                <div className="min-w-0 flex-1 basis-full sm:basis-0"><button onClick={() => setOpen(l.id)} className="text-left font-semibold after:absolute after:inset-0 after:content-['']">{l.provider_name}</button><div className="text-sm text-muted">{l.owner_name ?? ''}</div></div>
                <span className="relative z-10"><PhoneLink display={l.phone} /></span>
                {l.agreement_locked && <span className="chip bg-warn-bg text-warn"><Lock size={12} />Agreement lock</span>}
                <span className={cn('chip', STAGE_STYLE[l.stage] ?? 'bg-bg')}>{l.stage_label}</span><ArrowRight size={16} className="text-subtle" />
              </div>))}
          </div>)}
      </Spinnerless>
      {open && <LeadDetail id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function LeadDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { me } = useAuth();
  const rank = rankOf(me);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['lead', id], queryFn: () => raw<L>(`/api/leads/${id}`) });
  const [dlg, setDlg] = useState<null | 'lost' | 'reverse' | 'reopen' | 'reassign' | 'meeting' | 'agreements' | 'release'>(null);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ['lead', id] }); void qc.invalidateQueries({ queryKey: ['leads'] }); };
  const tr = useMutation({ mutationFn: (v: { action: string; reason?: string }) => raw(`/api/leads/${id}/transition`, { method: 'POST', body: JSON.stringify({ version: q.data!.version, ...v }) }),
    onSuccess: (_d, v) => { toast.success(v.action === 'sign_up' ? 'Sale confirmed — celebrating!' : 'Lead updated'); setDlg(null); refresh(); }, onError: (e) => toast.error(e.message) });
  // §27: answer a protection review — renew keeps the owner, release closes the lead and frees the number
  const prot = useMutation({ mutationFn: (v: { action: 'renew' | 'release'; reason?: string }) => raw(`/api/leads/${id}/protection`, { method: 'POST', body: JSON.stringify({ version: q.data!.version, ...v }) }),
    onSuccess: (_d, v) => { toast.success(v.action === 'renew' ? 'Protection renewed' : 'Lead released to the pool'); setDlg(null); refresh(); void qc.invalidateQueries({ queryKey: ['flags'] }); },
    onError: (e) => toast.error(e.message) });
  const l = q.data;
  const p = l?.protection as Protection | null | undefined;
  const mgmt = rank >= 1 && l && 'owner_name' in l;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={l?.provider_name ?? 'Lead'} description={l ? `${l.stage_label}${mgmt ? ` · owner ${l.owner_name}` : ''}` : ''} wide>
      {!l ? <div className="skeleton h-40" /> : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-3 text-sm"><PhoneLink display={l.phone} />
            {mgmt && l.project_id && <span className="chip bg-bg">Project {l.project_id}{l.project_owner ? ` · ${l.project_owner}` : ''}</span>}
            {mgmt && l.domain_id && <span className="chip bg-bg">{l.domain_id}</span>}
            {l.agreement_status && <span className="chip bg-warn-bg text-warn">Agreement: {l.agreement_status.replace('_', ' ')}</span>}</div>
          {mgmt && (
            <div className="flex flex-wrap gap-2">
              {['qualified_lead', 'meeting'].includes(l.stage) && <button className="btn-primary" onClick={() => tr.mutate({ action: 'sign_up' })}><PartyPopper size={16} />Confirm sale (Signed Up)</button>}
              {['qualified_lead', 'meeting'].includes(l.stage) && <button className="btn-secondary" onClick={() => setDlg('meeting')}><CalendarPlus size={16} />Schedule meeting</button>}
              {['signed_up', 'agreement_in_progress'].includes(l.stage) && me?.agreements_access && <button className="btn-primary" onClick={() => setDlg('agreements')}><ExternalLink size={16} />Proceed to Agreements</button>}
              {l.stage === 'signed_up' && <button className="btn-secondary" onClick={() => setDlg('reverse')}><Undo2 size={16} />Reverse sale</button>}
              {['qualified_lead', 'meeting'].includes(l.stage) && <button className="btn-secondary" onClick={() => setDlg('lost')}><XCircle size={16} />Mark lost</button>}
              {l.stage === 'lost' && <button className="btn-secondary" onClick={() => setDlg('reopen')}>Reopen</button>}
              {!l.agreement_locked && l.stage !== 'lost' && <button className="btn-secondary" onClick={() => setDlg('reassign')}><UserCog size={16} />Reassign</button>}
            </div>)}
          {mgmt && p && (
            <section className={cn('rounded-xl border p-3 text-sm', p.review_pending ? 'border-warn/40 bg-warn-bg' : 'border-line')} aria-label="Lead protection">
              <div className="flex flex-wrap items-center gap-2"><ShieldCheck size={16} aria-hidden />
                <span>Last activity {new Date(p.last_activity_at).toLocaleDateString()} ({p.days_inactive} day{p.days_inactive === 1 ? '' : 's'} ago) · protection window {p.window_days} days</span></div>
              {p.review_pending && <>
                <p className="mt-2 font-semibold">No activity for {p.days_inactive} days. Release this lead to the pool or renew its protection.</p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button className="btn-primary" disabled={prot.isPending} onClick={() => prot.mutate({ action: 'renew' })}>Renew protection</button>
                  <button className="btn-secondary" disabled={prot.isPending} onClick={() => setDlg('release')}>Release to pool</button>
                </div></>}
            </section>)}
          {mgmt && l.meetings && l.meetings.length > 0 && <section><h4 className="mb-2 font-semibold">Meetings</h4><ul className="space-y-1 text-sm">{l.meetings.map((m) => <li key={String(m.id)}>{dual(String(m.starts_at))} — {String(m.title)} <span className="chip bg-bg">{String(m.status)}</span></li>)}</ul></section>}
          {mgmt && l.attribution && <section><h4 className="mb-2 font-semibold">Attribution ledger</h4><div className="flex flex-wrap gap-2">{l.attribution.map((a, i) => <span key={i} className="chip bg-bg">{String(a.role).replace('_', ' ')}: {String(a.user)}{Number(a.share) ? ` · ${(Number(a.share) * 100).toFixed(0)}%` : ''}</span>)}</div></section>}
          {mgmt && l.timeline && <section><h4 className="mb-2 font-semibold">Timeline</h4><ol className="space-y-2 border-l-2 border-line pl-4 text-sm">{l.timeline.map((e, i) => (
            <li key={i}><span className="text-xs text-muted">{new Date(String(e.at)).toLocaleString()}</span> · <b>{asText(e.kind)}</b> {e.by ? `by ${asText(e.by)}` : ''} {e.stage ? `· ${asText(e.stage)}` : ''} {e.action ? `· ${asText(e.action)}` : ''} {e.remarks ? <span className="text-muted">— {asText(e.remarks)}</span> : null}</li>))}</ol></section>}
        </div>)}
      {l && (dlg === 'lost' || dlg === 'reverse' || dlg === 'reopen') && <ReasonDialog title={dlg === 'lost' ? 'Mark lead lost' : dlg === 'reverse' ? 'Reverse sale' : 'Reopen lead'} onClose={() => setDlg(null)} onSubmit={(reason) => tr.mutate({ action: dlg === 'reverse' ? 'reverse_sale' : dlg, reason })} />}
      {l && dlg === 'release' && <ReasonDialog title="Release lead to the pool" onClose={() => setDlg(null)} onSubmit={(reason) => prot.mutate({ action: 'release', reason })} />}
      {l && dlg === 'reassign' && <Reassign lead={l} onClose={() => setDlg(null)} onDone={refresh} />}
      {l && dlg === 'meeting' && <Schedule lead={l} onClose={() => setDlg(null)} onDone={refresh} />}
      {/* After the handoff the lead detail closes too: a modal underneath would block the toast's "Open Agreements" link. */}
      {l && dlg === 'agreements' && <Preflight lead={l} onClose={() => setDlg(null)} onDone={() => { refresh(); onClose(); }} />}
    </Dialog>
  );
}

function ReasonDialog({ title, onClose, onSubmit }: { title: string; onClose: () => void; onSubmit: (r: string) => void }) {
  const [r, setR] = useState('');
  return <Dialog open onOpenChange={(o) => !o && onClose()} title={title} footer={<button className="btn-primary" disabled={r.trim().length < 3} onClick={() => onSubmit(r)}>Confirm</button>}>
    <label className="block"><span className="label">Reason (audited)</span><textarea className="input h-24 py-2" value={r} onChange={(e) => setR(e.target.value)} /></label></Dialog>;
}

function Reassign({ lead, onClose, onDone }: { lead: L; onClose: () => void; onDone: () => void }) {
  const people = useQuery({ queryKey: ['users-scope'], queryFn: () => raw<{ items: { id: string; display_name: string }[] }>('/api/users?role=sales_agent&limit=200') });
  const [to, setTo] = useState(''); const [reason, setReason] = useState(''); const [share, setShare] = useState(50);
  const m = useMutation({ mutationFn: () => raw(`/api/leads/${lead.id}/reassign`, { method: 'POST', body: JSON.stringify({ version: lead.version, to_user: to, reason, incentive_split: to === lead.owner_id ? { [to]: 1 } : { [lead.owner_id]: (100 - share) / 100, [to]: share / 100 } }) }),
    onSuccess: () => { toast.success('Lead reassigned'); onDone(); onClose(); }, onError: (e) => toast.error(e.message) });
  return <Dialog open onOpenChange={(o) => !o && onClose()} title="Reassign lead" description="Reassignment needs a reason and records the incentive attribution." dismissOnOutside={false}
    footer={<button className="btn-primary" disabled={!to || reason.length < 3 || m.isPending} onClick={() => m.mutate()}>Reassign</button>}>
    <label className="block"><span className="label">New owner</span><select className="input mb-3" value={to} onChange={(e) => setTo(e.target.value)}><option value="">Choose…</option>{people.data?.items.filter((p) => p.id !== lead.owner_id).map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select></label>
    <label className="block"><span className="label">Incentive share for the new owner: {share}% (previous owner {100 - share}%)</span><input type="range" min={0} max={100} step={5} value={share} onChange={(e) => setShare(+e.target.value)} className="mb-3 w-full" /></label>
    <label className="block"><span className="label">Reason</span><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></label>
  </Dialog>;
}

function Schedule({ lead, onClose, onDone }: { lead: L; onClose: () => void; onDone: () => void }) {
  const g = useQuery({ queryKey: ['google'], queryFn: () => raw<{ configured: boolean; connected: boolean; message: string | null }>('/api/integrations/google/status') });
  const [v, setV] = useState({ title: `Demo · ${lead.provider_name ?? ''}`, date: businessDate(), time: '10:00', duration: 30, attendees: '', invite: false });
  const m = useMutation({ mutationFn: () => raw<{ meet_url: string | null }>(`/api/leads/${lead.id}/meetings`, { method: 'POST', body: JSON.stringify({ title: v.title, starts_at: ptToUtc(v.date, v.time).toISOString(), duration_minutes: v.duration, attendees: v.attendees.split(/[,;\s]+/).filter(Boolean), send_invites: v.invite }) }),
    onSuccess: (r) => { toast.success(r.meet_url ? 'Meeting created with Google Meet' : 'Meeting recorded (Google not connected)'); onDone(); onClose(); }, onError: (e) => toast.error(e.message) });
  return <Dialog open onOpenChange={(o) => !o && onClose()} title="Schedule meeting" description={g.data?.message ?? 'Creates a Google Calendar event with a Meet link.'} dismissOnOutside={false}
    footer={<button className="btn-primary" disabled={m.isPending} onClick={() => m.mutate()}>Create meeting</button>}>
    <div className="space-y-3">
      <div><label className="block"><span className="label">Title</span><input className="input" value={v.title} onChange={(e) => setV({ ...v, title: e.target.value })} /></label></div>
      <div className="grid grid-cols-3 gap-2"><input type="date" className="input" aria-label="Date" value={v.date} onChange={(e) => setV({ ...v, date: e.target.value })} /><input type="time" className="input" aria-label="Start time" value={v.time} onChange={(e) => setV({ ...v, time: e.target.value })} />
        <select className="input" aria-label="Duration" value={v.duration} onChange={(e) => setV({ ...v, duration: +e.target.value })}>{[15, 30, 45, 60, 90].map((d) => <option key={d} value={d}>{d} min</option>)}</select></div>
      <p className="text-xs text-muted">{dual(ptToUtc(v.date, v.time))}</p>
      <div><label className="block"><span className="label">Attendees (optional, client email)</span><input className="input" value={v.attendees} onChange={(e) => setV({ ...v, attendees: e.target.value })} /></label></div>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.invite} onChange={(e) => setV({ ...v, invite: e.target.checked })} />Send Google invitations</label>
    </div>
  </Dialog>;
}

type Pre = Schemas['PreflightOut'];
function Preflight({ lead, onClose, onDone }: { lead: L; onClose: () => void; onDone: () => void }) {
  const q = useQuery({ queryKey: ['preflight', lead.id], queryFn: () => raw<Pre>(`/api/leads/${lead.id}/agreements/preflight`) });
  const [v, setV] = useState<Record<string, string | string[]>>({});
  const p = q.data;
  const val = (k: keyof Pre) => (v[k] as string | undefined) ?? (p?.[k] as string | null | undefined) ?? '';
  const m = useMutation({
    mutationFn: () => raw<{ status: string; agreements_url: string | null; message: string }>(`/api/leads/${lead.id}/agreements/handoff`, { method: 'POST', body: JSON.stringify({
      legal_name: val('legal_name'), providers: p?.providers ?? [], address: val('address'), email: val('email') || null, phone: val('phone') || null, signatory: val('signatory'), title: val('title'),
      effective_date: val('effective_date'), entity: (v.entity as string) || p?.suggested_entity, document_types: v.document_types ?? ['baa', 'services'] }) }),
    onSuccess: (r) => { const url = r.agreements_url; toast.success(r.message, url ? { duration: 20000, action: { label: 'Open Agreements', onClick: () => window.open(url, '_blank', 'noopener') } } : undefined); onDone(); onClose(); }, onError: (e) => toast.error(e.message),
  });
  const f = (k: keyof Pre, label: string, type = 'text') => <div><label className="block"><span className="label">{label}</span><input type={type} className="input" value={val(k)} onChange={(e) => setV({ ...v, [k]: e.target.value })} /></label></div>;
  const docs = (v.document_types as string[] | undefined) ?? ['baa', 'services'];
  return <Dialog open onOpenChange={(o) => !o && onClose()} title="Proceed to Agreements" description="Review the client details. Confirming engages the Agreement Lock: ownership, attribution and identity freeze (Super Admin + reason to change)." wide dismissOnOutside={false}
    footer={<button className="btn-primary" disabled={!p || m.isPending || !((v.entity as string) || p.suggested_entity)} onClick={() => m.mutate()}><Lock size={16} />Lock & open in TFSC Agreements</button>}>
    {!p ? <div className="skeleton h-48" /> : <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {f('legal_name', 'Practice legal name')}{f('address', 'Address')}{f('email', 'Notice email', 'email')}{f('phone', 'Phone')}{f('signatory', 'Signatory')}{f('title', 'Title')}{f('effective_date', 'Effective date', 'date')}
      <div><label className="block"><span className="label">TFSC contracting company</span><select className="input" value={(v.entity) ?? p.suggested_entity ?? ''} onChange={(e) => setV({ ...v, entity: e.target.value })}><option value="">Choose…</option>{p.entity_options.map((o) => <option key={o}>{o}</option>)}</select></label>
        {p.must_pick_reason && <p className="mt-1 text-xs text-warn">{p.must_pick_reason}</p>}</div>
      <div className="sm:col-span-2 flex flex-wrap gap-4 text-sm"><span className="label">Documents</span>{[['baa', 'Business Associate Agreement'], ['services', 'Services Agreement']].map(([k, l]) => (
        <label key={k} className="flex items-center gap-2"><input type="checkbox" checked={docs.includes(k!)} onChange={(e) => setV({ ...v, document_types: e.target.checked ? [...docs, k!] : docs.filter((d) => d !== k) })} />{l}</label>))}</div>
      <p className="sm:col-span-2 text-xs text-muted">Domain: {p.domain ?? '—'} · Project: {p.project_id ?? '—'}{p.project_owner ? ` (owner ${p.project_owner})` : ''}. Services, pricing and terms are completed in TFSC; an independent reviewer must approve before anything is issued.</p>
    </div>}
  </Dialog>;
}
