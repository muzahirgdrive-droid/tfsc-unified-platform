/** Three-state outcome pill: Unset (gray) / Yes (green) / No (red). Space cycles; Y, N, U set directly.
 * Screen readers hear e.g. "Called: Yes" (§22 accessibility). */
import { Check, Minus, X } from 'lucide-react';
import { cn } from '@/lib/cn';

export type Tri = boolean | null;
const next = (v: Tri): Tri => (v === null ? true : v === true ? false : null);

export function Toggle3({ label, value, onChange, disabled, compact }: { label: string; value: Tri; onChange: (v: Tri) => void; disabled?: boolean; compact?: boolean }) {
  const text = value === null ? 'Unset' : value ? 'Yes' : 'No';
  return (
    <button
      type="button" role="switch" aria-checked={value === null ? 'mixed' : value} aria-label={`${label}: ${text}`} disabled={disabled}
      onClick={() => onChange(next(value))}
      onKeyDown={(e) => {
        const k = e.key.toLowerCase();
        if (k === 'y') { e.preventDefault(); onChange(true); } else if (k === 'n') { e.preventDefault(); onChange(false); } else if (k === 'u' || k === 'backspace') { e.preventDefault(); onChange(null); }
      }}
      className={cn('inline-flex items-center gap-1.5 rounded-full border font-semibold transition-colors duration-150',
        compact ? 'h-7 px-2 text-[11px]' : 'h-9 px-3 text-xs',
        value === null && 'border-line bg-bg text-muted hover:border-subtle',
        value === true && 'border-success/30 bg-success-bg text-success',
        value === false && 'border-danger/30 bg-danger-bg text-danger')}
    >
      {value === null ? <Minus size={13} aria-hidden /> : value ? <Check size={13} aria-hidden /> : <X size={13} aria-hidden />}
      <span>{label}</span>
      {!compact && <span className="font-medium">· {text}</span>}
    </button>
  );
}
