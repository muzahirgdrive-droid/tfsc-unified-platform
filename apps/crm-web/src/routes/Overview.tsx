import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { LayoutGrid, Plus, RotateCcw, Save } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Responsive, WidthProvider, type Layout } from 'react-grid-layout';
import 'react-grid-layout/css/styles.css';
import 'react-resizable/css/styles.css';
import { toast } from 'sonner';
import { WIDGETS, type Filters } from '@/components/widgets';
import { Dialog } from '@/components/ui/Dialog';
import { PageHeader } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { useCrmEvents } from '@/lib/sse';

const Grid = WidthProvider(Responsive);
type W = { id: string; x: number; y: number; w: number; h: number; filters?: Record<string, string> };
type LayoutResp = { widgets: W[]; customizable: boolean; allowed_widgets: string[]; source: string };

export const PRESETS: [string, string][] = [['today', 'Today (PT)'], ['yesterday', 'Yesterday'], ['week', 'This week'], ['mtd', 'Month to date'], ['last_month', 'Last month']];

export function FilterBar({ f, setF, rank }: { f: Filters; setF: (f: Filters) => void; rank: number }) {
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => raw<{ id: string; name: string; kind: string }[]>('/api/teams'), enabled: rank >= 1 });
  return (
    <div className="card mb-6 flex flex-wrap items-center gap-3 bg-gradient-to-r from-navy to-royal p-4 text-white">
      <div className="mr-auto"><div className="eyebrow text-cyan">Performance at a glance</div><div className="text-lg font-semibold">Make your next conversation count.</div></div>
      <label className="text-xs text-white/70">Period
        <select className="input mt-1 h-9 w-40 text-ink" value={f.preset} onChange={(e) => setF({ ...f, preset: e.target.value })}>{PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </label>
      {rank >= 1 && <label className="text-xs text-white/70">Scope
        <select className="input mt-1 h-9 w-48 text-ink" value={f.team_id ?? f.scope} onChange={(e) => {
          const v = e.target.value;
          setF(['me', 'team', 'org'].includes(v) ? { ...f, scope: v, team_id: undefined } : { ...f, scope: 'team', team_id: v });
        }}>
          <option value="team">My scope</option><option value="me">Just me</option>{rank >= 4 && <option value="org">Whole organization</option>}
          {(teams.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.kind === 'group' ? 'Group' : 'Team'}: {t.name}</option>)}
        </select></label>}
      <label className="text-xs text-white/70">Points credited
        <select className="input mt-1 h-9 w-44 text-ink" value={f.credit} onChange={(e) => setF({ ...f, credit: e.target.value as Filters['credit'] })}>
          <option value="event">When milestone happened</option><option value="activity">To the activity date</option></select></label>
    </div>
  );
}

export default function Overview() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const qc = useQueryClient();
  const [f, setF] = useState<Filters>({ preset: 'today', scope: rank >= 1 ? 'team' : 'me', credit: 'event' });
  const layout = useQuery({ queryKey: ['layout'], queryFn: () => raw<LayoutResp>('/api/dashboard/layout') });
  const [widgets, setWidgets] = useState<W[]>([]);
  const [dirty, setDirty] = useState(false);
  const [gallery, setGallery] = useState(false);
  useEffect(() => { if (layout.data) { setWidgets(layout.data.widgets.filter((w) => WIDGETS[w.id]).map((w) => ({ ...w, w: Math.max(w.w, WIDGETS[w.id]!.w === 3 ? 3 : 4), h: Math.max(w.h, WIDGETS[w.id]!.h) }))); setDirty(false); } }, [layout.data]);
  // live: SSE nudges refetch the (read-only) dashboard data
  const onEvent = useCallback(() => { qc.invalidateQueries({ queryKey: ['summary'] }); qc.invalidateQueries({ queryKey: ['leaderboard'] }); }, [qc]);
  useCrmEvents(onEvent);
  const save = useMutation({ mutationFn: () => raw('/api/dashboard/layout', { method: 'PUT', body: JSON.stringify({ widgets: widgets.map(({ id, x, y, w, h }) => ({ id, x, y, w, h, filters: {} })) }) }),
    onSuccess: () => { toast.success('Dashboard layout saved'); setDirty(false); qc.invalidateQueries({ queryKey: ['layout'] }); }, onError: (e) => toast.error((e as Error).message) });
  const reset = useMutation({ mutationFn: () => raw('/api/dashboard/layout', { method: 'DELETE' }), onSuccess: () => qc.invalidateQueries({ queryKey: ['layout'] }) });
  const customizable = !!layout.data?.customizable;
  const rgl: Layout[] = useMemo(() => widgets.map((w) => ({ i: w.id, x: w.x, y: w.y, w: w.w, h: w.h, minW: 3, minH: 3 })), [widgets]);
  const hello = new Date().getHours() < 12 ? 'Good morning' : new Date().getHours() < 18 ? 'Good afternoon' : 'Good evening';
  return (
    <div>
      <PageHeader eyebrow="A better view of your day" title={`${hello}, ${me?.display_name.split(' ')[0] ?? ''}.`} subtitle="From the first conversation to the next client relationship."
        actions={customizable ? <>
          <button className="btn-secondary" onClick={() => setGallery(true)}><Plus size={16} />Widgets</button>
          <button className="btn-secondary" onClick={() => reset.mutate()}><RotateCcw size={16} />Reset</button>
          <button className="btn-primary" disabled={!dirty || save.isPending} onClick={() => save.mutate()}><Save size={16} />Save layout</button>
        </> : null} />
      <FilterBar f={f} setF={setF} rank={rank} />
      {customizable ? (
        <Grid className="-mx-2" layouts={{ lg: rgl, md: rgl }} breakpoints={{ lg: 1100, md: 768, sm: 0 }} cols={{ lg: 12, md: 12, sm: 1 }} rowHeight={48} margin={[16, 16]}
          draggableHandle=".card header, .card > div:first-child" onLayoutChange={(l, all) => {
            if (!all.lg) return;
            const map = new Map(all.lg.map((x) => [x.i, x]));
            setWidgets((ws) => { const n = ws.map((w) => { const g = map.get(w.id); return g ? { ...w, x: g.x, y: g.y, w: g.w, h: g.h } : w; }); return n; });
            setDirty(true); void l;
          }}>
          {widgets.map((w) => <div key={w.id}>{WIDGETS[w.id]!.render(f)}</div>)}
        </Grid>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-12">
          {widgets.map((w) => <div key={w.id} className="md:col-span-6 xl:col-span-[var(--span)]" style={{ ['--span' as string]: String(Math.min(12, w.w * (WIDGETS[w.id]!.w >= 8 ? 1 : 1))) }}>{WIDGETS[w.id]!.render(f)}</div>)}
        </div>
      )}
      <Dialog open={gallery} onOpenChange={setGallery} title="Widget gallery" description="Add or remove widgets. Data stays read-only; your layout is saved per user." wide>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          {(layout.data?.allowed_widgets ?? []).filter((id) => WIDGETS[id]).map((id) => {
            const on = widgets.some((w) => w.id === id);
            return (
              <button key={id} className={`card flex items-center gap-3 p-3 text-left ${on ? 'border-royal' : ''}`} onClick={() => {
                setWidgets((ws) => on ? ws.filter((w) => w.id !== id) : [...ws, { id, x: 0, y: 999, w: WIDGETS[id]!.w, h: WIDGETS[id]!.h }]); setDirty(true);
              }}><LayoutGrid size={16} className="text-royal" /><span className="flex-1 text-sm font-medium">{WIDGETS[id]!.title}</span><span className={`chip ${on ? 'bg-success-bg text-success' : 'bg-bg text-muted'}`}>{on ? 'Shown' : 'Add'}</span></button>
            );
          })}
        </div>
      </Dialog>
    </div>
  );
}
