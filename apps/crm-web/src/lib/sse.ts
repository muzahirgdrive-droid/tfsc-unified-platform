/** Server-Sent Events subscription (celebrations, dashboard nudges, management events). */
import { useEffect } from 'react';

export type CrmEvent = { id: string; topic: string; [k: string]: unknown };
type Handler = (e: CrmEvent) => void;
const handlers = new Set<Handler>();
let source: EventSource | null = null;

function connect() {
  if (source) return;
  source = new EventSource('/api/events', { withCredentials: true });
  const dispatch = (ev: MessageEvent) => {
    try { const data = JSON.parse(ev.data) as CrmEvent; handlers.forEach((h) => h(data)); } catch { /* ignore malformed */ }
  };
  for (const topic of ['sale.celebrated', 'dashboard.changed', 'flag.raised', 'meeting.changed', 'message']) source.addEventListener(topic, dispatch);
  source.onerror = () => { /* EventSource reconnects automatically (retry: 3000) */ };
}

export function useCrmEvents(handler: Handler, enabled = true) {
  useEffect(() => {
    if (!enabled) return;
    connect();
    handlers.add(handler);
    return () => { handlers.delete(handler); };
  }, [handler, enabled]);
}

export function closeEvents() { source?.close(); source = null; }
