import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Toggle3 } from '@/components/ui/Toggle3';
import { applyToggle } from '@/routes/MyWork';

afterEach(cleanup);

describe('Toggle3', () => {
  it('announces its state and cycles Unset → Yes → No → Unset on click', () => {
    const onChange = vi.fn();
    const { rerender } = render(<Toggle3 label="Called" value={null} onChange={onChange} />);
    const b = screen.getByRole('switch', { name: 'Called: Unset' });
    expect(b.getAttribute('aria-checked')).toBe('mixed');
    fireEvent.click(b); expect(onChange).toHaveBeenLastCalledWith(true);
    rerender(<Toggle3 label="Called" value={true} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Called: Yes' })); expect(onChange).toHaveBeenLastCalledWith(false);
    rerender(<Toggle3 label="Called" value={false} onChange={onChange} />);
    fireEvent.click(screen.getByRole('switch', { name: 'Called: No' })); expect(onChange).toHaveBeenLastCalledWith(null);
  });
  it('sets values directly with Y / N / U', () => {
    const onChange = vi.fn();
    render(<Toggle3 label="Text" value={null} onChange={onChange} />);
    const b = screen.getByRole('switch');
    fireEvent.keyDown(b, { key: 'y' }); expect(onChange).toHaveBeenLastCalledWith(true);
    fireEvent.keyDown(b, { key: 'N' }); expect(onChange).toHaveBeenLastCalledWith(false);
    fireEvent.keyDown(b, { key: 'u' }); expect(onChange).toHaveBeenLastCalledWith(null);
  });
});

describe('applyToggle mirrors the server rules', () => {
  const base = { name: '', phone: '', email: '', called: null, connected: null, text_sent: null, email_sent: null, followup_needed: null, next_followup: null, remarks: '' } as const;
  it('Connected = Yes implies Called = Yes', () => {
    expect(applyToggle({ ...base }, 'connected', true)).toMatchObject({ called: true, connected: true });
  });
  it('Called = No clears a Yes on Connected', () => {
    expect(applyToggle({ ...base, called: true, connected: true }, 'called', false)).toMatchObject({ called: false, connected: null });
  });
  it('Called = Unset clears Connected', () => {
    expect(applyToggle({ ...base, called: true, connected: false }, 'called', null)).toMatchObject({ called: null, connected: null });
  });
});
