import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, CheckCheck, ShieldAlert } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { Empty, PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw, type Schemas } from '@/lib/api';
import { cn } from '@/lib/cn';

type F = Schemas['FlagOut'];
const SEV: Record<string, string> = { red: 'bg-danger text-white', amber: 'bg-warn-bg text-warn', info: 'bg-bg text-muted' };
const RES: [string, string][] = [['keep_existing_owner', 'Keep existing owner'], ['transfer', 'Transfer (with incentive split)'], ['shared_line', 'Shared line — different provider'], ['data_error', 'Data error']];

export default function Flags() {
  const qc = useQueryClient();
  const [filt, setFilt] = useState({ status: 'open', severity: '' });
  const [sel, setSel] = useState<string[]>([]);
  const [res, setRes] = useState<F | null>(null);
  const [form, setForm] = useState({ resolution: 'keep_existing_owner', note: '', share: 50 });
  const q = useQuery({ queryKey: ['flags', filt], queryFn: () => raw<{ items: F[]; counts: Record<string, number> }>(`/api/flags?status=${filt.status}${filt.severity ? `&severity=${filt.severity}` : ''}`), refetchInterval: 30_000 });
  const ack = useMutation({ mutationFn: () => raw('/api/flags/ack', { method: 'POST', body: JSON.stringify({ ids: sel }) }), onSuccess: () => { setSel([]); qc.invalidateQueries({ queryKey: ['flags'] }); toast.success('Acknowledged'); } });
  const resolve = useMutation({
    mutationFn: () => raw(`/api/flags/${res!.id}/resolve`, { method: 'POST', body: JSON.stringify({ version: res!.version, resolution: form.resolution, note: form.note,
      incentive_split: form.resolution === 'transfer' && res!.other_user && res!.raised_for_user ? { [res!.other_user]: (100 - form.share) / 100, [res!.raised_for_user]: form.share / 100 } : null }) }),
    onSuccess: () => { setRes(null); qc.invalidateQueries({ queryKey: ['flags'] }); toast.success('Resolved and audited'); }, onError: (e) => toast.error((e as Error).message),
  });
  const red = q.data?.items.filter((f) => f.severity === 'red' && f.status === 'open') ?? [];
  return (
    <div>
      <PageHeader eyebrow="Flags center" title="Duplicates & conflicts" subtitle="Amber: another agent's follow-up or strong follow-up. Red: someone's protected lead — clarify before incentives. Red alerts escalate to the Manager after 24 h."
        actions={<>{sel.length > 0 && <button className="btn-secondary" onClick={() => ack.mutate()}><CheckCheck size={16} />Acknowledge {sel.length} amber</button>}
          <select className="input w-36" value={filt.status} onChange={(e) => setFilt({ ...filt, status: e.target.value })}><option value="open">Open</option><option value="ack">Acknowledged</option><option value="resolved">Resolved</option><option value="all">All</option></select>
          <select className="input w-36" value={filt.severity} onChange={(e) => setFilt({ ...filt, severity: e.target.value })}><option value="">All severities</option><option value="red">Red</option><option value="amber">Amber</option><option value="info">Info</option></select></>} />
      {red.length > 0 && <div role="alert" className="card mb-4 flex items-center gap-3 border-danger bg-danger-bg p-4 text-danger"><ShieldAlert /><b>{red.length} red alert{red.length > 1 ? 's' : ''}:</b> a number that belongs to an active lead was contacted by another agent. Resolve to protect incentives.</div>}
      <Spinnerless loading={q.isLoading}>
        {q.data?.items.length === 0 ? <Empty title="No flags">Clean pipeline. Duplicates are detected the moment a number is saved.</Empty> : (
          <div className="card overflow-x-auto"><table className="w-full min-w-[720px] text-sm">
            <thead><tr className="table-head"><th className="p-3" /><th>Severity</th><th>What</th><th>New entry by</th><th>Existing</th><th>Age</th><th /></tr></thead>
            <tbody>{q.data?.items.map((f) => (
              <tr key={f.id} className="border-t border-line">
                <td className="p-3">{f.severity !== 'red' && f.status === 'open' && <input type="checkbox" aria-label="Select" checked={sel.includes(f.id)} onChange={(e) => setSel(e.target.checked ? [...sel, f.id] : sel.filter((x) => x !== f.id))} />}</td>
                <td><span className={cn('chip', SEV[f.severity])}>{f.severity === 'red' && <AlertTriangle size={12} />}{f.severity}</span></td>
                <td><b>{f.kind.replace(/_/g, ' ')}</b><div className="text-xs text-muted">{f.provider_name}</div></td>
                <td>{f.raised_for_name}</td><td>{f.other_user_name ?? '—'}</td><td className="tabular-nums">{f.age_hours} h</td>
                <td className="pr-3 text-right">{f.status !== 'resolved' && <button className="btn-secondary h-8" onClick={() => { setRes(f); setForm({ resolution: 'keep_existing_owner', note: '', share: 50 }); }}>Resolve</button>}{f.status === 'resolved' && <span className="text-xs text-muted">{f.resolution}</span>}</td>
              </tr>))}</tbody></table></div>)}
      </Spinnerless>
      {res && <Dialog open onOpenChange={(o) => !o && setRes(null)} title="Resolve flag" description={`${res.kind.replace(/_/g, ' ')} · ${res.provider_name ?? ''}`} dismissOnOutside={false}
        footer={<button className="btn-primary" disabled={form.note.length < 3 || resolve.isPending} onClick={() => resolve.mutate()}>Resolve</button>}>
        <div className="space-y-2">{RES.map(([v, l]) => <label key={v} className="flex items-center gap-2 text-sm"><input type="radio" name="res" checked={form.resolution === v} onChange={() => setForm({ ...form, resolution: v })} />{l}</label>)}</div>
        {form.resolution === 'transfer' && <div className="mt-3"><label className="block"><span className="label">Share for {res.raised_for_name}: {form.share}% · {res.other_user_name}: {100 - form.share}%</span><input type="range" min={0} max={100} step={5} value={form.share} onChange={(e) => setForm({ ...form, share: +e.target.value })} className="w-full" /></label></div>}
        <label className="label mt-3">Note (audited)</label><textarea className="input h-20 py-2" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
      </Dialog>}
    </div>
  );
}
