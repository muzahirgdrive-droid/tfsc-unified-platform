/** Live world clock (§17): all U.S. zones + Pakistan from the IANA database (Intl), server-synced, DST-aware. */
import * as P from '@radix-ui/react-popover';
import { Clock } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from '@/lib/cn';
import { serverNow } from '@/lib/time';

const ZONES: [string, string, string][] = [
  ['America/New_York', 'Eastern', 'New York'], ['America/Chicago', 'Central', 'Chicago'], ['America/Denver', 'Mountain', 'Denver'],
  ['America/Phoenix', 'Arizona', 'Phoenix'], ['America/Los_Angeles', 'Pacific', 'Los Angeles'], ['America/Anchorage', 'Alaska', 'Anchorage'],
  ['Pacific/Honolulu', 'Hawaii', 'Honolulu'], ['Asia/Karachi', 'Pakistan', 'Karachi'],
];

const offsetMin = (tz: string, d: Date) => {
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric' })
    .formatToParts(d).map((x) => [x.type, x.value]));
  const asUtc = Date.UTC(+p.year!, +p.month! - 1, +p.day!, +p.hour!, +p.minute!);
  return Math.round((asUtc - Math.floor(d.getTime() / 60000) * 60000) / 60000);
};
const abbr = (tz: string, d: Date) => new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' }).formatToParts(d).find((x) => x.type === 'timeZoneName')?.value ?? '';
const time = (tz: string, d: Date, sec = false) => new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', minute: '2-digit', ...(sec ? { second: '2-digit' } : {}) }).format(d);
const hourOf = (tz: string, d: Date) => +new Intl.DateTimeFormat('en-US', { timeZone: tz, hour: 'numeric', hourCycle: 'h23' }).format(d);
const weekday = (tz: string, d: Date) => new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short' }).format(d);

function nextDstChangeDays(tz: string, now: Date): number | null {
  const base = offsetMin(tz, now);
  for (let h = 1; h <= 24 * 45; h++) {
    if (offsetMin(tz, new Date(now.getTime() + h * 3600_000)) !== base) return Math.floor(h / 24);
  }
  return null;
}

export function useTick(ms = 1000) {
  const [, set] = useState(0);
  useEffect(() => { const t = setInterval(() => set((x) => x + 1), ms); return () => clearInterval(t); }, [ms]);
  return serverNow();
}

export function WorldClockChip() {
  const now = useTick();
  return (
    <P.Root>
      <P.Trigger className="btn-ghost h-9 gap-2 px-3 text-sm" aria-label="World clock">
        <Clock size={15} className="text-royal" aria-hidden />
        <span className="tabular-nums font-semibold">{time('America/Los_Angeles', now)}</span>
        <span className="text-muted">{abbr('America/Los_Angeles', now)}</span>
        <span className="hidden text-muted md:inline">· PKT {time('Asia/Karachi', now)}</span>
      </P.Trigger>
      <P.Portal><P.Content align="end" sideOffset={8} className="z-[70]"><WorldClockPanel /></P.Content></P.Portal>
    </P.Root>
  );
}

export function WorldClockPanel() {
  const now = useTick();
  const pk = offsetMin('Asia/Karachi', now);
  return (
    <div className="card w-[min(560px,calc(100vw-24px))] p-4">
      <div className="mb-3 flex items-center justify-between"><h3 className="font-semibold">World clock</h3><span className="text-xs text-muted">Server-synced · IANA time zones</span></div>
      <table className="w-full text-sm">
        <thead><tr className="table-head"><th className="pb-2">Zone</th><th>Time</th><th>UTC</th><th>vs PKT</th><th className="hidden sm:table-cell">Calling hours</th></tr></thead>
        <tbody>
          {ZONES.map(([tz, region, city]) => {
            const off = offsetMin(tz, now);
            const diff = (off - pk) / 60;
            const h = hourOf(tz, now);
            const calling = h >= 8 && h < 17 && !['Sat', 'Sun'].includes(weekday(tz, now));
            const dst = nextDstChangeDays(tz, now);
            return (
              <tr key={tz} className={cn('border-t border-line', tz === 'America/Los_Angeles' && 'bg-cyan/5 font-semibold')}>
                <td className="py-2">{region}<div className="text-xs font-normal text-muted">{city}{tz === 'America/Los_Angeles' ? ' · business clock' : ''}{dst !== null && dst <= 14 ? ` · DST changes in ${dst} d` : ''}</div></td>
                <td className="tabular-nums">{time(tz, now, true)} <span className="text-xs text-muted">{abbr(tz, now)}</span></td>
                <td className="tabular-nums text-muted">{off >= 0 ? '+' : ''}{off / 60}</td>
                <td className="tabular-nums">{tz === 'Asia/Karachi' ? '—' : `${diff > 0 ? '+' : ''}${diff} h`}</td>
                <td className="hidden sm:table-cell"><span className={cn('chip', calling ? 'bg-success-bg text-success' : 'bg-bg text-muted')}>{calling ? '8–5 open' : 'Closed'}</span></td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
