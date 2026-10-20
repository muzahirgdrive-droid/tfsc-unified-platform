import { useQuery } from '@tanstack/react-query';
import { Building2, FileText } from 'lucide-react';
import { useState } from 'react';
import { Dialog } from '@/components/ui/Dialog';
import { Empty, PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { asText } from '@/lib/text';

type C = { id: string; display_name: string; legal_name: string | null; account_owner: string | null; sale_date: string | null; agreement_status: string | null; primary_contact: Record<string, string | null> };
type D = C & { domain_id?: string | null; project_id?: string | null; project_owner?: string | null; agreements?: { event: string; at: string; state: string | null; files: { id: string; name: string; download_url: string }[] }[]; meetings?: { title: string; starts_at: string; status: string }[]; timeline?: Record<string, unknown>[]; attribution?: { user: string; role: string; share: number }[]; files_tab?: string };

export default function Clients() {
  const [qtext, setQ] = useState('');
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ['clients', qtext], queryFn: () => raw<C[]>(`/api/clients${qtext ? `?q=${encodeURIComponent(qtext)}` : ''}`) });
  const d = useQuery({ queryKey: ['client', open], enabled: !!open, queryFn: () => raw<D>(`/api/clients/${open}`) });
  return (
    <div>
      <PageHeader eyebrow="Clients" title="Signed relationships" subtitle="Created automatically when TFSC issues the agreement — from first data hunt to issued agreement in one record."
        actions={<input className="input w-64" placeholder="Search clients" value={qtext} onChange={(e) => setQ(e.target.value)} />} />
      <Spinnerless loading={q.isLoading}>
        {q.data?.length === 0 ? <Empty title="No clients yet">Proceed to Agreements from a signed lead; the client appears when TFSC issues the agreement.</Empty> : (
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">{q.data?.map((c) => (
            <button key={c.id} className="card p-5 text-left hover:shadow-[var(--shadow-lift)]" onClick={() => setOpen(c.id)}>
              <div className="flex items-center gap-2"><Building2 size={18} className="text-royal" /><b>{c.display_name}</b></div>
              <div className="mt-2 text-sm text-muted">Owner {c.account_owner ?? '—'} · sold {c.sale_date ?? '—'}</div>
              <span className="chip mt-2 bg-success-bg text-success">{c.agreement_status ?? '—'}</span>
            </button>))}</div>)}
      </Spinnerless>
      {open && <Dialog open onOpenChange={(o) => !o && setOpen(null)} title={d.data?.display_name ?? 'Client'} wide>
        {!d.data ? <div className="skeleton h-40" /> : <div className="space-y-4 text-sm">
          <div className="flex flex-wrap gap-2">{d.data.project_id && <span className="chip bg-bg">Project {d.data.project_id}{d.data.project_owner ? ` · ${d.data.project_owner}` : ''}</span>}{d.data.domain_id && <span className="chip bg-bg">{d.data.domain_id}</span>}
            {Object.entries(d.data.primary_contact).map(([k, v]) => v && <span key={k} className="chip bg-bg">{k}: {v}</span>)}</div>
          {d.data.agreements && <section><h4 className="mb-2 font-semibold">Agreements</h4><ul className="space-y-2">{d.data.agreements.map((a, i) => <li key={i} className="rounded-xl border border-line p-3"><b>{a.state}</b> <span className="text-muted">· {new Date(a.at).toLocaleString()}</span>
            {a.files.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{a.files.map((f) => <a key={f.id} href={f.download_url} target="_blank" rel="noopener" className="chip bg-info-bg text-info"><FileText size={12} />{f.name}</a>)}</div>}</li>)}</ul>
            <p className="mt-1 text-xs text-muted">Documents download from TFSC Agreements after TFSC authorizes you; contract bytes stay in TFSC.</p></section>}
          {d.data.meetings && d.data.meetings.length > 0 && <section><h4 className="mb-2 font-semibold">Meetings</h4>{d.data.meetings.map((m, i) => <div key={i}>{m.title} · {new Date(m.starts_at).toLocaleString()} · {m.status}</div>)}</section>}
          {d.data.attribution && <section><h4 className="mb-2 font-semibold">Attribution</h4><div className="flex flex-wrap gap-2">{d.data.attribution.map((a, i) => <span key={i} className="chip bg-bg">{a.role}: {a.user}</span>)}</div></section>}
          {d.data.timeline && <section><h4 className="mb-2 font-semibold">Timeline</h4><ol className="space-y-1 border-l-2 border-line pl-4">{d.data.timeline.map((e, i) => <li key={i}><span className="text-xs text-muted">{new Date(String(e.at)).toLocaleString()}</span> · {asText(e.kind)} {e.by ? `· ${asText(e.by)}` : ''}</li>)}</ol></section>}
          {d.data.files_tab && <section className="rounded-xl bg-bg p-3 text-muted">Files: {d.data.files_tab}</section>}
        </div>}
      </Dialog>}
    </div>
  );
}
