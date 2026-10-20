/** Pacific business clock on the client (ADR-0013). The server is authoritative: we measure the
 * offset between the PC clock and /api/time so a wrong PC clock cannot shift dates. */
import { TZDate } from '@date-fns/tz';
import { format } from 'date-fns';

export const PT = 'America/Los_Angeles';
export const PKT = 'Asia/Karachi';
let offsetMs = 0;
export const setServerOffset = (serverIso: string, sentAt: number, receivedAt: number) => {
  offsetMs = new Date(serverIso).getTime() - (sentAt + receivedAt) / 2;
};
export const serverNow = () => new Date(Date.now() + offsetMs);
export const localZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;

export const inZone = (d: Date | string | number, tz: string) => new TZDate(new Date(d).getTime(), tz);
export const fmtZone = (d: Date | string | number, tz: string, pattern = 'EEE MMM d, h:mm a') => format(inZone(d, tz), pattern);
export const zoneAbbr = (d: Date | string | number, tz: string) =>
  new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' }).formatToParts(new Date(d)).find((p) => p.type === 'timeZoneName')?.value ?? tz;

/** "Thu Oct 22, 10:00 AM PT · 10:00 PM PKT" (PT plus the viewer's local zone, §10.4). */
export function dual(d: Date | string | number): string {
  const local = localZone();
  const a = `${fmtZone(d, PT)} ${zoneAbbr(d, PT)}`;
  if (local === PT) return a;
  return `${a} · ${fmtZone(d, local, 'h:mm a')} ${zoneAbbr(d, local)}`;
}

export const businessDate = () => format(inZone(serverNow(), PT), 'yyyy-MM-dd');

/** Wall-clock PT date+time -> UTC instant (DST gaps move forward, overlaps take the first). */
export function ptToUtc(dateIso: string, hhmm: string): Date {
  const [y, m, d] = dateIso.split('-').map(Number) as [number, number, number];
  const [hh, mm] = hhmm.split(':').map(Number) as [number, number];
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  for (const off of [7, 8]) {
    const t = new Date(guess + off * 3600_000);
    const back = inZone(t, PT);
    if (back.getHours() === hh && back.getMinutes() === mm && back.getDate() === d) return t;
  }
  return new Date(guess + 7 * 3600_000 + 3600_000); // spring-forward gap: next valid hour
}

export function countdown(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, '0')}`;
}
