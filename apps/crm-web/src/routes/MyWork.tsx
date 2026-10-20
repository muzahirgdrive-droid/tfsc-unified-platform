import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Lock, Pencil, Star, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { toast } from 'sonner';
import { Precheck } from '@/components/Precheck';
import { Dialog } from '@/components/ui/Dialog';
import { FollowupPicker } from '@/components/ui/FollowupPicker';
import { Toggle3, type Tri } from '@/components/ui/Toggle3';
import { Empty, PageHeader, PhoneLink, Spinnerless } from '@/components/ui/bits';
import { useTick } from '@/components/layout/WorldClock';
import { call, client, raw, type Schemas } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { countdown } from '@/lib/time';

type Act = Schemas['ActivityOut'];
type Draft = { name: string; phone: string; email: string; called: Tri; connected: Tri; text_sent: Tri; email_sent: Tri; followup_needed: Tri; next_followup: string | null; remarks: string };
const EMPTY: Draft = { name: '', phone: '', email: '', called: null, connected: null, text_sent: null, email_sent: null, followup_needed: null, next_followup: null, remarks: '' };

/** Toggle rules mirrored from the server: Connected = Yes auto-sets Called = Yes; Called = No disables Connected. */
export function applyToggle(d: Draft, key: keyof Draft, v: Tri): Draft {
  const n = { ...d, [key]: v } as Draft;
  if (key === 'connected' && v === true) n.called = true;
  if (key === 'called' && v === false && n.connected === true) n.connected = null;
  if (key === 'called' && v === null) n.connected = null;
  return n;
}

export function LockChip({ a }: { a: Act }) {
  useTick(1000);
  if (a.state === 'data_hunting') return <span className="chip bg-bg text-muted" title="No outcome toggled: research only, no timer">Data hunting</span>;
  if (a.state === 'deleted') return <span className="chip bg-danger-bg text-danger">Deleted</span>;
  const left = a.edit_deadline ? (new Date(a.edit_deadline).getTime() - Date.now()) / 1000 : 0;
  if (left <= 0) return <span className="chip bg-bg text-muted"><Lock size={12} aria-hidden />Locked</span>;
  return <span className="chip bg-info-bg text-info tabular-nums" aria-label={`Editable for ${countdown(left)}`}>Editable {countdown(left)}</span>;
}

function QuickEntry({ onSaved }: { onSaved: () => void }) {
  const [d, setD] = useState<Draft>(EMPTY);
  const nameRef = useRef<HTMLInputElement>(null);
  const save = useMutation({
    mutationFn: () => call(client.POST('/api/activities', { body: { ...d, email: d.email || null, phone: d.phone || null, remarks: d.remarks || null } })),
    onSuccess: (r) => {
      const sev = r.duplicate_severity;
      if (sev) toast.warning(r.duplicate_notice); else toast.success(`Saved ${r.activity.provider_name}`);
      r.issues.filter((i) => i.severity === 'amber').forEach((i) => toast.message(i.message));
      setD(EMPTY); onSaved(); nameRef.current?.focus();
    },
    onError: (e) => toast.error((e as Error).message),
  });
  const submit = (e?: FormEvent) => { e?.preventDefault(); if (d.name.trim()) save.mutate(); };
  const onKey = (e: KeyboardEvent) => { if (e.key === 'Enter' && !(e.target instanceof HTMLTextAreaElement)) { e.preventDefault(); submit(); } };
  const [eng, setEng] = useState<typeof import('@tfsc/refine') | null>(null);
  useEffect(() => { import('@tfsc/refine').then(setEng); }, []);
  const nameHint = d.name && eng ? eng.refineName(d.name).value : '';
  const phoneHint = d.phone && eng ? eng.refinePhone(d.phone) : null;
  const t = (k: keyof Draft, label: string) => <Toggle3 label={label} value={d[k] as Tri} onChange={(v) => setD(applyToggle(d, k, v))} disabled={k === 'connected' && d.called === false} />;
  return (
    <form onSubmit={submit} onKeyDown={onKey} className="card mb-6 space-y-3 p-5" aria-label="Quick entry">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-[2fr_1.3fr_1.5fr]">
        <div><label className="label" htmlFor="q-name">Provider / practice</label><input id="q-name" ref={nameRef} autoFocus className="input" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} placeholder="john doe md" />
          {nameHint && nameHint !== d.name && <p className="mt-1 text-xs text-muted">→ {nameHint}</p>}</div>
        <div><label className="label" htmlFor="q-phone">Phone</label>
          <div className="flex items-center gap-2"><input id="q-phone" className="input" inputMode="tel" value={d.phone} onChange={(e) => setD({ ...d, phone: e.target.value })} placeholder="(555) 123-4567" /><Precheck phone={d.phone} /></div>
          {phoneHint?.display && phoneHint.display !== d.phone && <p className="mt-1 text-xs text-muted">→ {phoneHint.display}</p>}</div>
        <div><label className="label" htmlFor="q-email">Email</label><input id="q-email" className="input" inputMode="email" value={d.email} onChange={(e) => setD({ ...d, email: e.target.value })} /></div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {t('called', 'Called')}{t('connected', 'Connected')}{t('text_sent', 'Text')}{t('email_sent', 'Email')}{t('followup_needed', 'Follow-up')}
        <FollowupPicker value={d.next_followup} onChange={(v) => setD({ ...d, next_followup: v, followup_needed: v ? true : d.followup_needed })} />
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-60 flex-1"><label className="label" htmlFor="q-remarks">Remarks</label><input id="q-remarks" className="input" value={d.remarks} onChange={(e) => setD({ ...d, remarks: e.target.value })} /></div>
        <button className="btn-primary" disabled={save.isPending || !d.name.trim()}>Save <kbd className="rounded bg-white/15 px-1.5 text-[10px]">Enter</kbd></button>
      </div>
      <p className="text-xs text-muted">Space cycles a toggle, Y/N/U set it. Leaving every outcome unset saves the row as data hunting (no credit, no timer). Any Yes/No starts the 60-minute edit window.</p>
    </form>
  );
}

function EditDialog({ a, onClose }: { a: Act; onClose: () => void }) {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [d, setD] = useState<Draft>({ name: a.provider_name, phone: a.phone ?? '', email: a.email ?? '', called: a.called ?? null, connected: a.connected ?? null, text_sent: a.text_sent ?? null, email_sent: a.email_sent ?? null, followup_needed: a.followup_needed ?? null, next_followup: a.next_followup_at?.slice(0, 10) ?? null, remarks: a.remarks });
  const [reason, setReason] = useState('');
  const needsReason = a.state === 'locked' || a.owner_id !== me?.id;
  const save = useMutation({
    mutationFn: () => call(client.PATCH('/api/activities/{activity_id}', { params: { path: { activity_id: a.id } }, body: { version: a.version, reason: reason || null, ...d, email: d.email || null, phone: d.phone || null } })),
    onSuccess: () => { toast.success('Updated'); qc.invalidateQueries({ queryKey: ['activities'] }); onClose(); }, onError: (e) => toast.error((e as Error).message),
  });
  const t = (k: keyof Draft, label: string) => <Toggle3 label={label} value={d[k] as Tri} onChange={(v) => setD(applyToggle(d, k, v))} disabled={k === 'connected' && d.called === false} />;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Edit entry" description={needsReason ? 'This entry is locked or belongs to someone else: a reason is required and a revision is stored.' : 'The edit window never extends; un-setting outcomes does not reset it.'} dismissOnOutside={false}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" disabled={save.isPending || (needsReason && reason.trim().length < 3)} onClick={() => save.mutate()}>Save changes</button></>}>
      <div className="space-y-3">
        <div><label className="label">Provider / practice</label><input className="input" value={d.name} onChange={(e) => setD({ ...d, name: e.target.value })} /></div>
        <div className="grid grid-cols-2 gap-3"><div><label className="label">Phone</label><input className="input" value={d.phone} onChange={(e) => setD({ ...d, phone: e.target.value })} /></div>
          <div><label className="label">Email</label><input className="input" value={d.email} onChange={(e) => setD({ ...d, email: e.target.value })} /></div></div>
        <div className="flex flex-wrap gap-2">{t('called', 'Called')}{t('connected', 'Connected')}{t('text_sent', 'Text')}{t('email_sent', 'Email')}{t('followup_needed', 'Follow-up')}</div>
        <FollowupPicker value={d.next_followup} onChange={(v) => setD({ ...d, next_followup: v })} />
        <div><label className="label">Remarks</label><textarea className="input h-20 py-2" value={d.remarks} onChange={(e) => setD({ ...d, remarks: e.target.value })} /></div>
        {needsReason && <div><label className="label">Reason (required)</label><input className="input" value={reason} onChange={(e) => setReason(e.target.value)} /></div>}
      </div>
    </Dialog>
  );
}

export function ActivityRow({ a, onEdit, onStrong, onDelete }: { a: Act; onEdit: () => void; onStrong: () => void; onDelete?: () => void }) {
  const out = (v: Tri | undefined, l: string) => v === null || v === undefined ? null : <span className={cn('chip', v ? 'bg-success-bg text-success' : 'bg-danger-bg text-danger')}>{l} {v ? '✓' : '✕'}</span>;
  return (
    <li className="flex flex-col gap-2 border-t border-line py-3 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2"><b className="truncate">{a.provider_name}</b><LockChip a={a} />{a.conflict && <span className="chip bg-warn-bg text-warn">Under review</span>}</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 text-sm text-muted"><PhoneLink display={a.phone} e164={a.phone_e164} />{a.email && <span>{a.email}</span>}{a.owner_name && <span>· {a.owner_name}</span>}</div>
        {a.remarks && <p className="mt-1 line-clamp-2 text-sm">{a.remarks}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">{out(a.called, 'Called')}{out(a.connected, 'Conn')}{out(a.text_sent, 'Text')}{out(a.email_sent, 'Email')}
        <span className="chip bg-royal/10 text-royal" title="Score ladder points">{a.points} pts · {a.stage}</span></div>
      <div className="flex gap-1">
        {a.can_edit && <button className="btn-ghost h-8 px-2" onClick={onEdit} aria-label="Edit"><Pencil size={15} /></button>}
        {a.called === true && a.state !== 'deleted' && !a.conflict && <button className="btn-ghost h-8 px-2" onClick={onStrong} aria-label="Request strong follow-up" title="Request strong follow-up"><Star size={15} /></button>}
        {onDelete && <button className="btn-ghost h-8 px-2 text-danger" onClick={onDelete} aria-label="Delete"><Trash2 size={15} /></button>}
      </div>
    </li>
  );
}

export function StrongDialog({ a, onClose }: { a: Act; onClose: () => void }) {
  const [win, setWin] = useState('2–3 weeks');
  const [note, setNote] = useState('');
  const m = useMutation({ mutationFn: () => call(client.POST('/api/activities/{activity_id}/strong', { params: { path: { activity_id: a.id } }, body: { target_window: win, note } })),
    onSuccess: () => { toast.success('Strong follow-up requested. Your supervisor will confirm it.'); onClose(); }, onError: (e) => toast.error((e as Error).message) });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title="Request strong follow-up" description={`${a.provider_name} — a provisional lead. Only a supervisor can confirm it.`}
      footer={<><button className="btn-secondary" onClick={onClose}>Cancel</button><button className="btn-primary" onClick={() => m.mutate()} disabled={m.isPending}>Send to supervisor</button></>}>
      <label className="label">Target window</label><input className="input mb-3" value={win} onChange={(e) => setWin(e.target.value)} />
      <label className="label">What did they say?</label><textarea className="input h-24 py-2" value={note} onChange={(e) => setNote(e.target.value)} />
    </Dialog>
  );
}

export default function MyWork() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [edit, setEdit] = useState<Act | null>(null);
  const [strong, setStrong] = useState<Act | null>(null);
  const [state, setState] = useState('');
  const q = useQuery({ queryKey: ['activities', 'mine', state], queryFn: () => raw<{ items: Act[] }>(`/api/activities?owner_id=${me?.id}&limit=100${state ? `&state=${state}` : ''}`), refetchInterval: 60_000 });
  const today = useQuery({ queryKey: ['today'], queryFn: () => raw<Record<string, number>>('/api/activities-summary/today'), refetchInterval: 30_000 });
  useEffect(() => { document.title = 'My Work · TFSC Sales CRM'; }, []);
  return (
    <div>
      <PageHeader eyebrow="My work" title="Today's conversations" subtitle={`Business date ${today.data?.business_date ?? ''} (Pacific). ${today.data ? `${today.data.attempts} attempts · ${today.data.connected} connected · ${today.data.points_event} points` : ''}`} />
      <QuickEntry onSaved={() => { qc.invalidateQueries({ queryKey: ['activities'] }); qc.invalidateQueries({ queryKey: ['today'] }); }} />
      <div className="card p-5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">Recent entries</h2>
          <div className="flex gap-1">{[['', 'All'], ['committed', 'Committed'], ['data_hunting', 'Data hunting']].map(([v, l]) => <button key={v} onClick={() => setState(v!)} className={cn('chip', state === v ? 'bg-navy text-white' : 'bg-bg text-ink')}>{l}</button>)}</div>
        </div>
        <Spinnerless loading={q.isLoading}>
          {q.data?.items.length ? <ul>{q.data.items.map((a) => <ActivityRow key={a.id} a={a} onEdit={() => setEdit(a)} onStrong={() => setStrong(a)} />)}</ul>
            : <Empty title="No entries yet">Type a provider and phone above and press Enter. Paste a whole day in Sheet View.</Empty>}
        </Spinnerless>
      </div>
      {edit && <EditDialog a={edit} onClose={() => setEdit(null)} />}
      {strong && <StrongDialog a={strong} onClose={() => setStrong(null)} />}
      {rankOf(me) < 0 && null}
    </div>
  );
}
