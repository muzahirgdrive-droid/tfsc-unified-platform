/** Pre-dial badge (§11.1): shows a state only — never who owns the number. Debounced 250 ms. */
import { useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { raw } from '@/lib/api';
import { cn } from '@/lib/cn';

type Pre = { state: 'new' | 'seen' | 'followup' | 'strong' | 'protected' | 'invalid'; label: string; phone: string | null };
const STYLE: Record<Pre['state'], string> = {
  new: 'bg-success-bg text-success', seen: 'bg-info-bg text-info', followup: 'bg-warn-bg text-warn', strong: 'bg-warn-bg text-warn',
  protected: 'bg-danger text-white', invalid: 'bg-bg text-muted',
};

export function Precheck({ phone }: { phone: string }) {
  const [debounced, setDebounced] = useState(phone);
  useEffect(() => { const t = setTimeout(() => setDebounced(phone), 250); return () => clearTimeout(t); }, [phone]);
  const digits = debounced.replace(/\D/g, '');
  const q = useQuery({ queryKey: ['precheck', debounced], enabled: digits.length >= 10, staleTime: 30_000, queryFn: () => raw<Pre>(`/api/precheck?phone=${encodeURIComponent(debounced)}`) });
  if (digits.length < 10 || !q.data) return null;
  return <span role="status" className={cn('chip whitespace-nowrap', STYLE[q.data.state])}>{q.data.label}</span>;
}
