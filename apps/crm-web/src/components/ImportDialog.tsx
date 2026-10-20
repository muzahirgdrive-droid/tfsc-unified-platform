/** Server import job for .xlsx and large CSV files (§11.2): pick a sheet, confirm the column mapping, then
 * watch progress and read per-row results. Resumable; the same file + sheet is never saved twice. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { raw } from '@/lib/api';
import { COLUMNS } from '@/lib/sheet';

type SheetInfo = { name: string; rows: number; too_many: boolean; header_detected: boolean; mapping: (string | null)[]; headers: string[] };
type Job = { id: string; status: string; filename: string | null; sheets: SheetInfo[]; sheet: string | null; progress: { done: number; total: number } | null;
  result: { saved?: number; rejected?: number; review?: number; error?: string } | null };
type RowResult = { row_no: number; decision: string; issues: { field?: string; message: string }[] };

export function ImportDialog({ file, onClose }: { file: File; onClose: () => void }) {
  const qc = useQueryClient();
  const [jobId, setJobId] = useState<string | null>(null);
  const [sheet, setSheet] = useState<string>('');
  const [cols, setCols] = useState<(string | null)[]>([]);
  const [header, setHeader] = useState(true);
  const upload = useMutation({
    mutationFn: () => { const fd = new FormData(); fd.append('file', file); return raw<Job>('/api/imports', { method: 'POST', body: fd }); },
    onSuccess: (j) => { setJobId(j.id); const s = j.sheets.find((x) => x.rows > 0) ?? j.sheets[0]; if (s) pick(s); },
    onError: (e) => { toast.error((e as Error).message); onClose(); },
  });
  useEffect(() => { upload.mutate(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const job = useQuery({ queryKey: ['import', jobId], enabled: !!jobId, queryFn: () => raw<Job>(`/api/imports/${jobId}`),
    refetchInterval: (q) => (q.state.data?.status === 'importing' ? 1500 : false) });
  const j = job.data ?? upload.data;
  const pick = (s: SheetInfo) => { setSheet(s.name); setCols(s.mapping); setHeader(s.header_detected); };
  const start = useMutation({
    mutationFn: () => raw<Job>(`/api/imports/${jobId}/start`, { method: 'POST', body: JSON.stringify({ sheet, columns: cols, header }) }),
    onSuccess: (d) => { qc.setQueryData(['import', jobId], d); qc.invalidateQueries({ queryKey: ['activities'] }); },
    onError: (e) => toast.error((e as Error).message),
  });
  const done = j?.status === 'done';
  const rejected = useQuery({ queryKey: ['import-rows', jobId, done], enabled: done, queryFn: () => raw<{ items: RowResult[] }>(`/api/imports/${jobId}/rows?decision=rejected&limit=200`) });
  const info = j?.sheets.find((s) => s.name === sheet);
  const pct = j?.progress?.total ? Math.round((100 * j.progress.done) / j.progress.total) : 0;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={`Import ${file.name}`} wide dismissOnOutside={false}
      description="Rows are refined and saved on the server with the same rules as Refine & Save. Invalid rows are listed with reasons."
      footer={j && (j.status === 'uploaded' || j.status === 'failed')
        ? <button className="btn-primary" disabled={!info || info.too_many || start.isPending} onClick={() => start.mutate()}>{j.status === 'failed' ? 'Resume import' : `Import ${info?.rows.toLocaleString() ?? ''} rows`}</button>
        : <button className="btn-secondary" onClick={onClose}>Close</button>}>
      {!j && <div className="skeleton h-24" aria-label="Uploading" />}
      {j && j.status === 'uploaded' && (
        <div className="space-y-4">
          {j.sheets.length > 1 && <div><label className="label" htmlFor="imp-sheet">Sheet</label>
            <select id="imp-sheet" className="input" value={sheet} onChange={(e) => { const s = j.sheets.find((x) => x.name === e.target.value); if (s) pick(s); }}>
              {j.sheets.map((s) => <option key={s.name} value={s.name}>{s.name} · {s.rows.toLocaleString()} rows</option>)}</select></div>}
          {info?.too_many && <p className="text-sm text-danger">This sheet has more than 50,000 rows. Split it into smaller files.</p>}
          {info && <>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={header} onChange={(e) => setHeader(e.target.checked)} />First row is a header{info.header_detected ? ' (detected)' : ''}</label>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">{cols.map((k, i) => (
              <div key={i} className="flex items-center gap-2">
                <span className="w-36 truncate text-sm text-muted" title={info.headers[i]}>{header ? info.headers[i] || `Column ${i + 1}` : `Column ${i + 1}`}</span>
                <select aria-label={`Field for column ${i + 1}`} className="input h-9" value={k ?? ''} onChange={(e) => setCols(cols.map((c, n) => (n === i ? e.target.value || null : c)))}>
                  <option value="">— ignore —</option>{COLUMNS.map((c) => <option key={c.key} value={c.key}>{c.title}</option>)}</select>
              </div>))}</div>
          </>}
        </div>)}
      {j && (j.status === 'importing' || done || j.status === 'failed') && (
        <div className="space-y-3">
          <div className="h-2 overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label="Import progress">
            <div className="h-full bg-royal transition-all" style={{ width: `${pct}%` }} /></div>
          <p className="text-sm text-muted">{j.progress ? `${j.progress.done.toLocaleString()} of ${j.progress.total.toLocaleString()} rows` : 'Starting…'}</p>
          {j.result?.error && <p className="text-sm text-danger">{j.result.error}</p>}
          {done && j.result && <div className="flex flex-wrap gap-2">
            <span className="chip bg-success-bg text-success">{j.result.saved} saved</span>
            <span className="chip bg-danger-bg text-danger">{j.result.rejected} rejected</span>
            <span className="chip bg-warn-bg text-warn">{j.result.review} to review</span></div>}
          {!!rejected.data?.items.length && <div className="max-h-64 overflow-y-auto rounded-xl border border-line">
            <table className="w-full text-sm"><thead><tr className="table-head"><th className="p-2">Row</th><th>Reason</th></tr></thead>
              <tbody>{rejected.data.items.map((r) => <tr key={r.row_no} className="border-t border-line"><td className="p-2 tabular-nums">{r.row_no + (header ? 2 : 1)}</td>
                <td>{r.issues.map((i) => i.message).join(' · ')}</td></tr>)}</tbody></table></div>}
        </div>)}
    </Dialog>
  );
}
