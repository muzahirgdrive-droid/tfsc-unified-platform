import * as D from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/cn';

/** Modal dialog. Pass dismissOnOutside={false} for data-entry forms: outside clicks keep typed values
 * (v1.0.1 rule for Add/Manage User); Close and Escape still close. */
export function Dialog({ open, onOpenChange, title, description, children, footer, wide, dismissOnOutside = true }: {
  open: boolean; onOpenChange: (v: boolean) => void; title: string; description?: string; children: ReactNode; footer?: ReactNode; wide?: boolean; dismissOnOutside?: boolean;
}) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-50 bg-navy/40 backdrop-blur-[2px] data-[state=open]:animate-in" />
        <D.Content
          onPointerDownOutside={(e) => { if (!dismissOnOutside) e.preventDefault(); }}
          onInteractOutside={(e) => { if (!dismissOnOutside) e.preventDefault(); }}
          className={cn('card fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-24px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto p-6 shadow-[var(--shadow-lift)]', wide ? 'max-w-3xl' : 'max-w-lg')}
        >
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <D.Title className="text-lg font-semibold">{title}</D.Title>
              {description && <D.Description className="mt-1 text-sm text-muted">{description}</D.Description>}
            </div>
            <D.Close className="btn-ghost h-8 w-8 px-0" aria-label="Close"><X size={16} /></D.Close>
          </div>
          {children}
          {footer && <div className="mt-6 flex flex-wrap justify-end gap-2">{footer}</div>}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}
