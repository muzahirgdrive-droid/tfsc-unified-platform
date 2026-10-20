import { useQuery } from '@tanstack/react-query';
import { WIDGETS } from '@/components/widgets';
import { PageHeader } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';

export default function Security() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const scraping = useQuery({ queryKey: ['flags', 'scraping'], queryFn: () => raw<{ items: { id: string; raised_for_name: string | null; created_at: string; status: string }[] }>('/api/flags?kind=scraping_suspected&status=all') });
  return (
    <div>
      <PageHeader eyebrow="Security center" title="Protection & system health" subtitle="Read-only for Directors; full control for the Super Admin. Watermarks deter and trace screenshots; they cannot prevent them." />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {rank >= 4 && <div>{WIDGETS.system_health!.render({ preset: 'today', scope: 'org', credit: 'event' })}</div>}
        <div className="card p-5 lg:col-span-2"><h3 className="mb-3 font-semibold">Scraping suspicion</h3>
          {scraping.data?.items.length ? <ul className="space-y-1 text-sm">{scraping.data.items.map((f) => <li key={f.id}>{f.raised_for_name} · {new Date(f.created_at).toLocaleString()} · {f.status}</li>)}</ul> : <p className="text-sm text-muted">No read-budget breaches. More than 500 distinct prospects viewed per hour throttles the user and raises a red flag here.</p>}</div>
      </div>
    </div>
  );
}
