import { useQuery } from '@tanstack/react-query';
import { Crown, Trophy } from 'lucide-react';
import { Avatar, Empty, PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { WIDGETS } from '@/components/widgets';

type Fame = { recent: { id: string; date: string; agent: string; agent_id: string; supervisor: string | null; has_photo: boolean }[]; champions: { month: string; agent: string; agent_id: string; sales: number }[] };

export default function WallOfFame() {
  const q = useQuery({ queryKey: ['fame'], queryFn: () => raw<Fame>('/api/celebrations?months=12') });
  return (
    <div>
      <PageHeader eyebrow="Wall of Fame" title="Every sale, celebrated" subtitle="Recent successful sales and monthly champions. No client details are shown." />
      <Spinnerless loading={q.isLoading}>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <section className="card p-5 lg:col-span-1"><h2 className="mb-3 flex items-center gap-2 font-semibold"><Crown size={18} className="text-[#d4a017]" />Monthly champions</h2>
            {q.data?.champions.length ? <ul className="space-y-3">{q.data.champions.map((c) => <li key={c.month} className="flex items-center gap-3"><Avatar name={c.agent} userId={c.agent_id} hasPhoto size={40} /><div className="flex-1"><b>{c.agent}</b><div className="text-xs text-muted">{c.month}</div></div><span className="chip bg-success-bg text-success">{c.sales} {c.sales === 1 ? 'sale' : 'sales'}</span></li>)}</ul> : <p className="text-sm text-muted">No sales yet.</p>}</section>
          <section className="card p-5 lg:col-span-2"><h2 className="mb-3 flex items-center gap-2 font-semibold"><Trophy size={18} className="text-royal" />Recent sales</h2>
            {q.data?.recent.length ? <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">{q.data.recent.map((r) => <li key={r.id} className="flex items-center gap-3 rounded-xl border border-line p-3"><Avatar name={r.agent} userId={r.agent_id} hasPhoto={r.has_photo} size={44} /><div><b>{r.agent}</b><div className="text-xs text-muted">with {r.supervisor} · {r.date}</div></div></li>)}</ul> : <Empty title="The first sale will appear here" />}</section>
        </div>
      </Spinnerless>
      <div className="mt-4">{WIDGETS.leaderboard!.render({ preset: 'mtd', scope: 'org', credit: 'event' })}</div>
    </div>
  );
}
