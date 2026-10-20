/** Dashboard widget gallery (§23). Every widget reads read-only, pre-aggregated data from the API
 * and shows its metric definition on hover. Supervisor+ can arrange them; agents get a fixed layout. */
import { useQuery } from '@tanstack/react-query';
import { Link } from '@tanstack/react-router';
import { Activity, CheckCircle2, Phone, Target } from 'lucide-react';
import type { ReactNode } from 'react';
import { call, client, raw } from '@/lib/api';
import { num, pct } from '@/lib/cn';
import { Chart, PALETTE } from './Chart';
import { Avatar, BandBadge, Hint, Skeleton, Stat } from './ui/bits';

export type Filters = { preset: string; scope: string; team_id?: string; credit: 'event' | 'activity' };
type Summary = { period: { label: string }; current: Record<string, number | null> & { band: string; targets: { call_target: number; conn_target: number } }; previous: Record<string, number | null>; definitions: Record<string, string> };

export const useSummary = (f: Filters) => useQuery({
  queryKey: ['summary', f], refetchInterval: 30_000,
  queryFn: () => raw<Summary>(`/api/dashboard/summary?${new URLSearchParams({ preset: f.preset, scope: f.scope, credit: f.credit, ...(f.team_id ? { team_id: f.team_id } : {}) })}`),
});

function Frame({ title, hint, children, action }: { title: string; hint?: string; children: ReactNode; action?: ReactNode }) {
  return (
    <section className="card flex h-full flex-col overflow-hidden p-5">
      <header className="mb-3 flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-1.5 font-semibold">{title}{hint && <Hint text={hint} />}</h3>{action}
      </header>
      <div className="min-h-0 flex-1 overflow-auto" tabIndex={0} role="region" aria-label={title}>{children}</div>
    </section>
  );
}

const Loading = () => <div className="space-y-2"><Skeleton className="h-8 w-1/2" /><Skeleton className="h-24 w-full" /></div>;

function Kpi({ f, metric, label, kind = 'number', icon }: { f: Filters; metric: string; label: string; kind?: 'number' | 'pct'; icon?: ReactNode }) {
  const q = useSummary(f);
  if (!q.data) return <div className="card h-full p-5"><Loading /></div>;
  return <Stat label={label} value={q.data.current[metric] as number} previous={q.data.previous[metric] as number} kind={kind} definition={q.data.definitions[metric]} icon={icon} foot={q.data.period.label} />;
}

function Composite({ f }: { f: Filters }) {
  const q = useSummary(f);
  if (!q.data) return <div className="card h-full p-5"><Loading /></div>;
  const c = q.data.current;
  return (
    <div className="card flex h-full flex-col justify-between p-5">
      <div className="flex items-center justify-between text-sm text-muted"><span className="flex items-center gap-1.5">Composite score<Hint text={q.data.definitions.composite ?? ''} /></span><BandBadge band={c.band} /></div>
      <div className="mt-3 text-3xl font-bold">{pct(c.composite)}</div>
      <div className="mt-2 grid grid-cols-3 gap-2 text-xs text-muted">
        <span>Quality <b className="text-ink">{pct(c.quality)}</b></span><span>Calls/day <b className="text-ink">{num(c.calls_per_day)}</b></span><span>Conn. <b className="text-ink">{pct(c.conn_rate)}</b></span>
      </div>
    </div>
  );
}

function Gauge({ f }: { f: Filters }) {
  const q = useSummary({ ...f, preset: 'today' });
  if (!q.data) return <Frame title="Today's attempts"><Loading /></Frame>;
  const t = q.data.current.targets.call_target;
  const v = q.data.current.attempts ?? 0;
  return (
    <Frame title="Today's attempts" hint={q.data.definitions.attempts}>
      <Chart ariaLabel={`Attempts today ${v} of ${t}`} height={200} option={{
        tooltip: { show: false },
        series: [{ type: 'gauge', startAngle: 210, endAngle: -30, min: 0, max: Math.max(t, v), progress: { show: true, width: 14, itemStyle: { color: '#00afe7' } },
          axisLine: { lineStyle: { width: 14, color: [[1, '#e5eaf4']] } }, axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, pointer: { show: false },
          detail: { valueAnimation: true, offsetCenter: [0, '10%'], fontSize: 30, fontWeight: 700, color: '#14214c', formatter: `{value} / ${t}` }, data: [{ value: v }] }],
      }} />
    </Frame>
  );
}

function Hourly({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['hourly', f.scope, f.team_id], refetchInterval: 30_000, queryFn: () => raw<{ series: { label_pt: string; label_pkt: string; attempts: number; connected: number }[] }>(`/api/dashboard/hourly?scope=${f.scope}${f.team_id ? `&team_id=${f.team_id}` : ''}`) });
  return (
    <Frame title="Hourly activity (PT · PKT)" hint="Attempts and connections by Pacific hour today; labels also show Pakistan time.">
      {!q.data ? <Loading /> : <Chart ariaLabel="Hourly attempts today" height={200} option={{
        xAxis: { type: 'category', data: q.data.series.map((s) => `${s.label_pt}\n${s.label_pkt} PKT`), axisLabel: { fontSize: 10 } }, yAxis: { type: 'value' },
        series: [{ name: 'Attempts', type: 'bar', data: q.data.series.map((s) => s.attempts), itemStyle: { borderRadius: [6, 6, 0, 0] } },
          { name: 'Connected', type: 'line', smooth: true, data: q.data.series.map((s) => s.connected) }],
      }} />}
    </Frame>
  );
}

/** Widget tables live in resizable tiles, so optional columns use container queries (@sm/@md/@lg), not viewport breakpoints. */
const TBL = 'w-full text-sm [&_td]:px-1.5 [&_th]:px-1.5 [&_td:first-child]:pl-0 [&_th:first-child]:pl-0 [&_td]:whitespace-nowrap';

function Leaderboard({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['leaderboard', f.preset, f.credit], refetchInterval: 30_000, queryFn: () => raw<{ items: { rank: number; user_id: string; name: string; has_photo: boolean; attempts: number; conn_rate: number; quality: number; composite: number | null; band: string }[]; me: { rank: number } | null; total: number }>(`/api/dashboard/leaderboard?preset=${f.preset}&credit=${f.credit}&page_size=25`) });
  return (
    <Frame title="Productivity leaderboard" hint="Whole organization, ranked by composite score. Metrics only." action={q.data?.me ? <span className="chip bg-cyan/10 text-royal">You: #{q.data.me.rank} of {q.data.total}</span> : null}>
      {!q.data ? <Loading /> : (
        <div className="@container"><table className={TBL}>
          <thead><tr className="table-head"><th className="pb-2">#</th><th>Agent</th><th className="text-right">Attempts</th><th className="text-right">Conn.</th><th className="hidden text-right @md:table-cell">Quality</th><th className="hidden text-right @sm:table-cell">Score</th></tr></thead>
          <tbody>{q.data.items.map((r) => (
            <tr key={r.user_id} className="border-t border-line">
              <td className="py-2 font-semibold text-muted">{r.rank}</td>
              <td><span className="flex items-center gap-2"><Avatar name={r.name} userId={r.user_id} hasPhoto={r.has_photo} size={26} /><span className="truncate">{r.name}</span></span></td>
              <td className="text-right tabular-nums">{num(r.attempts)}</td><td className="text-right tabular-nums">{pct(r.conn_rate)}</td>
              <td className="hidden text-right tabular-nums @md:table-cell">{pct(r.quality)}</td><td className="hidden text-right @sm:table-cell"><BandBadge band={r.band} /></td>
            </tr>))}</tbody>
        </table></div>)}
    </Frame>
  );
}

function MyFollowups({ view = 'mine' }: { view?: string }) {
  const q = useQuery({ queryKey: ['followups', view, 'open'], queryFn: () => raw<{ counts: Record<string, number> }>(`/api/followups?view=${view}`) });
  const c = q.data?.counts;
  const rows: [string, string, string][] = [['due_today', 'Due today', 'bg-info-bg text-info'], ['overdue_1', 'Overdue', 'bg-warn-bg text-warn'], ['overdue_3', 'Overdue 3+ days', 'bg-danger-bg text-danger'], ['upcoming', 'Upcoming', 'bg-bg text-ink'], ['backlog', 'Call again (no date)', 'bg-bg text-muted']];
  return (
    <Frame title={view === 'mine' ? 'My follow-ups' : view === 'group' ? 'Group follow-ups' : 'Follow-up SLA'} action={<Link to="/followups" className="text-sm font-semibold text-royal">Open</Link>}>
      {!c ? <Loading /> : <ul className="space-y-2">{rows.map(([k, l, s]) => <li key={k} className="flex items-center justify-between text-sm"><span>{l}</span><span className={`chip ${s}`}>{c[k] ?? 0}</span></li>)}</ul>}
    </Frame>
  );
}

function Pipeline() {
  const q = useQuery({ queryKey: ['leads'], queryFn: () => call(client.GET('/api/leads')) });
  const counts: Record<string, number> = {};
  for (const l of q.data?.items ?? []) counts[l.stage_label] = (counts[l.stage_label] ?? 0) + 1;
  return (
    <Frame title="My pipeline" action={<Link to="/leads" className="text-sm font-semibold text-royal">Leads</Link>}>
      {!q.data ? <Loading /> : Object.keys(counts).length === 0 ? <p className="text-sm text-muted">No leads yet. Confirmed strong follow-ups become leads.</p> :
        <ul className="space-y-2">{Object.entries(counts).map(([k, v]) => <li key={k} className="flex justify-between text-sm"><span>{k}</span><b>{v}</b></li>)}</ul>}
    </Frame>
  );
}

function Celebrations() {
  const q = useQuery({ queryKey: ['celebrations'], queryFn: () => raw<{ recent: { id: string; date: string; agent: string; agent_id: string; supervisor: string | null; has_photo: boolean }[] }>('/api/celebrations?months=1') });
  return (
    <Frame title="Recent celebrations" action={<Link to="/wall-of-fame" className="text-sm font-semibold text-royal">Wall of Fame</Link>}>
      {!q.data ? <Loading /> : q.data.recent.length === 0 ? <p className="text-sm text-muted">The next sale will be celebrated here.</p> :
        <ul className="space-y-2">{q.data.recent.slice(0, 6).map((r) => <li key={r.id} className="flex items-center gap-2 text-sm"><Avatar name={r.agent} userId={r.agent_id} hasPhoto={r.has_photo} size={28} /><span className="flex-1"><b>{r.agent}</b> <span className="text-muted">with {r.supervisor}</span></span><span className="text-xs text-muted">{r.date}</span></li>)}</ul>}
    </Frame>
  );
}

function Streak() {
  const q = useQuery({ queryKey: ['series', 'attempts', 'me'], queryFn: () => raw<{ points: { date: string; value: number }[] }>('/api/dashboard/series?metric=attempts&preset=custom&scope=me&start=' + new Date(Date.now() - 60 * 864e5).toISOString().slice(0, 10) + '&end=' + new Date().toISOString().slice(0, 10)) });
  let streak = 0;
  for (const p of [...(q.data?.points ?? [])].reverse()) { const wd = new Date(p.date + 'T12:00:00').getDay(); if (wd === 0 || wd === 6) continue; if (p.value > 0) streak++; else if (streak || p !== q.data?.points.at(-1)) break; }
  return <Stat label="Active-day streak" value={q.data ? streak : null} definition="Consecutive workdays with at least one attempt." icon={<Activity size={18} />} foot="workdays" />;
}

function TeamBoard({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['team-board', f.team_id], refetchInterval: 20_000, queryFn: () => raw<{ agents: { user_id: string; name: string; has_photo: boolean; status: string; attempts: number; target: number; conn_rate: number; points: number; band: string; last_activity_at: string | null; research: number }[] }>(`/api/dashboard/team-board${f.team_id ? `?team_id=${f.team_id}` : ''}`) });
  const dot: Record<string, string> = { active: 'bg-success', idle: 'bg-warn', offline: 'bg-line' };
  return (
    <Frame title="Live team board" hint="Today, Pacific. Active = activity in the last 15 minutes.">
      {!q.data ? <Loading /> : (
        <div className="@container overflow-x-auto" tabIndex={0} role="region" aria-label="Team table">
        <table className={TBL}>
          <thead><tr className="table-head"><th className="pb-2">Agent</th><th className="text-right">Attempts</th><th className="text-right">Conn.</th><th className="hidden text-right @md:table-cell">Points</th><th className="hidden @lg:table-cell">Last activity</th><th className="hidden text-right @sm:table-cell">Band</th></tr></thead>
          <tbody>{q.data.agents.map((r) => (
            <tr key={r.user_id} className="border-t border-line">
              <td className="py-2"><span className="flex min-w-0 items-center gap-2"><span className={`h-2 w-2 shrink-0 rounded-full ${dot[r.status]}`} role="img" aria-label={r.status} /><Avatar name={r.name} userId={r.user_id} hasPhoto={r.has_photo} size={24} /><span className="truncate">{r.name}</span></span></td>
              <td className="whitespace-nowrap text-right tabular-nums">{r.attempts}<span className="text-muted">/{r.target}</span></td><td className="text-right tabular-nums">{pct(r.conn_rate)}</td>
              <td className="hidden text-right tabular-nums @md:table-cell">{r.points}</td><td className="hidden whitespace-nowrap text-muted @lg:table-cell">{r.last_activity_at ? new Date(r.last_activity_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—'}</td>
              <td className="hidden text-right @sm:table-cell"><BandBadge band={r.band} /></td>
            </tr>))}</tbody>
        </table></div>)}
    </Frame>
  );
}

function Funnel({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['funnel', f], queryFn: () => raw<{ stages: { stage: string; value: number }[] }>(`/api/dashboard/funnel?preset=${f.preset}&scope=${f.scope}${f.team_id ? `&team_id=${f.team_id}` : ''}`) });
  return (
    <Frame title="Conversion funnel" hint="Attempts → connected → touch → strong FU (confirmed) → lead → meeting → signed, for the selected period.">
      {!q.data ? <Loading /> : <Chart ariaLabel="Conversion funnel" height={260} option={{ tooltip: { trigger: 'item' }, series: [{ type: 'funnel', sort: 'none', minSize: '8%', gap: 3, label: { formatter: '{b}: {c}' }, data: q.data.stages.map((s) => ({ name: s.stage, value: s.value })) }] }} />}
    </Frame>
  );
}

function Heatmap({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['heatmap', f], queryFn: () => raw<{ cells: { weekday: number; hour_pt: number; attempts: number }[] }>(`/api/dashboard/heatmap?preset=${f.preset === 'today' ? 'mtd' : f.preset}&scope=${f.scope}`) });
  const days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const max = Math.max(1, ...(q.data?.cells ?? []).map((c) => c.attempts));
  return (
    <Frame title="Hour × weekday" hint="Attempts by Pacific hour and weekday.">
      {!q.data ? <Loading /> : <Chart ariaLabel="Attempts heatmap" height={240} option={{ tooltip: { position: 'top' }, grid: { left: 40, right: 10, top: 10, bottom: 44 },
        xAxis: { type: 'category', data: Array.from({ length: 24 }, (_, i) => `${i}`), axisLabel: { interval: 2 }, name: 'PT hour', nameLocation: 'middle', nameGap: 22 }, yAxis: { type: 'category', data: days },
        visualMap: { show: false, min: 0, max, inRange: { color: ['#eef3ff', '#00afe7', '#141956'] } },
        series: [{ type: 'heatmap', data: q.data.cells.map((c) => [c.hour_pt, c.weekday - 1, c.attempts]) }] }} />}
    </Frame>
  );
}

function Hunting({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['hunting', f], queryFn: () => raw<{ agents: { name: string; research: number; attempts: number; no_call: number }[] }>(`/api/dashboard/hunting-vs-outreach?preset=${f.preset}`) });
  return (
    <Frame title="Data hunting vs outreach" hint="Research rows (no outcome toggled) earn no credit and have no lock timer.">
      {!q.data ? <Loading /> : <Chart ariaLabel="Data hunting vs outreach" height={240} option={{ legend: { bottom: 0 }, xAxis: { type: 'category', data: q.data.agents.map((a) => a.name) }, yAxis: { type: 'value' },
        series: [{ name: 'Attempts', type: 'bar', stack: 't', data: q.data.agents.map((a) => a.attempts) }, { name: 'Data hunting', type: 'bar', stack: 't', data: q.data.agents.map((a) => a.research) }, { name: 'Called = No', type: 'bar', stack: 't', data: q.data.agents.map((a) => a.no_call) }] }} />}
    </Frame>
  );
}

function SimpleList({ title, path, render, link }: { title: string; path: string; render: (d: unknown) => ReactNode; link?: string }) {
  const q = useQuery({ queryKey: ['w', path], queryFn: () => raw<unknown>(path), refetchInterval: 30_000 });
  return <Frame title={title} action={link ? <Link to={link} className="text-sm font-semibold text-royal">Open</Link> : null}>{q.data === undefined ? <Loading /> : render(q.data)}</Frame>;
}

function Integrity({ f }: { f: Filters }) {
  const q = useSummary(f);
  const c = q.data?.current;
  return (
    <Frame title="Lock & edit compliance" hint="Duplicates hit, red alerts and supervisor corrections after the lock window, for the period.">
      {!c ? <Loading /> : <ul className="space-y-2 text-sm">
        <li className="flex justify-between"><span>Duplicate hits</span><b>{c.duplicates}</b></li><li className="flex justify-between"><span>Red alerts</span><b className="text-danger">{c.red_flags}</b></li>
        <li className="flex justify-between"><span>Post-lock corrections</span><b>{c.post_lock_edits}</b></li><li className="flex justify-between"><span>Data hunting rows</span><b>{c.research}</b></li></ul>}
    </Frame>
  );
}

function Trend({ f, metrics: list, title, stacked }: { f: Filters; metrics: string[]; title: string; stacked?: boolean }) {
  const preset = f.preset === 'today' || f.preset === 'yesterday' ? 'mtd' : f.preset;
  const qs = useQuery({ queryKey: ['trend', list, preset, f.scope, f.team_id], queryFn: () => Promise.all(list.map((m) => raw<{ points: { date: string; value: number }[] }>(`/api/dashboard/series?metric=${m}&preset=${preset}&scope=${f.scope}${f.team_id ? `&team_id=${f.team_id}` : ''}`))) });
  return (
    <Frame title={title}>
      {!qs.data ? <Loading /> : <Chart ariaLabel={title} height={240} option={{ legend: { bottom: 0 }, xAxis: { type: 'category', data: qs.data[0]?.points.map((p) => p.date.slice(5)) }, yAxis: { type: 'value' },
        series: qs.data.map((s, i) => ({ name: list[i], type: 'line', smooth: true, ...(stacked ? { stack: 'all', areaStyle: { opacity: 0.25 } } : {}), data: s.points.map((p) => p.value) })) }} />}
    </Frame>
  );
}

function Supervisors({ f }: { f: Filters }) {
  return <SimpleList title="Supervisor comparison" path={`/api/dashboard/supervisors?preset=${f.preset === 'today' ? 'mtd' : f.preset}`} render={(d) => (
    <div className="@container"><table className={TBL}><thead><tr className="table-head"><th className="pb-2">Supervisor</th><th className="hidden text-right @sm:table-cell">Agents</th><th className="text-right">Attempts</th><th className="text-right">Conn.</th><th className="text-right">Band</th></tr></thead>
      <tbody>{(d as { supervisors: { supervisor_id: string; supervisor: string; agents: number; attempts: number; conn_rate: number; band: string }[] }).supervisors.map((s) => (
        <tr key={s.supervisor_id} className="border-t border-line"><td className="py-2">{s.supervisor}</td><td className="hidden text-right @sm:table-cell">{s.agents}</td><td className="text-right tabular-nums">{num(s.attempts)}</td><td className="text-right">{pct(s.conn_rate)}</td><td className="text-right"><BandBadge band={s.band} /></td></tr>))}</tbody></table></div>)} />;
}

function Treemap({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['treemap', f.preset], queryFn: () => raw<{ items: { key: string; attempts: number; signups: number }[] }>(`/api/dashboard/breakdown?by=project&preset=${f.preset === 'today' ? 'mtd' : f.preset}`) });
  return <Frame title="Projects (attempts)" hint="Management only. Activity is stamped with the creator's project at entry time.">
    {!q.data ? <Loading /> : <Chart ariaLabel="Project treemap" height={260} option={{ tooltip: { trigger: 'item' }, series: [{ type: 'treemap', roam: false, nodeClick: false, breadcrumb: { show: false }, data: q.data.items.map((i) => ({ name: `${i.key} · ${i.signups} signed`, value: i.attempts })) }] }} />}
  </Frame>;
}

function Distribution({ f }: { f: Filters }) {
  const q = useQuery({ queryKey: ['dist', f.preset], queryFn: () => raw<{ items: { composite: number | null }[] }>(`/api/dashboard/leaderboard?preset=${f.preset}&page_size=200`) });
  const bins = Array.from({ length: 10 }, () => 0);
  for (const r of q.data?.items ?? []) if (r.composite !== null) bins[Math.min(9, Math.floor(r.composite * 10))]!++;
  return <Frame title="Composite distribution" hint="Agents per 10-point composite band.">{!q.data ? <Loading /> : <Chart ariaLabel="Composite distribution" height={220} option={{ xAxis: { type: 'category', data: bins.map((_, i) => `${i * 10}–${i * 10 + 10}%`) }, yAxis: { type: 'value' }, series: [{ type: 'bar', data: bins, itemStyle: { color: PALETTE[1], borderRadius: [6, 6, 0, 0] } }] }} />}</Frame>;
}

function Health() {
  return <SimpleList title="System health" path="/api/dashboard/system-health" link="/security" render={(d) => {
    const h = d as { outbox: Record<string, number>; dead_letters: number; inbox_errors: number; oldest_pending_age_s: number; sse_connections: number; database_bytes: number };
    return <ul className="space-y-2 text-sm">
      <li className="flex justify-between"><span>Outbox pending</span><b>{h.outbox.pending ?? 0}</b></li><li className="flex justify-between"><span>Dead letters</span><b className={h.dead_letters ? 'text-danger' : ''}>{h.dead_letters}</b></li>
      <li className="flex justify-between"><span>Oldest pending</span><b>{Math.round(h.oldest_pending_age_s)} s</b></li><li className="flex justify-between"><span>Live connections (this node)</span><b>{h.sse_connections}</b></li>
      <li className="flex justify-between"><span>Inbox errors</span><b>{h.inbox_errors}</b></li><li className="flex justify-between"><span>Database size</span><b>{(h.database_bytes / 1e6).toFixed(1)} MB</b></li></ul>;
  }} />;
}

export const WIDGETS: Record<string, { title: string; render: (f: Filters) => ReactNode; w: number; h: number }> = {
  kpi_attempts: { title: 'Call attempts', w: 3, h: 3, render: (f) => <Kpi f={f} metric="attempts" label="Call attempts" icon={<Phone size={18} />} /> },
  kpi_conn_rate: { title: 'Connection rate', w: 3, h: 3, render: (f) => <Kpi f={f} metric="conn_rate" label="Connection rate" kind="pct" icon={<Activity size={18} />} /> },
  kpi_points: { title: 'Points', w: 3, h: 3, render: (f) => <Kpi f={f} metric="points" label={f.credit === 'event' ? 'Points (event time)' : 'Points (activity date)'} icon={<Target size={18} />} /> },
  kpi_quality: { title: 'Quality', w: 3, h: 3, render: (f) => <Kpi f={f} metric="quality" label="Quality" kind="pct" /> },
  kpi_signups: { title: 'Sign-ups', w: 3, h: 3, render: (f) => <Kpi f={f} metric="signups" label="Confirmed sales" icon={<CheckCircle2 size={18} />} /> },
  kpi_calls_per_day: { title: 'Calls / day / rep', w: 3, h: 3, render: (f) => <Kpi f={f} metric="calls_per_day" label="Calls / day / rep" /> },
  kpi_composite: { title: 'Composite', w: 3, h: 3, render: (f) => <Composite f={f} /> },
  gauge_attempts: { title: 'Attempts vs target', w: 4, h: 4, render: (f) => <Gauge f={f} /> },
  hourly_sparkline: { title: 'Hourly activity', w: 8, h: 4, render: (f) => <Hourly f={f} /> },
  my_followups: { title: 'My follow-ups', w: 4, h: 4, render: () => <MyFollowups /> },
  my_pipeline: { title: 'My pipeline', w: 4, h: 4, render: () => <Pipeline /> },
  leaderboard: { title: 'Leaderboard', w: 8, h: 6, render: (f) => <Leaderboard f={f} /> },
  celebrations: { title: 'Celebrations', w: 4, h: 5, render: () => <Celebrations /> },
  streak: { title: 'Streak', w: 3, h: 3, render: () => <Streak /> },
  group_board: { title: 'Group follow-ups', w: 4, h: 4, render: () => <MyFollowups view="group" /> },
  group_leaderboard: { title: 'Group KPIs', w: 4, h: 3, render: (f) => <Kpi f={{ ...f, scope: 'group' }} metric="attempts" label="Group attempts" /> },
  team_board: { title: 'Live team board', w: 8, h: 6, render: (f) => <TeamBoard f={f} /> },
  funnel: { title: 'Funnel', w: 4, h: 6, render: (f) => <Funnel f={f} /> },
  heatmap: { title: 'Heatmap', w: 8, h: 5, render: (f) => <Heatmap f={f} /> },
  followup_sla: { title: 'Follow-up SLA', w: 4, h: 4, render: () => <MyFollowups view="team" /> },
  strong_queue: { title: 'Strong FU queue', w: 4, h: 4, render: () => <SimpleList title="Strong follow-up queue" path="/api/strong" link="/strong" render={(d) => { const l = d as { id: string; provider_name: string | null; requested_by_name: string | null }[]; return l.length ? <ul className="space-y-1 text-sm">{l.slice(0, 6).map((s) => <li key={s.id} className="flex justify-between"><span className="truncate">{s.provider_name}</span><span className="text-muted">{s.requested_by_name}</span></li>)}</ul> : <p className="text-sm text-muted">No pending requests.</p>; }} /> },
  flag_counter: { title: 'Flags', w: 4, h: 3, render: () => <SimpleList title="Open flags" path="/api/flags" link="/flags" render={(d) => { const c = (d as { counts: Record<string, number> }).counts; return <div className="flex gap-3"><span className="chip bg-danger-bg text-danger">Red {c.red ?? 0}</span><span className="chip bg-warn-bg text-warn">Amber {c.amber ?? 0}</span><span className="chip bg-bg text-muted">Info {c.info ?? 0}</span></div>; }} /> },
  hunting_vs_outreach: { title: 'Hunting vs outreach', w: 8, h: 5, render: (f) => <Hunting f={f} /> },
  lock_compliance: { title: 'Lock compliance', w: 4, h: 4, render: (f) => <Integrity f={f} /> },
  meetings_week: { title: 'Meetings this week', w: 4, h: 4, render: () => <SimpleList title="Meetings this week" link="/meetings" path={`/api/meetings?date_to=${new Date(Date.now() + 7 * 864e5).toISOString()}&date_from=${new Date().toISOString()}`} render={(d) => { const l = d as { id: string; title: string; starts_at: string; provider_name: string | null }[]; return l.length ? <ul className="space-y-1 text-sm">{l.map((m) => <li key={m.id}><b>{new Date(m.starts_at).toLocaleString([], { weekday: 'short', hour: 'numeric', minute: '2-digit' })}</b> · {m.provider_name}</li>)}</ul> : <p className="text-sm text-muted">No meetings scheduled.</p>; }} /> },
  target_trend: { title: 'Target attainment', w: 8, h: 5, render: (f) => <Trend f={f} metrics={['attempts', 'connected']} title="Attempts & connections trend" /> },
  supervisor_comparison: { title: 'Supervisors', w: 8, h: 5, render: (f) => <Supervisors f={f} /> },
  team_trends: { title: 'Team trends', w: 8, h: 5, render: (f) => <Trend f={f} metrics={['attempts', 'connected', 'any_touch']} title="Team trends" stacked /> },
  composite_distribution: { title: 'Distribution', w: 4, h: 5, render: (f) => <Distribution f={f} /> },
  domain_project_treemap: { title: 'Projects', w: 6, h: 5, render: (f) => <Treemap f={f} /> },
  cohort_conversion: { title: 'Conversion', w: 4, h: 4, render: (f) => <Trend f={f} metrics={['strong_confirmed', 'leads', 'signups']} title="Pipeline conversion" /> },
  movers: { title: 'Movers', w: 4, h: 5, render: (f) => <Leaderboard f={{ ...f, preset: 'week' }} /> },
  capacity: { title: 'Capacity', w: 4, h: 4, render: (f) => <Kpi f={f} metric="calls_per_day" label="Calls / day / rep (capacity)" /> },
  system_health: { title: 'System health', w: 4, h: 5, render: () => <Health /> },
  security_center: { title: 'Security center', w: 4, h: 3, render: () => <SimpleList title="Security" path="/api/flags?severity=red" link="/security" render={(d) => <p className="text-sm">Open red alerts: <b className="text-danger">{(d as { counts: Record<string, number> }).counts.red ?? 0}</b></p>} /> },
};
