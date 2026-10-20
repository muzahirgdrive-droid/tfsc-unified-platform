/** Super Admin: historical import of the legacy tracking workbook with the §20.5 reconciliation report.
 * Sheet names are not identities: every sheet is mapped to a user explicitly (exact-name suggestions only). */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CheckCircle2, FileSpreadsheet, TriangleAlert, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { toast } from 'sonner';
import { Empty, Hint } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { cn } from '@/lib/cn';
import { asText } from '@/lib/text';

type Sheet = { name: string; state: string; skipped?: string; entries?: number; formula_only?: number; no_name_or_phone?: number; no_name_or_phone_attempts?: number };
type Figures = Record<string, number>;
type Recon = { period: { start: string; end: string }; workbook: Figures; importable: Figures; database?: Figures & { rows_by_decision?: Record<string, number> };
  expected?: Figures; workbook_matches_expected?: Record<string, boolean>; database_matches_workbook?: Record<string, boolean>; phone_repeats: Figures };
type Batch = { id: string; status: string; filename: string | null; bytes: number | null; sha256: string | null; audited_workbook: boolean; created_at: string;
  scan: { rows: number; sheets: Sheet[]; roster: string[]; review: Record<string, number>; reconciliation: Recon } | null;
  mapping: Record<string, string | null>; suggested_mapping: Record<string, string | null>; progress: { done: number; total: number } | null;
  result: { reconciliation?: Recon; error?: string } | null };
type ReviewItem = { row_no: number; sheet: string; row: number; decision: string; issues: { code: string; message: string; cells?: string[] }[]; cells: Record<string, unknown> };

const busy = (s: string) => s === 'uploaded' || s === 'scanning' || s === 'importing';
const LABEL: Record<string, string> = { attempts: 'Attempts', connected: 'Connected', conn_rate: 'Connection rate', strong_fu: 'Strong follow-ups', signups: 'Sign-ups',
  points: 'Points', quality: 'Quality (raw)', sms: 'SMS touches', emails: 'Email touches', contradictions: 'Contradictory rows', ref_cells: '#REF! cells' };
const fmt = (k: string, v: number | undefined) => v === undefined ? '—' : (k === 'conn_rate' || k === 'quality') ? `${(v * 100).toFixed(4)}%` : v.toLocaleString();

export function LegacyImport() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ['legacy-imports'], queryFn: () => raw<Batch[]>('/api/admin/legacy-imports'),
    refetchInterval: (q) => (q.state.data?.some((b) => busy(b.status)) ? 2000 : false) });
  const [sel, setSel] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const upload = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append('file', f); return raw<Batch>('/api/admin/legacy-imports', { method: 'POST', body: fd }); },
    onSuccess: (b) => { toast.success('Uploaded. Scanning the workbook…'); setSel(b.id); void qc.invalidateQueries({ queryKey: ['legacy-imports'] }); },
    onError: (e) => toast.error(e.message),
  });
  const current = list.data?.find((b) => b.id === sel) ?? list.data?.[0];
  return (
    <div className="space-y-4">
      <div className="card flex flex-wrap items-center gap-4 p-5">
        <FileSpreadsheet className="text-royal" size={28} aria-hidden />
        <div className="min-w-0 flex-1">
          <h3 className="font-semibold">Legacy workbook import</h3>
          <p className="text-sm text-muted">Imports the Google Sheet export as history: no lock timers, celebrations, notifications or duplicate flags. Formulas are ignored; figures are recomputed from the raw cells and reconciled.</p>
        </div>
        <input ref={file} type="file" accept=".xlsx" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ''; }} />
        <button className="btn-primary" disabled={upload.isPending} onClick={() => file.current?.click()}><Upload size={16} />{upload.isPending ? 'Uploading…' : 'Upload .xlsx'}</button>
      </div>
      {list.data && list.data.length > 1 && (
        <div className="flex flex-wrap gap-2">{list.data.map((b) => (
          <button key={b.id} onClick={() => setSel(b.id)} className={cn('chip h-8 px-3', current?.id === b.id ? 'bg-navy text-white' : 'border border-line bg-surface')}>
            {b.filename ?? 'workbook'} · {new Date(b.created_at).toLocaleDateString()} · {b.status}</button>))}</div>)}
      {!list.isLoading && !current && <Empty title="No imports yet">Upload the tracking workbook to scan it. Nothing is written until you map sheets and start.</Empty>}
      {current && <BatchView key={current.id} b={current} />}
    </div>
  );
}

function BatchView({ b }: { b: Batch }) {
  const qc = useQueryClient();
  const agents = useQuery({ queryKey: ['users', 'sales_agent'], queryFn: () => raw<{ items: { id: string; display_name: string; email: string }[] }>('/api/users?role=sales_agent&limit=200') });
  // Keyed by batch id, so the mapping and period start fresh for each batch
  const [map, setMap] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries({ ...b.suggested_mapping, ...b.mapping }).map(([k, v]) => [k, v ?? ''])));
  const [period, setPeriod] = useState<{ start: string; end: string } | null>(null);
  const start = useMutation({
    mutationFn: () => raw<Batch>(`/api/admin/legacy-imports/${b.id}/start`, { method: 'POST', body: JSON.stringify({ mapping: map,
      ...(period ? { period_start: period.start, period_end: period.end } : {}) }) }),
    onSuccess: () => { toast.success('Import started'); void qc.invalidateQueries({ queryKey: ['legacy-imports'] }); },
    onError: (e) => toast.error(e.message),
  });
  const sheets = b.scan?.sheets.filter((s) => !s.skipped) ?? [];
  const recon = b.result?.reconciliation ?? b.scan?.reconciliation;
  const p = period ?? recon?.period ?? null;
  const canStart = b.status === 'scanned' || b.status === 'failed';
  return (
    <div className="space-y-4">
      <div className="card p-5">
        <div className="flex flex-wrap items-center gap-3">
          <StatusChip s={b.status} />
          <span className="text-sm text-muted">{b.filename} · {b.bytes ? `${(b.bytes / 1e6).toFixed(1)} MB` : ''} · SHA-256 <code className="text-xs">{b.sha256?.slice(0, 16)}…</code></span>
          {b.audited_workbook && <span className="chip bg-success-bg text-success"><CheckCircle2 size={13} />Audited workbook (§2)</span>}
        </div>
        {b.progress && <div className="mt-4"><div className="h-2 overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={b.progress.done} aria-valuemax={b.progress.total} aria-label="Import progress">
          <div className="h-full bg-royal transition-all" style={{ width: `${b.progress.total ? (100 * b.progress.done) / b.progress.total : 0}%` }} /></div>
          <p className="mt-1 text-xs text-muted">{b.progress.done.toLocaleString()} of {b.progress.total.toLocaleString()} rows</p></div>}
        {b.result?.error && <p className="mt-3 flex items-center gap-2 text-sm text-danger"><TriangleAlert size={16} />{b.result.error}</p>}
      </div>

      {b.scan && (
        <div className="card overflow-x-auto p-5" tabIndex={0} role="region" aria-label="Sheets to users">
          <h3 className="mb-1 font-semibold">Sheets → users</h3>
          <p className="mb-3 text-sm text-muted">Names are not identities: choose the CRM user for every sheet you import. Unmapped sheets are skipped.</p>
          <table className="w-full min-w-[640px] text-sm">
            <thead><tr className="table-head"><th className="pb-2">Sheet</th><th className="text-right">Entries</th><th className="text-right">Formula-only rows</th><th className="text-right">No name/phone</th><th className="pl-4">CRM user</th></tr></thead>
            <tbody>{b.scan.sheets.map((s) => (
              <tr key={s.name} className="border-t border-line">
                <td className="py-2">{s.name}{s.state !== 'visible' && <span className="ml-1 text-xs text-muted">({s.state})</span>}</td>
                {s.skipped ? <td colSpan={4} className="text-muted">Skipped: {s.skipped}</td> : <>
                  <td className="text-right tabular-nums">{(s.entries ?? 0).toLocaleString()}</td><td className="text-right tabular-nums">{s.formula_only ?? 0}</td>
                  <td className="text-right tabular-nums">{s.no_name_or_phone ?? 0}</td>
                  <td className="pl-4"><select aria-label={`User for ${s.name}`} className="input h-9" disabled={!canStart || b.status === 'failed'} value={map[s.name] ?? ''} onChange={(e) => setMap({ ...map, [s.name]: e.target.value })}>
                    <option value="">— skip —</option>{agents.data?.items.map((u) => <option key={u.id} value={u.id}>{u.display_name} · {u.email}</option>)}</select></td></>}
              </tr>))}</tbody>
          </table>
          {canStart && <div className="mt-4 flex flex-wrap items-end gap-3">
            <div><label className="label" htmlFor="ps">Reconcile from</label><input id="ps" type="date" className="input" value={p?.start ?? ''} disabled={b.status === 'failed'} onChange={(e) => setPeriod({ start: e.target.value, end: p?.end ?? e.target.value })} /></div>
            <div><label className="label" htmlFor="pe">to</label><input id="pe" type="date" className="input" value={p?.end ?? ''} disabled={b.status === 'failed'} onChange={(e) => setPeriod({ start: p?.start ?? e.target.value, end: e.target.value })} /></div>
            <button className="btn-primary" disabled={start.isPending || !sheets.some((s) => map[s.name])} onClick={() => start.mutate()}>{b.status === 'failed' ? 'Resume import' : 'Start import'}</button>
            <Hint text="Resumable: a stopped import continues from the last saved chunk and never duplicates rows." />
          </div>}
        </div>)}

      {recon && <ReconTable r={recon} />}
      {(b.status === 'imported' || b.status === 'failed' || b.status === 'importing') && <ReviewQueue id={b.id} />}
    </div>
  );
}

function StatusChip({ s }: { s: string }) {
  const tone = s === 'imported' ? 'bg-success-bg text-success' : s === 'failed' ? 'bg-danger-bg text-danger' : busy(s) ? 'bg-info-bg text-info' : 'bg-bg text-ink';
  return <span className={cn('chip capitalize', tone)}>{busy(s) ? `${s}…` : s}</span>;
}

function ReconTable({ r }: { r: Recon }) {
  const keys = ['attempts', 'connected', 'conn_rate', 'strong_fu', 'signups', 'points', 'quality', 'sms', 'emails', 'contradictions', 'ref_cells'];
  const ok = (m?: Record<string, boolean>, k?: string) => (m && k && k in m ? m[k] : undefined);
  return (
    <div className="card overflow-x-auto p-5" tabIndex={0} role="region" aria-label="Reconciliation">
      <h3 className="mb-1 font-semibold">Reconciliation · {r.period.start} – {r.period.end}</h3>
      <p className="mb-3 text-sm text-muted">Workbook = the sheet's own rules on raw cells. Importable = what the import writes (contradictory and undated rows wait for review). Database = what dashboards read after import.</p>
      <table className="w-full min-w-[640px] text-sm">
        <thead><tr className="table-head"><th className="pb-2">Figure</th><th className="text-right">Workbook</th>{r.expected && <th className="text-right">§2 audit</th>}<th className="text-right">Importable</th><th className="text-right">Database</th></tr></thead>
        <tbody>{keys.map((k) => {
          const a = ok(r.workbook_matches_expected, k); const d = ok(r.database_matches_workbook, k);
          return (
            <tr key={k} className="border-t border-line">
              <td className="py-2">{LABEL[k] ?? k}</td>
              <td className="text-right tabular-nums">{fmt(k, r.workbook[k])}{a !== undefined && <Mark ok={a} />}</td>
              {r.expected && <td className="text-right tabular-nums text-muted">{fmt(k, r.expected[k])}</td>}
              <td className="text-right tabular-nums">{fmt(k, r.importable[k])}</td>
              <td className="text-right tabular-nums">{r.database ? <>{fmt(k, r.database[k])}{d !== undefined && <Mark ok={d} />}</> : '—'}</td>
            </tr>);
        })}</tbody>
      </table>
      <p className="mt-3 text-xs text-muted">Repeated phone numbers: {r.phone_repeats.repeated_keys?.toLocaleString()} numbers appear more than once; {r.phone_repeats.cross_operator_keys?.toLocaleString()} on different sheets.</p>
    </div>
  );
}

const Mark = ({ ok }: { ok: boolean }) => ok
  ? <CheckCircle2 size={14} className="ml-1 inline text-success" aria-label="matches" />
  : <TriangleAlert size={14} className="ml-1 inline text-danger" aria-label="does not match" />;

function ReviewQueue({ id }: { id: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['legacy-review', id], queryFn: () => raw<{ items: ReviewItem[]; next_cursor: number | null; counts: Record<string, number> }>(`/api/admin/legacy-imports/${id}/review?limit=100`) });
  const act = useMutation({
    mutationFn: (v: { row: number; action: 'dismiss' | 'acknowledge' }) => raw(`/api/admin/legacy-imports/${id}/review/${v.row}`, { method: 'POST', body: JSON.stringify({ action: v.action }) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['legacy-review', id] }), onError: (e) => toast.error(e.message),
  });
  return (
    <div className="card overflow-x-auto p-5" tabIndex={0} role="region" aria-label="Review queue">
      <h3 className="mb-1 font-semibold">Review queue</h3>
      <p className="mb-3 text-sm text-muted">{Object.entries(q.data?.counts ?? {}).map(([k, v]) => `${k.replace('_', ' ')}: ${v.toLocaleString()}`).join(' · ')}</p>
      {q.data && !q.data.items.length ? <Empty title="Nothing to review" /> : (
        <table className="w-full min-w-[720px] text-sm">
          <thead><tr className="table-head"><th className="pb-2">Sheet · row</th><th>Issue</th><th>Provider</th><th>Outcomes (E–J)</th><th /></tr></thead>
          <tbody>{q.data?.items.map((it) => (
            <tr key={it.row_no} className="border-t border-line align-top">
              <td className="py-2 whitespace-nowrap">{it.sheet} · {it.row}</td>
              <td className="py-2">{it.issues.map((i) => <div key={i.code}><b>{i.code.replace('_', ' ')}</b>{i.cells ? ` (${i.cells.join(', ')})` : ''}<div className="text-xs text-muted">{i.message}</div></div>)}</td>
              <td className="py-2">{asText(it.cells.name)}</td>
              <td className="py-2 text-xs">{['called', 'connected', 'text_sent', 'email_sent', 'strong_followup', 'sign_up'].map((k) => asText(it.cells[k], '–')).join(' / ')}</td>
              <td className="py-2 text-right">{it.decision === 'review'
                ? <button className="btn-secondary h-8 px-3" onClick={() => act.mutate({ row: it.row_no, action: 'dismiss' })}>Dismiss</button>
                : <button className="btn-secondary h-8 px-3" onClick={() => act.mutate({ row: it.row_no, action: 'acknowledge' })}>Acknowledge</button>}</td>
            </tr>))}</tbody>
        </table>)}
      {q.data?.next_cursor && <p className="mt-2 text-xs text-muted">Showing the first 100 items; resolve these to see more.</p>}
    </div>
  );
}
