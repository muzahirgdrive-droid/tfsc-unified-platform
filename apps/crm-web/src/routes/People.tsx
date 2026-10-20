import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Network, UserPlus, UsersRound } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import { Dialog } from '@/components/ui/Dialog';
import { Avatar, Empty, PageHeader, Spinnerless } from '@/components/ui/bits';
import { raw } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';

type U = { id: string; display_name: string; first_name: string | null; last_name: string | null; email: string; phone: string | null; employee_code: string | null; status: string; crm_role: string; role_label: string; manager_id: string | null; has_photo: boolean; vonage_extension: string | null; version: number; domain_id?: string | null; project_id?: string | null; routing_view?: boolean; agreements_access?: boolean };
type T = { id: string; name: string; kind: string; supervisor_id: string; leader_id: string | null; member_ids: string[]; version: number; active: boolean; auto_assign: boolean; lead_capacity: number };
const ROLES: [string, string, number][] = [['sales_agent', 'Sales Agent', 2], ['supervisor', 'Supervisor', 3], ['manager', 'Manager', 3], ['director_admin', 'Director (Admin)', 4], ['super_admin', 'Super Admin', 4], ['none', 'No sales access (agreements only)', 3]];
const TFSC = ['SCRIBE ALIGN LLC', 'EXTENDED HEALTH SERVICES INC.', '24-7 CONSULTANCY LLC'];

export default function People() {
  const { me } = useAuth();
  const rank = rankOf(me);
  const [tab, setTab] = useState<'people' | 'teams' | 'tree'>('people');
  const [qtext, setQ] = useState('');
  const [edit, setEdit] = useState<U | 'new' | null>(null);
  const [team, setTeam] = useState<T | 'new' | null>(null);
  const users = useQuery({ queryKey: ['users', qtext], queryFn: () => raw<{ items: U[] }>(`/api/users?limit=200${qtext ? `&q=${encodeURIComponent(qtext)}` : ''}`) });
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => raw<T[]>('/api/teams') });
  const tree = useQuery({ queryKey: ['tree'], queryFn: () => raw<{ id: string; name: string; role_label: string; manager_id: string | null; status: string }[]>('/api/people/tree'), enabled: tab === 'tree' });
  const byId = new Map((users.data?.items ?? []).map((u) => [u.id, u]));
  return (
    <div>
      <PageHeader eyebrow="People & teams" title="Your organization" subtitle="Reporting lines decide scope. Supervisors form groups (3–4 agents, one Group Leader); Managers and Directors create teams."
        actions={<>{rank >= 2 && <button className="btn-primary" onClick={() => setEdit('new')}><UserPlus size={16} />{rank >= 2 ? 'Create user' : 'Request user'}</button>}
          {rank >= 1 && <button className="btn-secondary" onClick={() => setTeam('new')}><UsersRound size={16} />New {rank >= 2 ? 'team / group' : 'group'}</button>}</>} />
      <div className="mb-4 flex gap-1">{([['people', 'People'], ['teams', 'Teams & groups'], ['tree', 'Org chart']] as const).map(([v, l]) => <button key={v} onClick={() => setTab(v)} className={cn('chip h-9 px-3', tab === v ? 'bg-navy text-white' : 'border border-line bg-surface')}>{l}</button>)}</div>
      {tab === 'people' && <>
        <input className="input mb-3 max-w-sm" placeholder="Search name, email, code" value={qtext} onChange={(e) => setQ(e.target.value)} />
        <Spinnerless loading={users.isLoading}>
          <div className="card overflow-x-auto" tabIndex={0} role="region" aria-label="People"><table className="w-full min-w-[720px] text-sm">
            <thead><tr className="table-head"><th className="p-3">Name</th><th>Role</th><th>Reports to</th>{rank >= 2 && <th>Domain · Project</th>}<th>Agreements</th><th>Status</th></tr></thead>
            <tbody>{users.data?.items.map((u) => (
              <tr key={u.id} className="cursor-pointer border-t border-line hover:bg-bg" onClick={() => rank >= 2 && setEdit(u)}>
                <td className="p-3"><span className="flex items-center gap-2"><Avatar name={u.display_name} userId={u.id} hasPhoto={u.has_photo} /><span><b>{u.display_name}</b><div className="text-xs text-muted">{u.email}</div></span></span></td>
                <td>{u.role_label}</td><td>{u.manager_id ? byId.get(u.manager_id)?.display_name ?? '—' : '—'}</td>
                {rank >= 2 && <td className="text-muted">{u.domain_id} · {u.project_id ?? '—'}</td>}
                <td>{u.agreements_access ? <span className="chip bg-info-bg text-info">Granted</span> : <span className="text-subtle">—</span>}</td>
                <td><span className={cn('chip', u.status === 'active' ? 'bg-success-bg text-success' : 'bg-bg text-muted')}>{u.status}</span></td>
              </tr>))}</tbody></table></div>
        </Spinnerless></>}
      {tab === 'teams' && <Spinnerless loading={teams.isLoading}>{teams.data?.length === 0 ? <Empty title="No teams yet" /> : <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">{teams.data?.map((t) => (
        <button key={t.id} className="card p-5 text-left" onClick={() => setTeam(t)}>
          <div className="flex items-center justify-between"><b>{t.name}</b><span className="chip bg-bg">{t.kind}</span></div>
          <div className="mt-1 text-sm text-muted">Supervisor: {byId.get(t.supervisor_id)?.display_name ?? '—'}</div>
          {t.auto_assign && <div className="mt-2"><span className="chip bg-info-bg text-info">Round-robin · up to {t.lead_capacity} open leads each</span></div>}
          <div className="mt-3 flex flex-wrap gap-1">{t.member_ids.map((id) => <span key={id} className={cn('chip', id === t.leader_id ? 'bg-cyan/15 text-royal' : 'bg-bg')}>{byId.get(id)?.display_name ?? 'member'}{id === t.leader_id ? ' · leader' : ''}</span>)}</div>
        </button>))}</div>}</Spinnerless>}
      {tab === 'tree' && <div className="card p-5"><Tree nodes={tree.data ?? []} parent={null} depth={0} /></div>}
      {edit && <UserForm user={edit === 'new' ? null : edit} users={users.data?.items ?? []} teams={teams.data ?? []} onClose={() => setEdit(null)} />}
      {team && <TeamForm team={team === 'new' ? null : team} users={users.data?.items ?? []} onClose={() => setTeam(null)} />}
    </div>
  );
}

function Tree({ nodes, parent, depth }: { nodes: { id: string; name: string; role_label: string; manager_id: string | null; status: string }[]; parent: string | null; depth: number }) {
  const ids = new Set(nodes.map((n) => n.id));
  const kids = nodes.filter((n) => (parent ? n.manager_id === parent : !n.manager_id || !ids.has(n.manager_id)));
  if (!kids.length) return null;
  return <ul className={cn('space-y-1', depth && 'ml-5 border-l border-line pl-4')}>{kids.map((k) => <li key={k.id}><div className="flex items-center gap-2 py-1 text-sm"><Network size={14} className="text-royal" /><b>{k.name}</b><span className="text-muted">{k.role_label}</span></div><Tree nodes={nodes} parent={k.id} depth={depth + 1} /></li>)}</ul>;
}

/** Create / Manage User (§8). Does not close on an outside click (v1.0.1 rule); Close/Escape do. */
function UserForm({ user, users, teams, onClose }: { user: U | null; users: U[]; teams: T[]; onClose: () => void }) {
  const { me } = useAuth();
  const rank = rankOf(me);
  const qc = useQueryClient();
  const domains = useQuery({ queryKey: ['domains'], queryFn: () => raw<{ id: string; name: string; active: boolean }[]>('/api/settings/domains') });
  const projects = useQuery({ queryKey: ['projects'], queryFn: () => raw<{ id: string; name: string | null; domain_id: string | null; owner_name: string | null; status: string }[]>('/api/settings/projects') });
  const access = useQuery({ queryKey: ['agreements-access', user?.id], enabled: !!user && rank >= 3, queryFn: () => raw<{ enabled: boolean; role: string | null; companies: string[] }>(`/api/users/${user!.id}/agreements`) });
  const [v, setV] = useState({ first_name: user?.first_name ?? '', last_name: user?.last_name ?? '', display_name: user?.display_name ?? '', email: user?.email ?? '', phone: user?.phone ?? '', employee_code: user?.employee_code ?? '',
    crm_role: user?.crm_role ?? 'sales_agent', manager_id: user?.manager_id ?? me?.id ?? '', domain_id: user?.domain_id ?? 'scribe_align', project_id: user?.project_id ?? '', vonage_extension: user?.vonage_extension ?? '',
    team_id: '', group_id: '', group_leader: false, temporary_password: '', status: user?.status ?? 'active' });
  const [agr, setAgr] = useState<{ enabled: boolean; role: string; companies: string[] } | null>(null);
  const [offboard, setOffboard] = useState(false);
  // Keycloak emails SSO invitations (set a password; TOTP enrolment for Supervisor+) when provisioning is configured
  const invitations = useQuery({ queryKey: ['auth-status'], queryFn: () => raw<{ invitations: boolean }>('/api/auth/status') }).data?.invitations ?? false;
  const invite = useMutation({ mutationFn: () => raw(`/api/users/${user!.id}/invite`, { method: 'POST' }),
    onSuccess: () => toast.success('Invitation queued: the sign-in service emails a link to set a password.'), onError: (e) => toast.error(e.message) });
  const a = agr ?? { enabled: access.data?.enabled ?? false, role: access.data?.role ?? 'user', companies: access.data?.companies ?? [] };
  const proj = projects.data?.find((p) => p.id === v.project_id);
  const save = useMutation({
    mutationFn: async () => {
      const common = { phone: v.phone || null, employee_code: v.employee_code || null, domain_id: v.domain_id || null, project_id: v.project_id || null, vonage_extension: v.vonage_extension || null };
      const saved = user
        ? await raw<U>(`/api/users/${user.id}`, { method: 'PATCH', body: JSON.stringify({ version: user.version, first_name: v.first_name, last_name: v.last_name, display_name: v.display_name || null, crm_role: v.crm_role, manager_id: v.manager_id || null, ...(v.status !== user.status ? { status: v.status } : {}), ...common }) })
        : await raw<U>('/api/users', { method: 'POST', body: JSON.stringify({ first_name: v.first_name, last_name: v.last_name, display_name: v.display_name || null, email: v.email, crm_role: v.crm_role, manager_id: v.manager_id || null,
          team_id: v.team_id || null, group_id: v.group_id || null, group_leader: v.group_leader, temporary_password: v.temporary_password || null, ...common }) });
      if (rank >= 3 && agr) await raw(`/api/users/${saved.id}/agreements`, { method: 'PUT', body: JSON.stringify({ enabled: agr.enabled, role: agr.role, companies: agr.companies }) });
      return saved;
    },
    onSuccess: () => { toast.success(user ? 'User updated' : 'User created'); void qc.invalidateQueries({ queryKey: ['users'] }); onClose(); }, onError: (e) => toast.error(e.message),
  });
  const f = (k: keyof typeof v, label: string, type = 'text') => <div><label className="block"><span className="label">{label}</span><input type={type} className="input" value={String(v[k])} onChange={(e) => setV({ ...v, [k]: e.target.value })} disabled={k === 'email' && !!user} /></label></div>;
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={user ? `Manage ${user.display_name}` : 'Create user'} dismissOnOutside={false} wide
      footer={<>{user && rank >= 2 && user.id !== me?.id && user.status !== 'offboarded' && <button className="btn-danger mr-auto" onClick={() => setOffboard(true)}>Offboard…</button>}
        {user && invitations && rank >= 2 && ['invited', 'active'].includes(user.status) && <button className="btn-secondary" disabled={invite.isPending} onClick={() => invite.mutate()}>{user.status === 'invited' ? 'Resend invitation' : 'Send sign-in invitation'}</button>}
        <button className="btn-secondary" onClick={onClose}>Close</button><button className="btn-primary" disabled={save.isPending || !v.first_name || !v.last_name || !v.email} onClick={() => save.mutate()}>{user ? 'Save' : 'Create user'}</button></>}>
      {offboard && user && <OffboardDialog user={user} onClose={(done) => { setOffboard(false); if (done) onClose(); }} />}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {f('first_name', 'First name')}{f('last_name', 'Last name')}{f('display_name', 'Display name')}{f('email', 'Email', 'email')}{f('phone', 'Phone')}{f('employee_code', 'Employee code')}
        <div><label className="block"><span className="label">CRM role</span><select className="input" value={v.crm_role} onChange={(e) => setV({ ...v, crm_role: e.target.value })}>{ROLES.filter(([, , min]) => rank >= min || rank === 4).map(([r, l]) => <option key={r} value={r}>{l}</option>)}</select></label></div>
        <div><label className="block"><span className="label">Reports to</span><select className="input" value={v.manager_id} onChange={(e) => setV({ ...v, manager_id: e.target.value })}>{users.filter((u) => u.crm_role !== 'sales_agent' && u.id !== user?.id).map((u) => <option key={u.id} value={u.id}>{u.display_name} · {u.role_label}</option>)}{me && !users.some((u) => u.id === me.id) && <option value={me.id}>{me.display_name}</option>}</select></label></div>
        <div><label className="block"><span className="label">Domain (default Scribe Align LLC)</span><select className="input" value={v.domain_id} onChange={(e) => setV({ ...v, domain_id: e.target.value, project_id: '' })}>{domains.data?.filter((d) => d.active).map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label></div>
        <div><label className="block"><span className="label">Project ID</span><select className="input" value={v.project_id} onChange={(e) => setV({ ...v, project_id: e.target.value })}><option value="">—</option>{projects.data?.filter((p) => p.status !== 'closed' && (!p.domain_id || p.domain_id === v.domain_id)).map((p) => <option key={p.id} value={p.id}>{p.id}{p.name && p.name !== p.id ? ` · ${p.name}` : ''}</option>)}</select></label>
          {proj && <p className="mt-1 text-xs text-muted">Owner: {proj.owner_name ?? 'not set yet'}</p>}</div>
        {f('vonage_extension', 'Vonage line / extension (optional)')}
        {!user && <>
          <div><label className="block"><span className="label">Team</span><select className="input" value={v.team_id} onChange={(e) => setV({ ...v, team_id: e.target.value })}><option value="">—</option>{teams.filter((t) => t.kind === 'team').map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label></div>
          <div><label className="block"><span className="label">Group</span><select className="input" value={v.group_id} onChange={(e) => setV({ ...v, group_id: e.target.value })}><option value="">—</option>{teams.filter((t) => t.kind === 'group').map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>
            {v.group_id && <label className="mt-1 flex items-center gap-2 text-xs"><input type="checkbox" checked={v.group_leader} onChange={(e) => setV({ ...v, group_leader: e.target.checked })} />Group leader</label>}</div>
          {f('temporary_password', invitations ? 'Temporary password (leave empty to email an SSO invitation)' : 'Temporary password (if no email invitation)', 'password')}</>}
        {user && <div><label className="block"><span className="label">Status</span><select className="input" value={v.status} onChange={(e) => setV({ ...v, status: e.target.value })}>{user.status === 'invited' && <option value="invited">Invited (not signed in yet)</option>}<option value="active">Active</option><option value="suspended">Suspended</option></select></label></div>}
      </div>
      {rank >= 3 && <section className="mt-5 rounded-2xl border border-line p-4">
        <label className="flex items-center gap-2 font-semibold"><input type="checkbox" checked={a.enabled} onChange={(e) => setAgr({ ...a, enabled: e.target.checked })} />TFSC Agreements access</label>
        <p className="mt-1 text-xs text-muted">Granted in TFSC in one step. TFSC checks that your own rights cover it; sales roles never imply agreements access.</p>
        {a.enabled && <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div><label className="block"><span className="label">TFSC role</span><select className="input" value={a.role} onChange={(e) => setAgr({ ...a, role: e.target.value })}><option value="user">User</option>{rank >= 4 && <option value="admin">Admin</option>}</select></label></div>
          <div><span className="label">Companies</span>{TFSC.map((c) => <label key={c} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={a.companies.includes(c)} onChange={(e) => setAgr({ ...a, companies: e.target.checked ? [...a.companies, c] : a.companies.filter((x) => x !== c) })} />{c}</label>)}</div>
        </div>}
      </section>}
    </Dialog>
  );
}

function TeamForm({ team, users, onClose }: { team: T | null; users: U[]; onClose: () => void }) {
  const { me } = useAuth();
  const rank = rankOf(me);
  const qc = useQueryClient();
  const [v, setV] = useState({ name: team?.name ?? '', kind: team?.kind ?? (rank >= 2 ? 'team' : 'group'), supervisor_id: team?.supervisor_id ?? (rank === 1 ? me!.id : ''), members: team?.member_ids ?? [], leader_id: team?.leader_id ?? '',
    auto_assign: team?.auto_assign ?? false, lead_capacity: team?.lead_capacity ?? 25 });
  const policy = !!team && team.kind === 'team' && rank >= 2; // §14.4 round-robin is a Manager+ decision per team
  const save = useMutation({
    mutationFn: async () => {
      if (!team) return raw('/api/teams', { method: 'POST', body: JSON.stringify({ name: v.name, kind: v.kind, supervisor_id: v.supervisor_id, member_ids: v.members, leader_id: v.leader_id || null }) });
      for (const id of v.members.filter((m) => !team.member_ids.includes(m))) await raw(`/api/teams/${team.id}/members`, { method: 'POST', body: JSON.stringify({ user_id: id }) });
      for (const id of team.member_ids.filter((m) => !v.members.includes(m))) await raw(`/api/teams/${team.id}/members/remove`, { method: 'POST', body: JSON.stringify({ user_id: id }) });
      return raw('/api/teams/' + team.id, { method: 'PATCH', body: JSON.stringify({ version: team.version, name: v.name, leader_id: v.leader_id || null, clear_leader: !v.leader_id,
        ...(policy ? { auto_assign: v.auto_assign, lead_capacity: v.lead_capacity } : {}) }) });
    },
    onSuccess: () => { toast.success('Saved'); void qc.invalidateQueries({ queryKey: ['teams'] }); onClose(); }, onError: (e) => toast.error(e.message),
  });
  const agents = users.filter((u) => u.crm_role === 'sales_agent');
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} title={team ? `Edit ${team.name}` : 'New team or group'} description="Membership is effective-dated so past metrics stay with the team of that date." dismissOnOutside={false}
      footer={<button className="btn-primary" disabled={!v.name || !v.supervisor_id || save.isPending} onClick={() => save.mutate()}>Save</button>}>
      <div className="space-y-3">
        <div><label className="block"><span className="label">Name</span><input className="input" value={v.name} onChange={(e) => setV({ ...v, name: e.target.value })} /></label></div>
        {!team && rank >= 2 && <div><label className="block"><span className="label">Kind</span><select className="input" value={v.kind} onChange={(e) => setV({ ...v, kind: e.target.value })}><option value="team">Team</option><option value="group">Group (3–4 agents)</option></select></label></div>}
        {!team && <div><label className="block"><span className="label">Supervisor</span><select className="input" value={v.supervisor_id} onChange={(e) => setV({ ...v, supervisor_id: e.target.value })}><option value="">Choose…</option>{[...users.filter((u) => u.crm_role === 'supervisor'), ...(rank === 1 && me ? [{ id: me.id, display_name: me.display_name }] : [])].map((u) => <option key={u.id} value={u.id}>{u.display_name}</option>)}</select></label></div>}
        <div><span className="label">Members</span><div className="grid max-h-56 grid-cols-2 gap-1 overflow-y-auto">{agents.map((u) => <label key={u.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.members.includes(u.id)} onChange={(e) => setV({ ...v, members: e.target.checked ? [...v.members, u.id] : v.members.filter((x) => x !== u.id) })} />{u.display_name}</label>)}</div></div>
        {policy && <div className="rounded-xl border border-line p-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={v.auto_assign} onChange={(e) => setV({ ...v, auto_assign: e.target.checked })} />
            Round-robin new leads to this team's agents when no first connector or active requester owns them</label>
          <label className="mt-2 block"><span className="label">Open-lead capacity per agent (agents at capacity are skipped)</span>
            <input className="input" type="number" min={1} max={500} value={v.lead_capacity} disabled={!v.auto_assign} onChange={(e) => setV({ ...v, lead_capacity: Math.max(1, Math.min(500, +e.target.value || 1)) })} /></label>
        </div>}
        {v.kind === 'group' && <div><label className="block"><span className="label">Group leader (coordinates follow-ups; no extra power)</span><select className="input" value={v.leader_id} onChange={(e) => setV({ ...v, leader_id: e.target.value })}><option value="">None</option>{v.members.map((id) => <option key={id} value={id}>{users.find((u) => u.id === id)?.display_name}</option>)}</select></label></div>}
      </div>
    </Dialog>
  );
}

/** One click (§21): suspends CRM + Agreements access, ends sessions, moves open follow-ups and active leads to the supervisor. */
function OffboardDialog({ user, onClose }: { user: U; onClose: (done: boolean) => void }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState('');
  const run = useMutation({
    mutationFn: () => raw<{ sessions_revoked: number; followups_moved: number; leads_moved: number; leads_round_robin: number; agreements_revoked: boolean }>(`/api/users/${user.id}/offboard`, { method: 'POST', body: JSON.stringify({ reason }) }),
    onSuccess: (r) => { toast.success(`${user.display_name} offboarded: ${r.followups_moved} follow-up(s) and ${r.leads_moved} lead(s) moved${r.leads_round_robin ? ` (${r.leads_round_robin} by round-robin)` : ''}, ${r.sessions_revoked} session(s) ended${r.agreements_revoked ? ', Agreements access revoked' : ''}.`); void qc.invalidateQueries({ queryKey: ['users'] }); onClose(true); },
    onError: (e) => toast.error(e.message),
  });
  return (
    <Dialog open onOpenChange={(o) => !o && onClose(false)} title={`Offboard ${user.display_name}?`} dismissOnOutside={false}
      description="Suspends CRM and Agreements access, ends every session, and moves open follow-ups and active leads to their supervisor's queue (recorded as reassignments). This is audited."
      footer={<><button className="btn-secondary" onClick={() => onClose(false)}>Cancel</button><button className="btn-danger" disabled={run.isPending || reason.trim().length < 3} onClick={() => run.mutate()}>Offboard</button></>}>
      <label className="label" htmlFor="ob-r">Reason</label><input id="ob-r" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Resigned on 2026-10-20" />
    </Dialog>
  );
}
