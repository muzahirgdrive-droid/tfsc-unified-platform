import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { Empty, PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw, type Schemas } from '@/lib/api';
import { cn } from '@/lib/cn';

type S = Schemas['StrongOut'];

export default function Strong() {
  const qc = useQueryClient();
  const [tab, setTab] = useState('pending');
  const q = useQuery({ queryKey: ['strong', tab], queryFn: () => raw<S[]>(`/api/strong?decision=${tab}`), refetchInterval: 30_000 });
  const people = useQuery({ queryKey: ['users-scope'], queryFn: () => raw<{ items: { id: string; display_name: string; crm_role: string }[] }>('/api/users?role=sales_agent&limit=200') });
  const [reject, setReject] = useState<S | null>(null);
  const [convert, setConvert] = useState<S | null>(null);
  const [reason, setReason] = useState(''); const [owner, setOwner] = useState(''); const [comment, setComment] = useState<Record<string, string>>({});
  const refresh = () => qc.invalidateQueries({ queryKey: ['strong'] });
  const decide = useMutation({ mutationFn: (v: { s: S; decision: 'confirmed' | 'rejected' }) => raw(`/api/strong/${v.s.id}/decide`, { method: 'POST', body: JSON.stringify({ version: v.s.version, decision: v.decision, reason: reason || null }) }),
    onSuccess: () => { toast.success('Decision recorded'); setReject(null); setReason(''); void refresh(); }, onError: (e) => toast.error(e.message) });
  const conv = useMutation({ mutationFn: (s: S) => raw<{ owner_id: string; owner_rule: string }>(`/api/strong/${s.id}/convert`, { method: 'POST', body: JSON.stringify({ owner_id: owner || null, reason: reason || null }) }),
    onSuccess: (r) => { toast.success(`Converted to lead (${r.owner_rule.replace('_', ' ')})`); setConvert(null); setOwner(''); setReason(''); void refresh(); void qc.invalidateQueries({ queryKey: ['leads'] }); }, onError: (e) => toast.error(e.message) });
  const post = useMutation({ mutationFn: (s: S) => raw(`/api/strong/${s.id}/comments`, { method: 'POST', body: JSON.stringify({ body: comment[s.id] }) }), onSuccess: () => { void refresh(); setComment({}); } });
  const name = (id: string | null | undefined) => people.data?.items.find((p) => p.id === id)?.display_name ?? 'automatic rule';
  return (
    <div>
      <PageHeader eyebrow="Strong follow-ups" title="Provisional leads to confirm" subtitle="Only a supervisor confirms. Converting creates an owned, locked lead — owner chosen by rule (first connector → requester) unless you override with a reason."
        actions={<div className="flex gap-1">{['pending', 'confirmed', 'rejected'].map((t) => <button key={t} onClick={() => setTab(t)} className={cn('chip h-9 px-3 capitalize', tab === t ? 'bg-navy text-white' : 'border border-line bg-surface')}>{t}</button>)}</div>} />
      <Spinnerless loading={q.isLoading}>
        {q.data?.length === 0 ? <Empty title="Nothing waiting">Agents request strong follow-ups from a committed call.</Empty> : (
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {q.data?.map((s) => (
              <article key={s.id} className="card p-5">
                <div className="flex items-start justify-between gap-2"><div><h3 className="font-semibold">{s.provider_name}</h3><p className="text-sm text-muted">Requested by {s.requested_by_name} · {new Date(s.requested_at).toLocaleString()}</p></div>
                  {s.target_window && <span className="chip bg-info-bg text-info">{s.target_window}</span>}</div>
                {s.note && <p className="mt-3 rounded-xl bg-bg p-3 text-sm">“{s.note}”</p>}
                {s.last_remarks && <p className="mt-2 text-sm text-muted">Last notes: {s.last_remarks}</p>}
                {(s.comments ?? []).length > 0 && <ul className="mt-3 space-y-1 text-sm">{(s.comments ?? []).map((c, i) => <li key={i}><b>{String(c.author)}:</b> {String(c.body)}</li>)}</ul>}
                <div className="mt-3 flex gap-2"><input className="input h-9" placeholder="Ask the agent a question…" value={comment[s.id] ?? ''} onChange={(e) => setComment({ ...comment, [s.id]: e.target.value })} />
                  <button className="btn-secondary h-9" disabled={!comment[s.id]} onClick={() => post.mutate(s)}>Send</button></div>
                <div className="mt-4 flex flex-wrap gap-2">
                  {s.decision === 'pending' && <><button className="btn-primary" onClick={() => decide.mutate({ s, decision: 'confirmed' })}>Confirm</button><button className="btn-secondary" onClick={() => setReject(s)}>Reject</button></>}
                  {s.decision === 'confirmed' && <button className="btn-primary" onClick={() => setConvert(s)}>Convert to lead · owner: {name(s.suggested_owner_id)}</button>}
                </div>
              </article>))}
          </div>)}
      </Spinnerless>
      {reject && <Dialog open onOpenChange={(o) => !o && setReject(null)} title="Reject strong follow-up" footer={<button className="btn-danger" disabled={reason.length < 3} onClick={() => decide.mutate({ s: reject, decision: 'rejected' })}>Reject</button>}>
        <label className="block"><span className="label">Reason (shared with the agent)</span><textarea className="input h-24 py-2" value={reason} onChange={(e) => setReason(e.target.value)} /></label></Dialog>}
      {convert && <Dialog open onOpenChange={(o) => !o && setConvert(null)} title="Convert to lead" description={`Automatic owner: ${name(convert.suggested_owner_id)} (${convert.suggested_owner_rule?.replace('_', ' ')}).`}
        footer={<button className="btn-primary" disabled={conv.isPending || (!!owner && owner !== convert.suggested_owner_id && reason.length < 3)} onClick={() => conv.mutate(convert)}>Convert & lock</button>}>
        <label className="block"><span className="label">Owner</span><select className="input mb-3" value={owner} onChange={(e) => setOwner(e.target.value)}><option value="">Automatic ({name(convert.suggested_owner_id)})</option>{people.data?.items.map((p) => <option key={p.id} value={p.id}>{p.display_name}</option>)}</select></label>
        {owner && owner !== convert.suggested_owner_id && <><label className="block"><span className="label">Override reason (audited)</span><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></label></>}
      </Dialog>}
    </div>
  );
}
