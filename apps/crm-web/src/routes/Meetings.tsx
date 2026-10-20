import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addDays, format, startOfWeek } from 'date-fns';
import { CalendarCheck, Link2, RefreshCw, Video } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Empty, PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { dual, fmtZone, PT } from '@/lib/time';

type M = { id: string; lead_id: string; provider_name: string | null; organizer_name: string | null; title: string; starts_at: string; ends_at: string; status: string; meet_url: string | null; google_state: string; notes: string | null; version: number };

export default function Meetings() {
  const qc = useQueryClient();
  const [week, setWeek] = useState(() => startOfWeek(new Date(), { weekStartsOn: 1 }));
  const from = week.toISOString(); const to = addDays(week, 7).toISOString();
  const q = useQuery({ queryKey: ['meetings', from], queryFn: () => raw<M[]>(`/api/meetings?date_from=${from}&date_to=${to}`) });
  const g = useQuery({ queryKey: ['google'], queryFn: () => raw<{ mode: string; configured: boolean; connected: boolean; message: string | null }>('/api/integrations/google/status') });
  const upd = useMutation({ mutationFn: (v: { m: M; status: string }) => raw(`/api/meetings/${v.m.id}`, { method: 'PATCH', body: JSON.stringify({ version: v.m.version, status: v.status }) }),
    onSuccess: () => { toast.success('Meeting updated'); void qc.invalidateQueries({ queryKey: ['meetings'] }); }, onError: (e) => toast.error(e.message) });
  const sync = useMutation({ mutationFn: () => raw<{ changed: number }>('/api/meetings/sync', { method: 'POST' }), onSuccess: (r) => { toast.success(`Synced: ${r.changed} changes`); void qc.invalidateQueries({ queryKey: ['meetings'] }); }, onError: (e) => toast.error(e.message) });
  const days = Array.from({ length: 7 }, (_, i) => addDays(week, i));
  return (
    <div>
      <PageHeader eyebrow="Meetings" title="This week with management" subtitle="Visible to Supervisor+ only. Times shown in Pacific and your local time."
        actions={<>
          <button className="btn-secondary" onClick={() => setWeek(addDays(week, -7))}>←</button><button className="btn-secondary" onClick={() => setWeek(startOfWeek(new Date(), { weekStartsOn: 1 }))}>This week</button><button className="btn-secondary" onClick={() => setWeek(addDays(week, 7))}>→</button>
          {g.data?.configured ? (g.data.connected ? <button className="btn-secondary" onClick={() => sync.mutate()}><RefreshCw size={16} />Sync Google</button> : <a className="btn-primary" href="/api/integrations/google/start"><Link2 size={16} />Connect Google Calendar</a>)
            : <span className="chip h-9 bg-bg px-3 text-muted">Google Calendar: not connected</span>}
        </>} />
      <Spinnerless loading={q.isLoading}>
        {q.data?.length === 0 ? <Empty title="No meetings this week">Schedule meetings from a lead.</Empty> : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-7">
            {days.map((d) => (
              <section key={d.toISOString()} className="card min-h-40 p-3">
                <h3 className="mb-2 text-sm font-semibold">{format(d, 'EEE MMM d')}</h3>
                <ul className="space-y-2">{(q.data ?? []).filter((m) => fmtZone(m.starts_at, PT, 'yyyy-MM-dd') === format(d, 'yyyy-MM-dd')).map((m) => (
                  <li key={m.id} className="rounded-xl border border-line p-2 text-xs">
                    <div className="font-semibold">{m.title}</div><div className="text-muted">{m.provider_name}</div><div>{dual(m.starts_at)}</div>
                    <div className="mt-1 flex flex-wrap gap-1"><span className="chip bg-bg">{m.status}</span>{m.meet_url && <a href={m.meet_url} target="_blank" rel="noopener" className="chip bg-info-bg text-info"><Video size={11} />Meet</a>}</div>
                    {m.status === 'scheduled' && <div className="mt-1 flex gap-1">{[['held', 'Held'], ['no_show', 'No-show'], ['cancelled', 'Cancel']].map(([s, l]) => <button key={s} className="chip bg-surface border border-line" onClick={() => upd.mutate({ m, status: s! })}>{l}</button>)}</div>}
                  </li>))}</ul>
              </section>))}
          </div>)}
      </Spinnerless>
      <p className="mt-4 flex items-center gap-2 text-xs text-muted"><CalendarCheck size={14} />Reschedules and cancellations made in Google update the CRM automatically once Calendar is connected.</p>
    </div>
  );
}
