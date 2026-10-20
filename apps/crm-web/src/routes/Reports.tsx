import { useState } from 'react';
import { ExportsPanel } from '@/components/Exports';
import { WIDGETS, useSummary, type Filters } from '@/components/widgets';
import { Hint, PageHeader } from '@/components/ui/bits';
import { rankOf, useAuth } from '@/lib/auth';
import { num, pct } from '@/lib/cn';
import { FilterBar } from './Overview';

const ROWS: [string, string, 'n' | 'p'][] = [['attempts', 'Attempts', 'n'], ['connected', 'Connected', 'n'], ['conn_rate', 'Connection rate', 'p'], ['sms', 'SMS touches', 'n'], ['emails', 'Email touches', 'n'], ['any_touch', 'Any touch', 'n'],
  ['research', 'Data hunting rows', 'n'], ['research_conversion', 'Research → attempt', 'p'], ['points', 'Points', 'n'], ['quality', 'Quality', 'p'], ['calls_per_day', 'Calls/day/rep (roster)', 'n'], ['calls_per_day_reporting', 'Calls/day per reporting rep (workbook)', 'n'],
  ['composite', 'Composite', 'p'], ['followups_created', 'Follow-ups created', 'n'], ['strong_requested', 'Strong FU requested', 'n'], ['strong_confirmed', 'Strong FU confirmed', 'n'], ['leads', 'Leads qualified', 'n'], ['signups', 'Sign-ups', 'n'],
  ['duplicates', 'Duplicate hits', 'n'], ['red_flags', 'Red alerts', 'n'], ['post_lock_edits', 'Post-lock edits', 'n']];

export default function Reports() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const [f, setF] = useState<Filters>({ preset: 'mtd', scope: 'team', credit: 'event' });
  const s = useSummary(f);
  return (
    <div>
      <PageHeader eyebrow="Reports" title="Metric catalog" subtitle="Every metric with its definition, the selected period and the previous period of the same length. Data is read-only." />
      <FilterBar f={f} setF={setF} rank={rank} />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <div className="card overflow-x-auto p-5 xl:col-span-2" tabIndex={0} role="region" aria-label="Report metrics">
          <table className="w-full text-sm"><thead><tr className="table-head"><th className="pb-2">Metric</th><th className="text-right">{s.data?.period.label ?? 'Period'}</th><th className="text-right">Previous</th><th className="text-right">Change</th></tr></thead>
            <tbody>{ROWS.map(([k, l, t]) => {
              const cur = s.data?.current[k] as number | null | undefined; const prev = s.data?.previous[k] as number | null | undefined;
              const fmt = (x: number | null | undefined) => (t === 'p' ? pct(x) : num(x));
              const d = cur !== undefined && cur !== null && prev !== undefined && prev !== null ? cur - prev : null;
              return <tr key={k} className="border-t border-line"><td className="py-2"><span className="flex items-center gap-1.5">{l}{s.data?.definitions[k] && <Hint text={s.data.definitions[k]!} />}</span></td><td className="text-right tabular-nums font-semibold">{fmt(cur)}</td><td className="text-right tabular-nums text-muted">{fmt(prev)}</td>
                <td className={`text-right tabular-nums ${d === null || d === 0 ? 'text-muted' : d > 0 ? 'text-success' : 'text-danger'}`}>{d === null ? '—' : `${d > 0 ? '+' : ''}${t === 'p' ? pct(d) : num(d)}`}</td></tr>;
            })}</tbody></table>
          <p className="mt-3 text-xs text-muted">Band: {String(s.data?.current.band ?? '—')} · workdays {String(s.data?.current.workdays ?? '—')} · roster-days {String(s.data?.current.roster_days ?? '—')} · targets {s.data?.current.targets.call_target} calls/day, {pct(s.data?.current.targets.conn_target)} connection.</p>
        </div>
        <div className="space-y-4">{WIDGETS.funnel!.render(f)}</div>
        <div className="xl:col-span-3">{WIDGETS.team_trends!.render(f)}</div>
        {rank >= 2 && <div className="xl:col-span-2">{WIDGETS.supervisor_comparison!.render(f)}</div>}
        {rank >= 2 && <div>{WIDGETS.domain_project_treemap!.render(f)}</div>}
        {rank >= 2 && <div className="xl:col-span-3"><ExportsPanel /></div>}
      </div>
    </div>
  );
}
