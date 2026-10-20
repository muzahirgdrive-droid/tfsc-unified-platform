import DataEditor, { GridCellKind, type EditableGridCell, type GridCell, type GridColumn, type Item, type Theme } from '@glideapps/glide-data-grid';
import '@glideapps/glide-data-grid/dist/index.css';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ClipboardPaste, Eraser, FileUp, Redo2, Sparkles, Undo2, Wand2 } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';
import type { Issue, RefinedRow } from '@tfsc/refine';
import { Dialog } from '@/components/ui/Dialog';
import { PageHeader } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { clearDraft, loadDraft, saveDraft } from '@/lib/drafts';
import { refineInWorker } from '@/lib/refine';
import { COLUMNS, emptyRow, parseTable, toRows, type ColKey, type SheetRow } from '@/lib/sheet';

const FIELD_OF: Partial<Record<string, ColKey>> = { name: 'name', phone: 'phone', email: 'email', business_date: 'business_date', next_followup: 'next_followup', last_followup: 'last_followup', followup_count: 'followup_count' };
const SEV_BG: Record<string, string> = { green: '#e5f6ec', amber: '#fff3dc', red: '#fde8e8' };
const MAX_ROWS = 1000;
const MAX_COPY = 50;

type RowState = { refined?: RefinedRow; result?: { status: string; issues: Issue[]; notice?: string | null } };
type BatchOut = { saved: number; rejected: number; review: number; ms: number; rows: { row_no: number; status: string; issues: Issue[]; duplicate_notice?: string | null }[] };

const theme: Partial<Theme> = {
  accentColor: '#2d409d', accentLight: 'rgba(0,175,231,0.12)', textDark: '#14214c', textMedium: '#5e6c8f', textHeader: '#5e6c8f',
  bgHeader: '#f4f7fc', bgHeaderHovered: '#e9eef8', borderColor: '#e5eaf4', fontFamily: 'Outfit, sans-serif', headerFontStyle: '600 12px', baseFontStyle: '13px',
};

function displayValue(k: ColKey, r: SheetRow, st?: RowState): string {
  const ref = st?.refined?.refined as Record<string, unknown> | undefined;
  if (!ref) return r[k];
  if (k === 'name' || k === 'phone' || k === 'email' || k === 'remarks') return String(ref[k] ?? r[k] ?? '');
  if (['business_date', 'next_followup', 'last_followup'].includes(k)) return String(ref[k] ?? r[k] ?? '');
  const col = COLUMNS.find((c) => c.key === k);
  if (col?.toggle) { const v = ref[k]; return v === true ? 'Yes' : v === false ? 'No' : ''; }
  return r[k];
}

export default function Sheet() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [rows, setRows] = useState<SheetRow[]>(() => Array.from({ length: 30 }, emptyRow));
  const [state, setState] = useState<RowState[]>([]);
  const [history, setHistory] = useState<{ past: SheetRow[][]; future: SheetRow[][] }>({ past: [], future: [] });
  const [widths, setWidths] = useState<Record<string, number>>({});
  const [search, setSearch] = useState(false);
  const [preview, setPreview] = useState<{ ms: number; counts: Record<string, number> } | null>(null);
  const [mobile, setMobile] = useState(() => window.matchMedia('(max-width: 767px)').matches);
  const [mapDialog, setMapDialog] = useState<null | { headers: string[]; table: string[][] }>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { const m = window.matchMedia('(max-width: 767px)'); const f = () => setMobile(m.matches); m.addEventListener('change', f); return () => m.removeEventListener('change', f); }, []);
  useEffect(() => { if (me) loadDraft<SheetRow[]>(me.id).then((d) => { if (d?.length) { setRows(d); toast.message('Restored your unsaved sheet draft.'); } }); }, [me]);
  useEffect(() => { if (!me) return; const t = setTimeout(() => saveDraft(me.id, rows.filter((r) => Object.values(r).some(Boolean))), 800); return () => clearTimeout(t); }, [rows, me]);

  const commit = useCallback((next: SheetRow[]) => {
    setHistory((h) => ({ past: [...h.past.slice(-49), rows], future: [] }));
    setRows(next.slice(0, MAX_ROWS));
    setState([]); setPreview(null);
  }, [rows]);
  const undo = () => setHistory((h) => { if (!h.past.length) return h; const prev = h.past[h.past.length - 1]!; setRows(prev); setState([]); return { past: h.past.slice(0, -1), future: [rows, ...h.future] }; });
  const redo = () => setHistory((h) => { if (!h.future.length) return h; const nxt = h.future[0]!; setRows(nxt); setState([]); return { past: [...h.past, rows], future: h.future.slice(1) }; });

  const columns: GridColumn[] = useMemo(() => [
    ...COLUMNS.map((c) => ({ id: c.key, title: c.title, width: widths[c.key] ?? c.width })),
    { id: 'quality', title: 'Quality', width: 80 }, { id: 'status', title: 'Status', width: 220 },
  ], [widths]);

  const filled = rows.map((r, i) => ({ r, i })).filter(({ r }) => Object.values(r).some((v) => v.trim() !== ''));

  const getCell = useCallback(([col, row]: Item): GridCell => {
    const r = rows[row]!; const st = state[row];
    const c = COLUMNS[col];
    if (!c) {
      const id = columns[col]!.id;
      if (id === 'quality') {
        const ref = st?.refined?.refined as Record<string, unknown> | undefined;
        const q = !ref ? '' : ref.called === true ? (ref.connected ? ((ref.text_sent || ref.email_sent) ? '30' : '20') : '10') : (Object.keys(ref).length ? '0' : '');
        return { kind: GridCellKind.Text, data: q, displayData: q, allowOverlay: false, readonly: true };
      }
      const s = st?.result ? `${st.result.status}${st.result.issues[0] ? ` · ${st.result.issues[0].message}` : ''}${st.result.notice ? ` · ${st.result.notice}` : ''}`
        : st?.refined ? (st.refined.issues.map((x) => x.message).join(' · ') || 'Ready') : '';
      const sev = st?.result ? (st.result.status === 'saved' ? 'green' : 'red') : st?.refined?.severity;
      return { kind: GridCellKind.Text, data: s, displayData: s, allowOverlay: true, readonly: true, themeOverride: sev ? { bgCell: SEV_BG[sev] } : undefined };
    }
    const shown = displayValue(c.key, r, st);
    const issues = st?.refined?.issues.filter((x) => (FIELD_OF[x.field] ?? x.field) === c.key) ?? [];
    const sev = issues.some((x) => x.severity === 'red') ? 'red' : issues.some((x) => x.severity === 'amber') ? 'amber' : (st?.refined && shown !== r[c.key] && r[c.key]) ? 'green' : undefined;
    return { kind: GridCellKind.Text, data: r[c.key], displayData: shown, allowOverlay: true, themeOverride: sev ? { bgCell: SEV_BG[sev] } : undefined };
  }, [rows, state, columns]);

  const onCellsEdited = useCallback((items: readonly { location: Item; value: EditableGridCell }[]) => {
    const next = rows.map((r) => ({ ...r }));
    for (const { location: [col, row], value } of items) {
      const c = COLUMNS[col]; if (!c) continue;
      while (next.length <= row && next.length < MAX_ROWS) next.push(emptyRow());
      if (next[row]) next[row]![c.key] = String((value as { data: unknown }).data ?? '');
    }
    commit(next);
    return true;
  }, [rows, commit]);

  const pasteText = (text: string, at = 0) => {
    const table = parseTable(text);
    if (!table.length) return;
    const parsed = toRows(table);
    if (!parsed.headerDetected && table[0] && table[0].length > 3 && table[0].some((h) => /[a-z]{3,}/i.test(h) && !/\d/.test(h)) && filled.length === 0) {
      setMapDialog({ headers: table[0], table }); return;
    }
    const base = rows.slice(0, at).concat(parsed.rows);
    commit(base.concat(Array.from({ length: Math.max(5, 30 - base.length) }, emptyRow)));
    toast.success(`Pasted ${parsed.rows.length} rows${parsed.headerDetected ? ' (headers detected)' : ''}. Press Refine to preview.`);
  };

  const refine = async () => {
    if (!filled.length) return toast.message('Paste or type rows first.');
    const { rows: out, ms } = await refineInWorker(filled.map(({ r }) => ({ ...r })));
    const st: RowState[] = [];
    filled.forEach(({ i }, k) => { st[i] = { refined: out[k] }; });
    setState(st);
    const counts = { green: 0, amber: 0, red: 0 } as Record<string, number>;
    out.forEach((o) => { counts[o.severity] = (counts[o.severity] ?? 0) + 1; });
    setPreview({ ms, counts });
  };

  const save = useMutation({
    mutationFn: async () => {
      const send = filled.map(({ r }) => Object.fromEntries(Object.entries(r).filter(([, v]) => v !== '')));
      return raw<BatchOut>('/api/activities/batch', { method: 'POST', body: JSON.stringify({ request_key: `grid-${me?.id.slice(0, 8)}-${crypto.randomUUID()}`, rows: send, source: 'grid' }) });
    },
    onSuccess: async (out) => {
      // keep invalid rows in the grid with reasons; remove saved ones
      const keep: SheetRow[] = []; const keepState: RowState[] = [];
      out.rows.forEach((res) => {
        const src = filled[res.row_no]!;
        if (res.status !== 'saved') { keep.push(src.r); keepState.push({ result: { status: res.status === 'review' ? 'Needs review' : 'Not saved', issues: res.issues } }); }
      });
      setHistory({ past: [], future: [] });
      setRows(keep.concat(Array.from({ length: Math.max(5, 30 - keep.length) }, emptyRow)));
      setState(keepState); setPreview(null);
      if (me && !keep.length) await clearDraft(me.id);
      const notices = out.rows.filter((r) => r.duplicate_notice).length;
      toast.success(`Saved ${out.saved} rows in ${(out.ms / 1000).toFixed(1)} s${out.rejected ? ` · ${out.rejected} kept with reasons` : ''}${out.review ? ` · ${out.review} sent to review` : ''}${notices ? ` · ${notices} duplicate notices` : ''}`);
      qc.invalidateQueries({ queryKey: ['activities'] });
    },
    onError: (e) => toast.error((e as Error).message),
  });

  const fillDown = (sel: { x: number; y: number; width: number; height: number } | undefined) => {
    if (!sel || sel.height < 2) return;
    const next = rows.map((r) => ({ ...r }));
    for (let x = sel.x; x < sel.x + sel.width; x++) {
      const c = COLUMNS[x]; if (!c) continue;
      for (let y = sel.y + 1; y < sel.y + sel.height; y++) next[y]![c.key] = next[sel.y]![c.key];
    }
    commit(next);
  };
  const selRef = useRef<{ x: number; y: number; width: number; height: number }>(undefined);

  const onFile = async (f: File) => {
    if (f.size > 25_000_000) return toast.error('Files over 25 MB go through the import job (ask an admin).');
    if (/\.csv$|\.tsv$|\.txt$/i.test(f.name)) { pasteText(await f.text()); return; }
    toast.message('XLSX files are processed by the server import job (resumable, all sheets). This build accepts CSV/TSV here; for .xlsx use Import in Settings.');
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) redo(); else undo(); }
    else if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); redo(); }
    else if (mod && e.key.toLowerCase() === 'd') { e.preventDefault(); fillDown(selRef.current); }
    else if (mod && e.key.toLowerCase() === 'f') { e.preventDefault(); setSearch(true); }
  };

  return (
    <div onKeyDown={onKeyDown} onKeyDownCapture={(e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c' && (selRef.current?.height ?? 0) > MAX_COPY) {
        e.preventDefault(); e.stopPropagation(); toast.error(`Copying more than ${MAX_COPY} rows is not allowed.`);
      }
    }}>
      <PageHeader eyebrow="Sheet view" title="Paste a day. Refine. Save." subtitle="Paste rows straight from Excel or Google Sheets (with or without headers). Refine previews every fix; Refine & Save stores all valid rows at once."
        actions={<>
          <button className="btn-secondary" onClick={undo} disabled={!history.past.length} aria-label="Undo"><Undo2 size={16} /></button>
          <button className="btn-secondary" onClick={redo} disabled={!history.future.length} aria-label="Redo"><Redo2 size={16} /></button>
          <button className="btn-secondary" onClick={() => fileRef.current?.click()}><FileUp size={16} />Upload CSV</button>
          <button className="btn-secondary" onClick={async () => { try { pasteText(await navigator.clipboard.readText()); } catch { toast.message('Click a cell and press Ctrl+V to paste.'); } }}><ClipboardPaste size={16} />Paste</button>
          <button className="btn-secondary" onClick={() => { commit(Array.from({ length: 30 }, emptyRow)); if (me) clearDraft(me.id); }}><Eraser size={16} />Discard</button>
          <button className="btn-secondary" onClick={refine}><Wand2 size={16} />Refine</button>
          <button className="btn-primary" disabled={save.isPending || !filled.length} onClick={async () => { await refine(); save.mutate(); }}><Sparkles size={16} />{save.isPending ? 'Saving…' : 'Refine & Save'}</button>
        </>} />
      <input ref={fileRef} type="file" accept=".csv,.tsv,.txt,.xlsx" className="hidden" onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])} />
      {preview && (
        <div className="card mb-3 flex flex-wrap items-center gap-3 p-3 text-sm">
          <span>Refined {filled.length} rows in {preview.ms.toFixed(0)} ms:</span>
          <span className="chip bg-success-bg text-success">{preview.counts.green ?? 0} ready</span>
          <span className="chip bg-warn-bg text-warn">{preview.counts.amber ?? 0} suggestions</span>
          <span className="chip bg-danger-bg text-danger">{preview.counts.red ?? 0} need fixing</span>
          <span className="text-muted">Green = safe automatic fix · amber = suggestion · red = blocks that row only</span>
        </div>
      )}
      {mobile ? (
        <div className="space-y-3">
          {rows.slice(0, Math.max(filled.length + 1, 3)).map((r, i) => (
            <div key={i} className={cn('card space-y-2 p-4', state[i]?.refined?.severity === 'red' && 'border-danger')}>
              {(['name', 'phone', 'email', 'remarks'] as ColKey[]).map((k) => (
                <input key={k} className="input" placeholder={COLUMNS.find((c) => c.key === k)!.title} value={r[k]} onChange={(e) => { const n = rows.map((x) => ({ ...x })); n[i]![k] = e.target.value; commit(n); }} />
              ))}
              <div className="flex flex-wrap gap-1.5">{(['called', 'connected', 'text_sent', 'email_sent'] as ColKey[]).map((k) => (
                <button key={k} className={cn('chip h-8', r[k] === 'Yes' ? 'bg-success-bg text-success' : r[k] === 'No' ? 'bg-danger-bg text-danger' : 'bg-bg text-muted')}
                  onClick={() => { const n = rows.map((x) => ({ ...x })); n[i]![k] = r[k] === '' ? 'Yes' : r[k] === 'Yes' ? 'No' : ''; commit(n); }}>{COLUMNS.find((c) => c.key === k)!.title}: {r[k] || 'Unset'}</button>))}</div>
              {state[i]?.refined?.issues.length ? <p className="text-xs text-muted">{state[i]!.refined!.issues.map((x) => x.message).join(' · ')}</p> : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="card overflow-hidden p-0">
          <DataEditor
            columns={columns} rows={rows.length} getCellContent={getCell} onCellsEdited={onCellsEdited} theme={theme} width="100%" height={Math.min(680, 36 + rows.length * 34)}
            rowMarkers="number" smoothScrollX smoothScrollY freezeColumns={2} rowHeight={34} headerHeight={36}
            getCellsForSelection keybindings={{ search: true, selectAll: true, copy: true, paste: true }} showSearch={search} onSearchClose={() => setSearch(false)}
            onPaste={(target, values) => {
              const text = values.map((r) => r.join('\t')).join('\n');
              if (values.length > 1 || values[0]!.length > 1) { const [, row] = target; pasteText(text, row); return false; }
              return true;
            }}
            onGridSelectionChange={(s) => { const r = s.current?.range; selRef.current = r ? { x: r.x, y: r.y, width: r.width, height: r.height } : undefined; }}
            onColumnResize={(col, w) => setWidths((p) => ({ ...p, [String(col.id)]: w }))}
            trailingRowOptions={{ hint: 'New row', sticky: true }} onRowAppended={() => { if (rows.length < MAX_ROWS) setRows([...rows, emptyRow()]); }}
          />
        </div>
      )}
      <p className="mt-3 text-xs text-muted">Shortcuts: Ctrl+V paste · Ctrl+Z/Y undo/redo · Ctrl+D fill down · Ctrl+F find. Copying more than {MAX_COPY} rows is blocked. Strong Follow-Up and Sign Up columns are kept as information only — in the CRM those are supervisor-confirmed milestones. Saved rows get the import edit window (default 15 minutes).</p>
      <Dialog open={!!mapDialog} onOpenChange={(o) => !o && setMapDialog(null)} title="Map your columns" description="We could not recognise every header. Choose what each pasted column means." wide>
        {mapDialog && <ColumnMapper headers={mapDialog.headers} onDone={(mapping) => {
          const body = mapDialog.table.slice(1).map((cells) => { const r = emptyRow(); cells.forEach((v, i) => { const k = mapping[i]; if (k) r[k] = v; }); return r; });
          commit(body.concat(Array.from({ length: 5 }, emptyRow))); setMapDialog(null); toast.success(`Pasted ${body.length} rows.`);
        }} />}
      </Dialog>
    </div>
  );
}

function ColumnMapper({ headers, onDone }: { headers: string[]; onDone: (m: (ColKey | null)[]) => void }) {
  const [m, setM] = useState<(ColKey | null)[]>(() => headers.map((h) => COLUMNS.find((c) => c.aliases.includes(h.trim().toLowerCase()))?.key ?? null));
  return (
    <div className="space-y-2">
      {headers.map((h, i) => (
        <div key={i} className="grid grid-cols-2 items-center gap-3">
          <span className="truncate text-sm font-medium">{h || `Column ${i + 1}`}</span>
          <select className="input" value={m[i] ?? ''} onChange={(e) => setM(m.map((x, j) => (j === i ? ((e.target.value || null) as ColKey | null) : x)))}>
            <option value="">— ignore —</option>{COLUMNS.map((c) => <option key={c.key} value={c.key}>{c.title}</option>)}
          </select>
        </div>
      ))}
      <button className="btn-primary mt-3 w-full" onClick={() => onDone(m)}>Use this mapping</button>
    </div>
  );
}
