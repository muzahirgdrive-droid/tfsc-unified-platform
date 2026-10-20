/** ⌘K / Ctrl+K command palette for navigation. */
import { useNavigate } from '@tanstack/react-router';
import { Command } from 'cmdk';
import { Search } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { Dialog } from '@/components/ui/Dialog';

export function CommandPalette({ items }: { items: { to: string; label: string; icon: ReactNode }[] }) {
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setOpen((v) => !v); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  return (
    <>
      <button className="btn-secondary h-9 w-56 justify-start text-muted max-sm:w-9 max-sm:px-0 max-sm:justify-center" onClick={() => setOpen(true)} aria-label="Search and jump (Ctrl+K)">
        <Search size={15} /><span className="max-sm:hidden">Jump to…</span><kbd className="ml-auto rounded bg-bg px-1.5 text-[10px] max-sm:hidden">Ctrl K</kbd>
      </button>
      <Dialog open={open} onOpenChange={setOpen} title="Jump to">
        <Command label="Navigation" className="text-sm">
          <Command.Input autoFocus placeholder="Type a page…" className="input mb-2" />
          <Command.List>
            <Command.Empty className="p-3 text-muted">No match.</Command.Empty>
            {items.map((i) => (
              <Command.Item key={i.to} value={i.label} onSelect={() => { setOpen(false); nav({ to: i.to }); }}
                className="flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 data-[selected=true]:bg-bg">{i.icon}{i.label}</Command.Item>
            ))}
          </Command.List>
        </Command>
      </Dialog>
    </>
  );
}
