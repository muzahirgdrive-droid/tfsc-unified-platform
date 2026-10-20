/** Blocking red-duplicate alert (§12, acceptance 7). The server sends `flag.raised` only to the people responsible
 * (both reporting lines + Super Admins) and never with client details. The alert stays until the user chooses. */
import * as D from '@radix-ui/react-dialog';
import { useQueryClient } from '@tanstack/react-query';
import { useNavigate } from '@tanstack/react-router';
import { ShieldAlert } from 'lucide-react';
import { useCallback, useState } from 'react';
import { useCrmEvents, type CrmEvent } from '@/lib/sse';
import { asText } from '@/lib/text';

export function RedAlert({ enabled }: { enabled: boolean }) {
  const [alerts, setAlerts] = useState<CrmEvent[]>([]);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const onEvent = useCallback((e: CrmEvent) => {
    if (e.topic !== 'flag.raised' || e.severity !== 'red') return;
    setAlerts((a) => (a.some((x) => x.flag_id === e.flag_id) ? a : [...a, e]));
    void qc.invalidateQueries({ queryKey: ['flags'] });
  }, [qc]);
  useCrmEvents(onEvent, enabled);
  const block = (e: Event) => e.preventDefault();
  return (
    <D.Root open={alerts.length > 0}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[70] bg-navy/60 backdrop-blur-[2px]" />
        <D.Content role="alertdialog" onEscapeKeyDown={block} onPointerDownOutside={block} onInteractOutside={block}
          className="card fixed left-1/2 top-1/2 z-[70] w-[calc(100vw-24px)] max-w-md -translate-x-1/2 -translate-y-1/2 border-2 border-danger p-6 shadow-[var(--shadow-lift)]">
          <div className="flex items-start gap-3">
            <ShieldAlert className="mt-0.5 shrink-0 text-danger" aria-hidden />
            <div>
              <D.Title className="text-lg font-semibold text-danger">{alerts.length > 1 ? `${alerts.length} red alerts` : 'Red alert'}: protected lead contacted</D.Title>
              <D.Description className="mt-1 text-sm">{asText(alerts.at(-1)?.message)} Clarify it before incentives are paid.</D.Description>
            </div>
          </div>
          <div className="mt-6 flex flex-wrap justify-end gap-2">
            <button className="btn-secondary" onClick={() => setAlerts([])}>Later</button>
            <button className="btn-primary" autoFocus onClick={() => { setAlerts([]); void navigate({ to: '/flags' }); }}>Review in Flags center</button>
          </div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
