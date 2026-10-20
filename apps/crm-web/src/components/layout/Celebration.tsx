/** Full-screen sale celebration (§18): confetti + trophy for ~6 s on every screen. Dismissible,
 * focus-safe, aria-live, reduced-motion variant, sound off. No client details are ever shown. */
import confetti from 'canvas-confetti';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { Trophy, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useConfig } from '@/lib/config';
import { useCrmEvents, type CrmEvent } from '@/lib/sse';
import { Avatar } from '@/components/ui/bits';
import { asText } from '@/lib/text';

// No web worker: canvas-confetti's worker is a blob: URL, which our CSP (script-src 'self') blocks.
const confettiFire = confetti.create(undefined, { resize: true, useWorker: false });
const SEEN = 'crm.celebrations.seen';
const seen = (): string[] => { try { return JSON.parse(localStorage.getItem(SEEN) ?? '[]') as string[]; } catch { return []; } };
const markSeen = (id: string) => { try { localStorage.setItem(SEEN, JSON.stringify([...seen(), id].slice(-50))); } catch { /* ignore */ } };

export function CelebrationOverlay({ enabled }: { enabled: boolean }) {
  const [current, setCurrent] = useState<CrmEvent | null>(null);
  const reduced = useReducedMotion();
  const cfg = useConfig(enabled);
  const closeRef = useRef<HTMLButtonElement>(null);
  const onEvent = useCallback((e: CrmEvent) => {
    if (e.topic !== 'sale.celebrated') return;
    const id = asText(e.celebration_id ?? e.id);
    if (seen().includes(id)) return;
    markSeen(id);
    setCurrent(e);
  }, []);
  useCrmEvents(onEvent, enabled);
  useEffect(() => {
    if (!current) return;
    if (!reduced) {
      const end = Date.now() + 1800;
      const frame = () => {
        void confettiFire({ particleCount: 6, angle: 60, spread: 70, origin: { x: 0 }, colors: ['#00afe7', '#2d409d', '#ffd166'] });
        void confettiFire({ particleCount: 6, angle: 120, spread: 70, origin: { x: 1 }, colors: ['#00afe7', '#2d409d', '#ffd166'] });
        if (Date.now() < end) requestAnimationFrame(frame);
      };
      frame();
    }
    closeRef.current?.focus();
    const t = setTimeout(() => setCurrent(null), (cfg.data?.celebration_seconds ?? 6) * 1000);
    return () => clearTimeout(t);
  }, [current, reduced, cfg.data?.celebration_seconds]);
  return (
    <AnimatePresence>
      {current && (
        <motion.div role="alertdialog" aria-live="assertive" aria-label="Sale celebration" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          className="fixed inset-0 z-[80] flex items-center justify-center bg-navy/70 p-6 backdrop-blur-sm" onClick={() => setCurrent(null)}>
          <motion.div initial={reduced ? false : { scale: 0.85, y: 20 }} animate={{ scale: 1, y: 0 }} transition={{ type: 'spring', stiffness: 220, damping: 18 }}
            className="relative w-full max-w-lg rounded-3xl bg-gradient-to-br from-navy to-royal p-8 text-center text-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <button ref={closeRef} onClick={() => setCurrent(null)} className="absolute right-4 top-4 rounded-full p-1.5 hover:bg-white/10" aria-label="Dismiss"><X size={18} /></button>
            <motion.div animate={reduced ? {} : { rotate: [0, -8, 8, -4, 0], scale: [1, 1.1, 1] }} transition={{ duration: 1.2 }} className="mx-auto mb-4 flex h-20 w-20 items-center justify-center rounded-full bg-white/10">
              <Trophy size={44} className="text-[#ffd166]" aria-hidden />
            </motion.div>
            <div className="mx-auto mb-4 flex justify-center"><Avatar name={asText(current.agent_name)} userId={asText(current.agent_id)} hasPhoto size={64} onDark /></div>
            <p className="text-xl font-semibold leading-snug">{asText(current.message)}</p>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
