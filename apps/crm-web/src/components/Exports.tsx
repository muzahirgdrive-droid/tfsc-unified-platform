/** Approval-logged exports (§21): Managers request, Directors approve; the ZIP is AES-encrypted and its password is
 * shown once to the requester. Agents and Supervisors never see this panel (and the API refuses them). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Download, KeyRound, ShieldCheck } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { raw } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { businessDate } from '@/lib/time';

type Exp = { id: string; kind: string; status: string; start: string; end: string; reason: string | null; requested_by: string; requested_by_name: string;
  approved_by_name: string | null; row_count: number | null; expires_at: string | null; password_revealed: boolean; downloads: number; error: string | null; created_at: string };

const TONE: Record<string, string> = { requested: 'bg-info-bg text-info', ready: 'bg-success-bg text-success', rejected: 'bg-danger-bg text-danger' };

export function ExportsPanel() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ['exports'], queryFn: () => raw<Exp[]>('/api/exports') });
  const today = businessDate();
  const [form, setForm] = useState({ kind: 'activities', start: today.slice(0, 8) + '01', end: today, reason: '' });
  const [pw, setPw] = useState<string | null>(null);
  const done = () => qc.invalidateQueries({ queryKey: ['exports'] });
  const req = useMutation({ mutationFn: () => raw('/api/exports', { method: 'POST', body: JSON.stringify(form) }),
    onSuccess: () => { toast.success('Export requested. A Director must approve it.'); setForm({ ...form, reason: '' }); done(); }, onError: (e) => toast.error((e as Error).message) });
  const decide = useMutation({ mutationFn: (v: { id: string; approve: boolean }) => raw(`/api/exports/${v.id}/decision`, { method: 'POST', body: JSON.stringify({ approve: v.approve }) }),
    onSuccess: () => { toast.success('Decision recorded'); done(); }, onError: (e) => toast.error((e as Error).message) });
  const reveal = useMutation({ mutationFn: (id: string) => raw<{ password: string }>(`/api/exports/${id}/password`, { method: 'POST' }),
    onSuccess: (r) => { setPw(r.password); done(); }, onError: (e) => toast.error((e as Error).message) });
  const download = async (id: string) => {
    const r = await fetch(`/api/exports/${id}/download`, { credentials: 'same-origin' });
    if (!r.ok) return toast.error('This export is not available.');
    const url = URL.createObjectURL(await r.blob());
    const a = document.createElement('a'); a.href = url; a.download = `tfsc-crm-export-${id.slice(0, 8)}.zip`; a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000); done();
  };
  return (
    <section className="card p-5" aria-labelledby="exports-h">
      <h3 id="exports-h" className="mb-1 flex items-center gap-2 font-semibold"><ShieldCheck size={18} className="text-royal" />Exports</h3>
      <p className="mb-4 text-sm text-muted">Every export needs a Director's approval, is limited to 50,000 rows of your scope, carries a watermark (who, when, export id) and is an AES-encrypted ZIP. The password is shown once, to the requester.</p>
      <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-5">
        <div><label className="label" htmlFor="ex-kind">Data</label><select id="ex-kind" className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="activities">Activities</option><option value="leads">Leads</option></select></div>
        <div><label className="label" htmlFor="ex-s">From</label><input id="ex-s" type="date" className="input" value={form.start} onChange={(e) => setForm({ ...form, start: e.target.value })} /></div>
        <div><label className="label" htmlFor="ex-e">To</label><input id="ex-e" type="date" className="input" value={form.end} onChange={(e) => setForm({ ...form, end: e.target.value })} /></div>
        <div className="sm:col-span-2"><label className="label" htmlFor="ex-r">Reason</label><input id="ex-r" className="input" placeholder="e.g. Monthly compliance review" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} /></div>
      </div>
      <button className="btn-primary" disabled={req.isPending || form.reason.trim().length < 5} onClick={() => req.mutate()}>Request export</button>
      <div className="mt-5 overflow-x-auto" tabIndex={0} role="region" aria-label="Export requests"><table className="w-full min-w-[720px] text-sm">
        <thead><tr className="table-head"><th className="pb-2">Requested</th><th>Data</th><th>Period</th><th>By</th><th>Status</th><th className="text-right">Rows</th><th /></tr></thead>
        <tbody>{list.data?.map((x) => (
          <tr key={x.id} className="border-t border-line align-top">
            <td className="py-2 whitespace-nowrap">{new Date(x.created_at).toLocaleString()}</td><td className="capitalize">{x.kind}</td>
            <td className="whitespace-nowrap">{x.start} – {x.end}</td><td>{x.requested_by_name}<div className="text-xs text-muted">{x.reason}</div></td>
            <td><span className={cn('chip capitalize', TONE[x.status] ?? 'bg-bg')}>{x.status}</span>{x.error && <div className="text-xs text-danger">{x.error}</div>}</td>
            <td className="text-right tabular-nums">{x.row_count?.toLocaleString() ?? '—'}</td>
            <td className="py-2 text-right whitespace-nowrap">
              {x.status === 'requested' && rank >= 3 && <><button className="btn-primary h-8 px-3" onClick={() => decide.mutate({ id: x.id, approve: true })}>Approve</button> <button className="btn-secondary h-8 px-3" onClick={() => decide.mutate({ id: x.id, approve: false })}>Reject</button></>}
              {x.status === 'ready' && x.requested_by === me?.id && <>{!x.password_revealed && <button className="btn-secondary h-8 px-3" onClick={() => reveal.mutate(x.id)}><KeyRound size={14} />Password</button>} <button className="btn-secondary h-8 px-3" onClick={() => download(x.id)}><Download size={14} />Download</button></>}
            </td>
          </tr>))}</tbody></table>
        {list.data && !list.data.length && <p className="py-4 text-sm text-muted">No exports yet.</p>}</div>
      {pw && <Dialog open onOpenChange={(o) => !o && setPw(null)} title="ZIP password (shown once)" dismissOnOutside={false} footer={<button className="btn-primary" onClick={() => setPw(null)}>I have stored it</button>}>
        <p className="mb-3 text-sm text-muted">Store this password now. It cannot be shown again; a lost password needs a new export.</p>
        <code className="block rounded-xl bg-bg p-3 text-base font-semibold tracking-wide select-all">{pw}</code>
      </Dialog>}
    </section>
  );
}
