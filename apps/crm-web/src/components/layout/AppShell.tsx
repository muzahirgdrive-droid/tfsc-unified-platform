import * as DM from '@radix-ui/react-dropdown-menu';
import { useQuery } from '@tanstack/react-query';
import { Link, Outlet, useRouterState } from '@tanstack/react-router';
import {
  Bell, Building2, CalendarClock, ChevronDown, ClipboardList, ExternalLink, Flag, Gauge, Grid3X3, LayoutDashboard, LogOut, Menu, Plus, Settings,
  ShieldCheck, Sparkles, Star, Target, Trophy, Users, X,
} from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { call, client } from '@/lib/api';
import { rankOf, useAuth } from '@/lib/auth';
import { cn } from '@/lib/cn';
import { useConfig } from '@/lib/config';
import { Avatar } from '@/components/ui/bits';
import { CelebrationOverlay } from './Celebration';
import { RedAlert } from './RedAlert';
import { CommandPalette } from './CommandPalette';
import { WorldClockChip } from './WorldClock';

type NavItem = { to: string; label: string; icon: ReactNode; minRank?: number; cap?: string };
const NAV: NavItem[] = [
  { to: '/', label: 'Overview', icon: <LayoutDashboard size={18} /> },
  { to: '/my-work', label: 'My Work', icon: <ClipboardList size={18} /> },
  { to: '/sheet', label: 'Sheet View', icon: <Grid3X3 size={18} /> },
  { to: '/followups', label: 'Follow-ups', icon: <CalendarClock size={18} /> },
  { to: '/strong', label: 'Strong FU', icon: <Star size={18} />, minRank: 1 },
  { to: '/leads', label: 'Leads', icon: <Target size={18} /> },
  { to: '/meetings', label: 'Meetings', icon: <Sparkles size={18} />, minRank: 1 },
  { to: '/wall-of-fame', label: 'Wall of Fame', icon: <Trophy size={18} /> },
  { to: '/clients', label: 'Clients', icon: <Building2 size={18} /> },
  { to: '/flags', label: 'Flags', icon: <Flag size={18} />, minRank: 1 },
  { to: '/people', label: 'People & Teams', icon: <Users size={18} />, minRank: 1 },
  { to: '/reports', label: 'Reports', icon: <Gauge size={18} />, minRank: 1 },
  { to: '/settings', label: 'Settings', icon: <Settings size={18} />, minRank: 3 },
  { to: '/security', label: 'Security', icon: <ShieldCheck size={18} />, minRank: 3 },
];
const MOBILE: NavItem[] = [
  { to: '/', label: 'Today', icon: <LayoutDashboard size={20} /> },
  { to: '/my-work', label: 'Add', icon: <Plus size={20} /> },
  { to: '/followups', label: 'Follow-ups', icon: <CalendarClock size={20} /> },
  { to: '/wall-of-fame', label: 'Leaders', icon: <Trophy size={20} /> },
  { to: '/me', label: 'Me', icon: <Users size={20} /> },
];

function Watermark({ name, code }: { name: string; code: string | null | undefined }) {
  const [stamp, setStamp] = useState(() => new Date().toISOString().slice(0, 16).replace('T', ' '));
  useEffect(() => { const t = setInterval(() => setStamp(new Date().toISOString().slice(0, 16).replace('T', ' ')), 60_000); return () => clearInterval(t); }, []);
  const svg = useMemo(() => {
    const text = `${name} · ${code ?? ''} · ${stamp} UTC`.replace(/[<&>]/g, '');
    return `url("data:image/svg+xml;utf8,${encodeURIComponent(`<svg xmlns='http://www.w3.org/2000/svg' width='360' height='200'><text x='10' y='110' transform='rotate(-24 180 100)' font-family='Outfit,sans-serif' font-size='14' fill='%2314214C'>${text}</text></svg>`)}")`;
  }, [name, code, stamp]);
  return <div className="watermark" style={{ backgroundImage: svg }} aria-hidden />;
}

function Notifications() {
  const q = useQuery({ queryKey: ['notifications'], queryFn: () => call(client.GET('/api/notifications')), refetchInterval: 30_000 });
  const unread = q.data?.unread ?? 0;
  return (
    <DM.Root onOpenChange={(o) => { if (!o && unread) call(client.POST('/api/notifications/read', { body: { ids: [] } })).then(() => q.refetch()); }}>
      <DM.Trigger className="btn-ghost relative h-9 w-9 px-0" aria-label={`Notifications${unread ? `, ${unread} unread` : ''}`}>
        <Bell size={18} />{unread > 0 && <span className="absolute right-1 top-1 h-4 min-w-4 rounded-full bg-danger px-1 text-[10px] font-bold text-white">{unread}</span>}
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="end" sideOffset={8} className="card z-[70] max-h-[420px] w-[340px] overflow-y-auto p-2">
          {(q.data?.items ?? []).length === 0 && <p className="p-4 text-sm text-muted">You're all caught up.</p>}
          {(q.data?.items ?? []).map((n) => (
            <div key={n.id} className={cn('rounded-lg p-3 text-sm', !n.read_at && 'bg-cyan/5')}>
              <div className="font-semibold">{n.kind.replace(/[._]/g, ' ')}</div>
              <div className="text-muted">{String((n.payload as Record<string, unknown>).message ?? '')}</div>
              <div className="mt-1 text-[11px] text-subtle">{new Date(n.created_at).toLocaleString()}</div>
            </div>
          ))}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

export function AppShell() {
  const { me, logout } = useAuth();
  const cfg = useConfig(!!me);
  const [open, setOpen] = useState(false);
  const path = useRouterState({ select: (s) => s.location.pathname });
  const rank = rankOf(me);
  useEffect(() => setOpen(false), [path]);
  if (!me) return null;
  const items = NAV.filter((n) => rank >= (n.minRank ?? 0));
  const isActive = (to: string) => (to === '/' ? path === '/' : path.startsWith(to));
  const sidebar = (
    <nav className="flex h-full flex-col gap-1 bg-gradient-to-b from-navy via-navy to-[#0d1240] p-4 text-white" aria-label="Main">
      <div className="mb-4 rounded-2xl bg-white p-3"><img src="/assets/logo.png" alt="Twenty Four Seven Consultancy" className="h-9 w-auto" /></div>
      <div className="mb-3 px-2"><div className="text-lg font-bold">TFSC Sales CRM</div><div className="text-[10px] uppercase tracking-[0.2em] text-white/50">The relationship workspace</div></div>
      <div className="-mx-1 flex-1 space-y-0.5 overflow-y-auto px-1">
        {items.map((n) => (
          <Link key={n.to} to={n.to} className={cn('flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-colors', isActive(n.to) ? 'bg-white/15 text-white shadow-inner' : 'text-white/75 hover:bg-white/10 hover:text-white')}>
            <span aria-hidden>{n.icon}</span>{n.label}
          </Link>
        ))}
        {me.agreements_access && cfg.data?.agreements_url && (
          <a href={cfg.data.agreements_url} target="_blank" rel="noopener" className="flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium text-cyan hover:bg-white/10">
            <ExternalLink size={18} aria-hidden />Agreements ↗
          </a>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3 rounded-xl bg-white/10 p-3">
        <Avatar name={me.display_name} userId={me.id} hasPhoto={me.has_photo} onDark />
        <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold">{me.display_name}</div><div className="text-[11px] text-white/60">{me.role_label}{me.is_group_leader ? ' · Group Leader' : ''}</div></div>
        <button onClick={logout} className="rounded-lg p-1.5 text-white/70 hover:bg-white/10 hover:text-white" aria-label="Sign out"><LogOut size={16} /></button>
      </div>
    </nav>
  );
  return (
    <div className="min-h-dvh lg:pl-[264px]">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-[264px] lg:block">{sidebar}</aside>
      {open && <div className="fixed inset-0 z-50 lg:hidden"><div className="absolute inset-0 bg-navy/50" onClick={() => setOpen(false)} /><aside className="absolute inset-y-0 left-0 w-[280px]">{sidebar}</aside></div>}
      <header className="sticky top-0 z-30 flex h-16 items-center gap-2 border-b border-line bg-surface/85 px-4 backdrop-blur lg:px-8">
        <button className="btn-ghost h-9 w-9 px-0 lg:hidden" onClick={() => setOpen((v) => !v)} aria-label="Menu">{open ? <X size={18} /> : <Menu size={18} />}</button>
        <CommandPalette items={items} />
        <div className="flex-1" />
        <WorldClockChip />
        <Notifications />
        <DM.Root>
          <DM.Trigger className="btn-ghost h-9 gap-2 px-2" aria-label="Account"><Avatar name={me.display_name} userId={me.id} hasPhoto={me.has_photo} size={28} /><ChevronDown size={14} /></DM.Trigger>
          <DM.Portal>
            <DM.Content align="end" sideOffset={8} className="card z-[70] min-w-48 p-1 text-sm">
              <DM.Item asChild><Link to="/me" className="block rounded-lg px-3 py-2 outline-none hover:bg-bg">My profile & security</Link></DM.Item>
              <DM.Item onSelect={logout} className="cursor-pointer rounded-lg px-3 py-2 text-danger outline-none hover:bg-danger-bg">Sign out</DM.Item>
            </DM.Content>
          </DM.Portal>
        </DM.Root>
      </header>
      <main className="mx-auto max-w-[1400px] px-4 pb-28 pt-6 lg:px-8 lg:pb-10">
        <Outlet />
      </main>
      <nav className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-5 border-t border-line bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden" aria-label="Quick">
        {MOBILE.map((n) => (
          <Link key={n.to} to={n.to} className={cn('flex flex-col items-center gap-0.5 py-2 text-[11px] font-medium', isActive(n.to) ? 'text-royal' : 'text-muted')}>{n.icon}{n.label}</Link>
        ))}
      </nav>
      <Watermark name={me.display_name} code={me.employee_code} />
      <CelebrationOverlay enabled />
      <RedAlert enabled={rankOf(me) >= 1} />
    </div>
  );
}
