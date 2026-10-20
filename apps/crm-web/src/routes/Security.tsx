import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, RotateCcw, ShieldAlert, TriangleAlert } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { ExportsPanel } from '@/components/Exports';
import { WIDGETS } from '@/components/widgets';
import { PageHeader, Stat } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { asText } from '@/lib/text';

type Count = { key: string; count: number };
type Overview = { open_red_flags: Count[]; scraping_suspected_7d: number; failed_logins_24h: number; failed_logins_by_user: Count[]; active_sessions: number;
  supervisors_without_sso: number; offboarded_30d: number; exports_pending: number; exports_ready: number; outbox_pending: number; outbox_dead: number;
  sse_connections: number; audit_rows: number; last_anchor: { day: string; rows: number; sink: string; head: string; remote: boolean } | null; jobs: { job: string; last_run: string | null }[] };
type AuditRow = { id: number; at: string; actor: string | null; action: string; resource: string; resource_id: string | null; ip: string | null; after: Record<string, unknown> | null };
type Verify = { ok: boolean; rows: number; broken_links: number[]; altered_rows: number[]; anchors: number; anchors_failed: string[]; remote_anchors: number | null; remote_failed: string[] };
type Session = { id: string; user_id: string; user: string; created_at: string; last_seen_at: string; ip: string | null; user_agent: string | null };
type Dead = { id: number; topic: string; attempts: number; last_error: string | null; created_at: string };
const TABS = ['overview', 'audit', 'sessions', 'exports', 'deliveries'] as const;

export default function Security() {
  const { me } = useAuth();
  const [tab, setTab] = useState<(typeof TABS)[number]>('overview');
  return (
    <div>
      <PageHeader eyebrow="Security center" title="Protection & system health" subtitle="Read-only for Directors; full control for the Super Admin. Watermarks deter and trace screenshots; they cannot prevent them." />
      <div className="mb-4 flex flex-wrap gap-1" role="tablist">{TABS.map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={cn('chip h-9 px-3 capitalize', tab === t ? 'bg-navy text-white' : 'border border-line bg-surface')}>{t}</button>)}</div>
      {tab === 'overview' && <OverviewTab super={rankOf(me) >= 4} />}
      {tab === 'audit' && <AuditTab super={rankOf(me) >= 4} />}
      {tab === 'sessions' && <SessionsTab super={rankOf(me) >= 4} />}
      {tab === 'exports' && <ExportsPanel />}
      {tab === 'deliveries' && <DeadTab super={rankOf(me) >= 4} />}
    </div>
  );
}

function OverviewTab({ super: isSuper }: { super: boolean }) {
  const q = useQuery({ queryKey: ['security-overview'], queryFn: () => raw<Overview>('/api/security/overview'), refetchInterval: 30_000 });
  const scraping = useQuery({ queryKey: ['flags', 'scraping'], queryFn: () => raw<{ items: { id: string; raised_for_name: string | null; created_at: string; status: string }[] }>('/api/flags?kind=scraping_suspected&status=all') });
  const o = q.data;
  const red = o?.open_red_flags.reduce((n, x) => n + x.count, 0) ?? null;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <Stat label="Open red flags" value={red} icon={<ShieldAlert size={18} />} definition="Unresolved red flags (protected-lead conflicts, scraping suspicion). Unresolved for 24 h, they escalate to the Manager." />
        <Stat label="Failed logins (24 h)" value={o?.failed_logins_24h ?? null} definition="Wrong password or inactive account, from the audit log." />
        <Stat label="Active sessions" value={o?.active_sessions ?? null} definition="Signed-in sessions within the idle (30 min) and absolute (12 h) limits." />
        <Stat label="Supervisor+ without SSO" value={o?.supervisors_without_sso ?? null} definition="Active Supervisor-and-above accounts with no linked Keycloak identity (MFA is enforced by Keycloak)." />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card p-5"><h3 className="mb-3 font-semibold">Audit chain</h3>
          <dl className="space-y-1 text-sm"><Row k="Entries" v={o?.audit_rows.toLocaleString()} /><Row k="Last daily anchor" v={o?.last_anchor ? `${o.last_anchor.day} · ${o.last_anchor.rows.toLocaleString()} rows` : 'none yet'} />
            <Row k="Anchor head" v={o?.last_anchor?.head ? `${o.last_anchor.head}…` : '—'} /><Row k="Anchor sink" v={o?.last_anchor ? `${o.last_anchor.sink}${o.last_anchor.remote ? ' + object-lock bucket' : ''}` : '—'} /></dl></div>
        <div className="card p-5"><h3 className="mb-3 font-semibold">Deliveries & real time</h3>
          <dl className="space-y-1 text-sm"><Row k="Outbox pending" v={o?.outbox_pending} /><Row k="Dead letters" v={o?.outbox_dead} warn={!!o?.outbox_dead} />
            <Row k="Live connections (this node)" v={o?.sse_connections} /><Row k="Exports awaiting approval" v={o?.exports_pending} /><Row k="Offboarded (30 days)" v={o?.offboarded_30d} /></dl></div>
        <div className="card p-5"><h3 className="mb-3 font-semibold">Scheduled jobs</h3>
          <ul className="space-y-1 text-sm">{o?.jobs.length ? o.jobs.map((j) => <li key={j.job} className="flex justify-between gap-2"><span>{j.job.replace('_', ' ')}</span><span className="text-muted">{j.last_run ? new Date(j.last_run).toLocaleString() : 'never'}</span></li>) : <li className="text-muted">No runs recorded yet.</li>}</ul></div>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {isSuper && <div>{WIDGETS.system_health!.render({ preset: 'today', scope: 'org', credit: 'event' })}</div>}
        <div className="card p-5"><h3 className="mb-3 font-semibold">Failed logins by account (24 h)</h3>
          {o?.failed_logins_by_user.length ? <ul className="space-y-1 text-sm">{o.failed_logins_by_user.map((f) => <li key={f.key} className="flex justify-between"><span>{f.key}</span><b className="tabular-nums">{f.count}</b></li>)}</ul> : <p className="text-sm text-muted">None.</p>}</div>
        <div className="card p-5"><h3 className="mb-3 font-semibold">Scraping suspicion</h3>
          {scraping.data?.items.length ? <ul className="space-y-1 text-sm">{scraping.data.items.map((f) => <li key={f.id}>{f.raised_for_name} · {new Date(f.created_at).toLocaleString()} · {f.status}</li>)}</ul> : <p className="text-sm text-muted">No read-budget breaches. More than 500 distinct prospects viewed per hour throttles the user and raises a red flag here.</p>}</div>
      </div>
    </div>
  );
}

const Row = ({ k, v, warn }: { k: string; v: unknown; warn?: boolean }) => <div className="flex justify-between gap-2"><dt className="text-muted">{k}</dt><dd className={cn('tabular-nums', warn && 'font-semibold text-danger')}>{asText(v, '—')}</dd></div>;

function AuditTab({ super: isSuper }: { super: boolean }) {
  const [action, setAction] = useState('');
  const [cursor, setCursor] = useState<number | null>(null);
  const q = useQuery({ queryKey: ['audit', action, cursor], queryFn: () => raw<{ items: AuditRow[]; next_cursor: number | null }>(`/api/security/audit?limit=50${action ? `&action=${encodeURIComponent(action)}` : ''}${cursor ? `&cursor=${cursor}` : ''}`) });
  const verify = useMutation({ mutationFn: () => raw<Verify>('/api/security/audit/verify', { method: 'POST' }), onError: (e) => toast.error(e.message) });
  const v = verify.data;
  return (
    <div className="space-y-4">
      {isSuper && <div className="card flex flex-wrap items-center gap-3 p-4">
        <button className="btn-primary" disabled={verify.isPending} onClick={() => verify.mutate()}>{verify.isPending ? 'Verifying…' : 'Verify audit chain'}</button>
        {v && (v.ok ? <span className="flex items-center gap-1 text-sm text-success"><CheckCircle2 size={16} />Intact: {v.rows.toLocaleString()} entries, {v.anchors} anchors checked{v.remote_anchors !== null ? `, ${v.remote_anchors} write-once copies match` : ''}.</span>
          : <span className="flex items-center gap-1 text-sm text-danger"><TriangleAlert size={16} />Tampering detected: {v.broken_links.length} broken links, {v.altered_rows.length} altered entries, {v.anchors_failed.length} anchors failed{v.remote_failed.length ? `, ${v.remote_failed.length} write-once copies differ` : ''}.</span>)}
      </div>}
      <div className="card overflow-x-auto p-5" tabIndex={0} role="region" aria-label="Audit log">
        <div className="mb-3 flex flex-wrap items-end gap-2"><div><label className="label" htmlFor="aa">Action starts with</label><input id="aa" className="input w-64" placeholder="e.g. user. or export." value={action} onChange={(e) => { setAction(e.target.value); setCursor(null); }} /></div></div>
        <table className="w-full min-w-[760px] text-sm"><thead><tr className="table-head"><th className="pb-2">When</th><th>Who</th><th>Action</th><th>Resource</th><th>IP</th></tr></thead>
          <tbody>{q.data?.items.map((r) => <tr key={r.id} className="border-t border-line"><td className="py-2 whitespace-nowrap">{new Date(r.at).toLocaleString()}</td><td>{r.actor ?? 'system'}</td><td className="font-medium">{r.action}</td><td className="text-muted">{r.resource}{r.resource_id ? ` · ${r.resource_id.slice(0, 12)}` : ''}</td><td className="text-muted">{r.ip ?? ''}</td></tr>)}</tbody></table>
        <div className="mt-3 flex gap-2">{cursor && <button className="btn-secondary h-8 px-3" onClick={() => setCursor(null)}>Newest</button>}{q.data?.next_cursor && <button className="btn-secondary h-8 px-3" onClick={() => setCursor(q.data.next_cursor)}>Older</button>}</div>
      </div>
    </div>
  );
}

function SessionsTab({ super: isSuper }: { super: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['security-sessions'], queryFn: () => raw<Session[]>('/api/security/sessions') });
  const revoke = useMutation({ mutationFn: (id: string) => raw(`/api/security/sessions/${id}`, { method: 'DELETE' }),
    onSuccess: () => { toast.success('Session ended'); void qc.invalidateQueries({ queryKey: ['security-sessions'] }); }, onError: (e) => toast.error(e.message) });
  return (
    <div className="card overflow-x-auto p-5" tabIndex={0} role="region" aria-label="Signed-in sessions"><table className="w-full min-w-[760px] text-sm"><thead><tr className="table-head"><th className="pb-2">User</th><th>Signed in</th><th>Last seen</th><th>IP</th><th>Device</th><th /></tr></thead>
      <tbody>{q.data?.map((s) => <tr key={s.id} className="border-t border-line"><td className="py-2">{s.user}</td><td>{new Date(s.created_at).toLocaleString()}</td><td>{new Date(s.last_seen_at).toLocaleString()}</td><td className="text-muted">{s.ip ?? ''}</td>
        <td className="max-w-[240px] truncate text-muted" title={s.user_agent ?? ''}>{s.user_agent ?? ''}</td><td className="text-right">{isSuper && <button className="btn-secondary h-8 px-3" onClick={() => revoke.mutate(s.id)}>End session</button>}</td></tr>)}</tbody></table></div>
  );
}

function DeadTab({ super: isSuper }: { super: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['dead-letters'], queryFn: () => raw<Dead[]>('/api/security/outbox/dead') });
  const retry = useMutation({ mutationFn: (id: number) => raw(`/api/security/outbox/${id}/retry`, { method: 'POST' }),
    onSuccess: () => { toast.success('Queued for delivery'); void qc.invalidateQueries({ queryKey: ['dead-letters'] }); }, onError: (e) => toast.error(e.message) });
  return (
    <div className="card overflow-x-auto p-5" tabIndex={0} role="region" aria-label="Undelivered events"><h3 className="mb-1 font-semibold">Undelivered events</h3><p className="mb-3 text-sm text-muted">Events that failed 12 times (e.g. TFSC unreachable). Retrying re-queues them; deliveries are idempotent on the receiving side.</p>
      {q.data && !q.data.length ? <p className="text-sm text-muted">Nothing stuck.</p> : <table className="w-full min-w-[640px] text-sm"><thead><tr className="table-head"><th className="pb-2">Event</th><th>Attempts</th><th>Last error</th><th /></tr></thead>
        <tbody>{q.data?.map((d) => <tr key={d.id} className="border-t border-line align-top"><td className="py-2">{d.topic}<div className="text-xs text-muted">{new Date(d.created_at).toLocaleString()}</div></td><td>{d.attempts}</td><td className="max-w-[360px] text-xs text-muted">{d.last_error}</td>
          <td className="text-right">{isSuper && <button className="btn-secondary h-8 px-3" onClick={() => retry.mutate(d.id)}><RotateCcw size={14} />Retry</button>}</td></tr>)}</tbody></table>}
    </div>
  );
}
