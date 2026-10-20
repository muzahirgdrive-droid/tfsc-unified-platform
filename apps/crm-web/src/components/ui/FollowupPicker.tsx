/** Calendar popover for the next follow-up: quick chips, time slots, and the PT + local dual display (§11.1). */
import * as P from '@radix-ui/react-popover';
import { CalendarDays } from 'lucide-react';
import { useState } from 'react';
import { DayPicker } from 'react-day-picker';
import 'react-day-picker/style.css';
import { addDays, addWeeks, format, nextMonday, parseISO } from 'date-fns';
import { businessDate, dual, ptToUtc } from '@/lib/time';

const SLOTS = ['09:00', '10:00', '11:00', '12:00', '13:00', '14:00', '15:00', '16:00', '17:00'];

export function FollowupPicker({ value, onChange }: { value: string | null; onChange: (v: string | null) => void }) {
  const [open, setOpen] = useState(false);
  const [time, setTime] = useState('10:00');
  const today = parseISO(businessDate());
  const set = (d: Date) => { onChange(format(d, 'yyyy-MM-dd')); };
  const chips: [string, Date][] = [['+1 day', addDays(today, 1)], ['+3 days', addDays(today, 3)], ['Next Mon', nextMonday(today)], ['+2 weeks', addWeeks(today, 2)], ['+3 weeks', addWeeks(today, 3)]];
  return (
    <P.Root open={open} onOpenChange={setOpen}>
      <P.Trigger className="input inline-flex items-center gap-2 text-left" aria-label="Next follow-up date">
        <CalendarDays size={15} className="text-royal" aria-hidden />
        <span className={value ? '' : 'text-muted'}>{value ? format(parseISO(value), 'EEE MMM d') : 'Follow-up date'}</span>
      </P.Trigger>
      <P.Portal>
        <P.Content sideOffset={6} className="card z-[70] w-[320px] p-3">
          <div className="mb-2 flex flex-wrap gap-1.5">
            {chips.map(([l, d]) => <button key={l} type="button" className="chip bg-bg text-ink hover:bg-royal/10" onClick={() => set(d)}>{l}</button>)}
            {value && <button type="button" className="chip bg-danger-bg text-danger" onClick={() => onChange(null)}>Clear</button>}
          </div>
          <DayPicker mode="single" selected={value ? parseISO(value) : undefined} onSelect={(d) => d && set(d)} disabled={{ before: today }} weekStartsOn={1} />
          <div className="mt-2 flex flex-wrap gap-1">
            {SLOTS.map((s) => <button key={s} type="button" onClick={() => setTime(s)} className={`chip ${s === time ? 'bg-navy text-white' : 'bg-bg text-ink'}`}>{s}</button>)}
          </div>
          {value && <p className="mt-2 text-xs text-muted">{dual(ptToUtc(value, time))}</p>}
          <button type="button" className="btn-primary mt-3 w-full" onClick={() => setOpen(false)}>Done</button>
        </P.Content>
      </P.Portal>
    </P.Root>
  );
}
