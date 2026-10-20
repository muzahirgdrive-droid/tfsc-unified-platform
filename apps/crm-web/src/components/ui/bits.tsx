import * as T from '@radix-ui/react-tooltip';
import { Info, Phone } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { cn, num, pct } from '@/lib/cn';
import { useConfig } from '@/lib/config';

export function PageHeader({ eyebrow, title, subtitle, actions }: { eyebrow?: string; title: string; subtitle?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
        <h1 className="text-2xl font-bold sm:text-3xl">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Hint({ text, children }: { text: string; children?: ReactNode }) {
  return (
    <T.Provider delayDuration={150}>
      <T.Root>
        <T.Trigger asChild>{children ?? <button type="button" className="text-subtle hover:text-royal" aria-label="Definition"><Info size={14} /></button>}</T.Trigger>
        <T.Portal>
          <T.Content sideOffset={6} className="z-[70] max-w-xs rounded-lg bg-navy px-3 py-2 text-xs leading-relaxed text-white shadow-[var(--shadow-lift)]">{text}</T.Content>
        </T.Portal>
      </T.Root>
    </T.Provider>
  );
}

export function Stat({ label, value, kind = 'number', previous, definition, icon, foot }: {
  label: string; value: number | null | undefined; kind?: 'number' | 'pct'; previous?: number | null; definition?: string; icon?: ReactNode; foot?: string;
}) {
  const fmt = kind === 'pct' ? pct(value) : num(value);
  let delta: ReactNode = null;
  if (previous !== undefined && previous !== null && value !== null && value !== undefined) {
    const d = value - previous;
    const up = d > 0;
    delta = (
      <span className={cn('chip', d === 0 ? 'bg-bg text-muted' : up ? 'bg-success-bg text-success' : 'bg-danger-bg text-danger')}>
        {d === 0 ? '±0' : `${up ? '▲' : '▼'} ${kind === 'pct' ? pct(Math.abs(d)) : num(Math.abs(d))}`} vs prev
      </span>
    );
  }
  return (
    <div className="card p-5">
      <div className="flex items-center justify-between text-sm text-muted">
        <span className="flex items-center gap-1.5">{label}{definition && <Hint text={definition} />}</span>
        <span className="text-royal">{icon}</span>
      </div>
      <div className="mt-3 text-3xl font-bold tracking-tight">{fmt}</div>
      <div className="mt-2 flex items-center gap-2 text-xs text-muted">{delta}{foot}</div>
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) { return <div className={cn('skeleton', className)} aria-hidden />; }

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center px-6 py-12 text-center">
      <div className="mb-3 h-12 w-12 rounded-full bg-gradient-to-br from-cyan/20 to-royal/20" aria-hidden />
      <h3 className="font-semibold">{title}</h3>
      {children && <p className="mt-1 max-w-md text-sm text-muted">{children}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

const BAND_STYLE: Record<string, string> = {
  Extraordinary: 'bg-success-bg text-success', Satisfactory: 'bg-info-bg text-info', Average: 'bg-warn-bg text-warn',
  'Below Average': 'bg-danger-bg text-danger', Poor: 'bg-danger text-white', 'No activity': 'bg-bg text-muted',
};
export const BandBadge = ({ band }: { band: string }) => <span className={cn('chip', BAND_STYLE[band] ?? 'bg-bg text-muted')}>{band}</span>;

export function Avatar({ name, userId, hasPhoto, size = 32, onDark = false }: { name: string; userId?: string; hasPhoto?: boolean; size?: number; onDark?: boolean }) {
  const [broken, setBroken] = useState(false);
  const initials = name.split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase();
  return hasPhoto && userId && !broken
    ? <img src={`/api/users/${userId}/photo`} alt="" width={size} height={size} onError={() => setBroken(true)} className="shrink-0 rounded-full object-cover" style={{ width: size, height: size }} />
    : <span className={`inline-flex shrink-0 items-center justify-center rounded-full font-bold ${onDark ? 'bg-white/90 text-navy' : 'bg-royal/10 text-royal'}`} style={{ width: size, height: size, fontSize: Math.max(10, Math.round(size * 0.34)) }} aria-hidden>{initials}</span>;
}

/** Click-to-dial (§16). Mode comes from Settings: tel: (default), a verified custom URI template, or VBC API. */
export function PhoneLink({ display, e164 }: { display: string | null | undefined; e164?: string | null }) {
  const cfg = useConfig();
  if (!display) return <span className="text-muted">—</span>;
  const target = e164 ?? display.replace(/[^\d+]/g, '');
  const tpl = cfg.data?.dialer.template;
  const href = tpl ? tpl.replace('{e164}', encodeURIComponent(target)) : `tel:${target}`;
  return (
    <a href={href} className="inline-flex items-center gap-1 font-medium text-royal hover:underline" aria-label={`Call ${display}`}>
      <Phone size={13} aria-hidden />{display}
    </a>
  );
}

export function Spinnerless({ loading, children, rows = 3 }: { loading: boolean; children: ReactNode; rows?: number }) {
  if (!loading) return <>{children}</>;
  return <div className="space-y-2">{Array.from({ length: rows }, (_, i) => <Skeleton key={i} className="h-12 w-full" />)}</div>;
}
