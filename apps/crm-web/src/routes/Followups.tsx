import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, MessageSquarePlus, Repeat } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { Empty, PageHeader, PhoneLink, Spinnerless } from '@/components/ui/bits';
import { raw, type Schemas } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { dual, ptToUtc } from '@/lib/time';

type F = Schemas['FollowupOut'];
const COLS: [string, string, string][] = [['overdue_3', 'Overdue 3+ days', 'border-t-danger'], ['overdue_1', 'Overdue', 'border-t-warn'], ['due_today', 'Due today', 'border-t-info'], ['upcoming', 'Upcoming', 'border-t-royal'], ['backlog', 'Call again (no date)', 'border-t-line']];

export default function Followups() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const views: [string, string][] = [['mine', 'Mine'], ...(me?.is_group_leader || rank >= 1 ? [['group', 'My group'] as [string, string]] : []), ...(rank >= 1 ? [['team', 'Team'] as [string, string]] : [])];
  const [view, setView] = useState('mine');
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['followups', view, 'open'], queryFn: () => raw<{ items: F[]; counts: Record<string, number> }>(`/api/followups?view=${view}`), refetchInterval: 60_000 });
  const [act, setAct] = useState<{ f: F; kind: 'note' | 'reschedule' | 'done' } | null>(null);
  const [note, setNote] = useState(''); const [date, setDate] = useState(''); const [time, setTime] = useState('10:00');
  const m = useMutation({
    mutationFn: () => raw(`/api/followups/${act!.f.id}/action`, { method: 'POST', body: JSON.stringify({ version: act!.f.version, action: act!.kind, note: note || null, due_at: act!.kind === 'reschedule' && date ? ptToUtc(date, time).toISOString() : null }) }),
    onSuccess: () => { toast.success('Follow-up updated'); setAct(null); setNote(''); qc.invalidateQueries({ queryKey: ['followups'] }); }, onError: (e) => toast.error((e as Error).message),
  });
  return (
    <div>
      <PageHeader eyebrow="Follow-ups" title="Keep every promise" subtitle="Due today (blue), overdue (amber), 3+ days overdue (red, escalated to your supervisor). Each follow-up call is logged as a new activity."
        actions={<div className="flex gap-1">{views.map(([v, l]) => <button key={v} onClick={() => setView(v)} className={cn('chip h-9 px-3', view === v ? 'bg-navy text-white' : 'bg-surface text-ink border border-line')}>{l}</button>)}</div>} />
      <Spinnerless loading={q.isLoading} rows={4}>
        {q.data && q.data.items.length === 0 ? <Empty title="No open follow-ups">Toggle “Follow-up” or pick a date when you log a call.</Empty> : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5">
            {COLS.map(([k, label, border]) => (
              <section key={k} className={cn('card border-t-4 p-3', border)}>
                <h2 className="mb-2 flex items-center justify-between px-1 text-sm font-semibold">{label}<span className="chip bg-bg text-muted">{q.data?.counts[k] ?? 0}</span></h2>
                <ul className="space-y-2">{(q.data?.items ?? []).filter((f) => f.bucket === k).map((f) => (
                  <li key={f.id} className="rounded-xl border border-line p-3 text-sm">
                    <div className="font-semibold">{f.provider_name ?? 'Prospect'}</div>
                    <PhoneLink display={f.phone} />
                    {view !== 'mine' && <div className="text-xs text-muted">{f.owner_name}</div>}
                    {f.due_at && <div className="mt-1 text-xs text-muted">{dual(f.due_at)}</div>}
                    {(f.notes ?? []).length > 0 && <p className="mt-1 text-xs italic text-muted">“{String((f.notes ?? []).at(-1)?.body)}”</p>}
                    <div className="mt-2 flex gap-1">
                      <button className="btn-ghost h-7 px-2" onClick={() => setAct({ f, kind: 'done' })} aria-label="Mark done"><Check size={14} /></button>
                      <button className="btn-ghost h-7 px-2" onClick={() => setAct({ f, kind: 'reschedule' })} aria-label="Reschedule"><Repeat size={14} /></button>
                      <button className="btn-ghost h-7 px-2" onClick={() => setAct({ f, kind: 'note' })} aria-label="Add note"><MessageSquarePlus size={14} /></button>
                    </div>
                  </li>))}</ul>
              </section>
            ))}
          </div>)}
      </Spinnerless>
      {act && (
        <Dialog open onOpenChange={(o) => !o && setAct(null)} title={act.kind === 'done' ? 'Mark follow-up done' : act.kind === 'reschedule' ? 'Reschedule' : 'Add note'} description={act.f.provider_name ?? ''}
          footer={<><button className="btn-secondary" onClick={() => setAct(null)}>Cancel</button><button className="btn-primary" disabled={m.isPending || (act.kind === 'note' && !note) || (act.kind === 'reschedule' && !date)} onClick={() => m.mutate()}>Save</button></>}>
          {act.kind === 'reschedule' && <div className="mb-3 grid grid-cols-2 gap-2"><input type="date" className="input" value={date} onChange={(e) => setDate(e.target.value)} /><input type="time" className="input" value={time} onChange={(e) => setTime(e.target.value)} />
            {date && <p className="col-span-2 text-xs text-muted">{dual(ptToUtc(date, time))}</p>}</div>}
          <label className="label">Note {act.kind === 'note' ? '' : '(optional)'}</label><textarea className="input h-24 py-2" value={note} onChange={(e) => setNote(e.target.value)} />
        </Dialog>)}
    </div>
  );
}
