import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { toast } from 'sonner';
import { Avatar, PageHeader } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { useAuth } from '@/lib/auth';

export default function Me() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const [pw, setPw] = useState({ current_password: '', new_password: '', confirm: '' });
  const sessions = useQuery({ queryKey: ['sessions'], queryFn: () => raw<{ id: string; current: boolean; created_at: string; last_seen_at: string; ip: string | null; user_agent: string | null }[]>('/api/auth/sessions'), enabled: !me?.reset_required });
  const change = useMutation({ mutationFn: () => raw('/api/auth/password', { method: 'POST', body: JSON.stringify({ current_password: pw.current_password, new_password: pw.new_password }) }),
    onSuccess: () => { toast.success('Password changed. Other sessions were signed out.'); setPw({ current_password: '', new_password: '', confirm: '' }); qc.invalidateQueries({ queryKey: ['me'] }); }, onError: (e) => toast.error((e as Error).message) });
  const photo = useMutation({ mutationFn: (f: File) => { const fd = new FormData(); fd.append('file', f); return raw(`/api/users/${me!.id}/photo`, { method: 'POST', body: fd }); },
    onSuccess: () => { toast.success('Photo updated'); qc.invalidateQueries({ queryKey: ['me'] }); }, onError: (e) => toast.error((e as Error).message) });
  const revoke = useMutation({ mutationFn: (id: string) => raw(`/api/auth/sessions/${id}`, { method: 'DELETE' }), onSuccess: () => sessions.refetch() });
  if (!me) return null;
  return (
    <div>
      <PageHeader eyebrow="My profile" title={me.display_name} subtitle={`${me.role_label}${me.is_group_leader ? ' · Group Leader' : ''} · ${me.email}`} />
      {me.reset_required && <div role="alert" className="card mb-4 border-warn bg-warn-bg p-4 text-warn">Set a new password to continue.</div>}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="card flex flex-col items-center p-6"><Avatar name={me.display_name} userId={me.id} hasPhoto={me.has_photo} size={96} />
          <label className="btn-secondary mt-4 cursor-pointer">Change photo<input type="file" accept="image/png,image/jpeg,image/webp" className="hidden" onChange={(e) => e.target.files?.[0] && photo.mutate(e.target.files[0])} /></label>
          <p className="mt-2 text-center text-xs text-muted">Square-cropped and re-encoded on the server; location metadata removed. Max 5 MB.</p></div>
        <form className="card space-y-3 p-6" onSubmit={(e) => { e.preventDefault(); if (pw.new_password !== pw.confirm) return toast.error('Passwords do not match'); change.mutate(); }}>
          <h3 className="font-semibold">Password</h3>
          {(['current_password', 'new_password', 'confirm'] as const).map((k) => <div key={k}><label className="label">{k === 'confirm' ? 'Confirm new password' : k.replace('_', ' ')}</label><input type="password" className="input" autoComplete={k === 'current_password' ? 'current-password' : 'new-password'} value={pw[k]} onChange={(e) => setPw({ ...pw, [k]: e.target.value })} /></div>)}
          <p className="text-xs text-muted">At least 12 characters.</p><button className="btn-primary" disabled={change.isPending}>Change password</button></form>
        {!me.reset_required && <div className="card p-6"><h3 className="mb-3 font-semibold">Active sessions</h3><ul className="space-y-2 text-sm">{sessions.data?.map((s) => (
          <li key={s.id} className="flex items-center justify-between gap-2"><span><b>{s.current ? 'This device' : (s.user_agent ?? 'Device').slice(0, 40)}</b><div className="text-xs text-muted">{s.ip ?? ''} · last seen {new Date(s.last_seen_at).toLocaleString()}</div></span>
            {!s.current && <button className="btn-secondary h-8" onClick={() => revoke.mutate(s.id)}>Sign out</button>}</li>))}</ul></div>}
      </div>
    </div>
  );
}
