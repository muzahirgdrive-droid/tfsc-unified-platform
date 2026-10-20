import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, call } from '@/lib/api';

const res = (status: number) => new Response(null, { status });

afterEach(() => vi.restoreAllMocks());

describe('call()', () => {
  it('returns data on success', async () => {
    await expect(call(Promise.resolve({ data: { ok: 1 }, response: res(200) }))).resolves.toEqual({ ok: 1 });
  });
  it('throws ApiError with the server message', async () => {
    const p = call(Promise.resolve({ error: { error: 'Duplicate phone' }, response: res(409) }));
    await expect(p).rejects.toBeInstanceOf(ApiError);
    await expect(call(Promise.resolve({ error: { error: 'Duplicate phone' }, response: res(409) }))).rejects.toMatchObject({ status: 409, message: 'Duplicate phone' });
  });
  it('signals sign-out on 401 outside the login page', async () => {
    const spy = vi.fn();
    window.addEventListener('crm:unauthorized', spy);
    await expect(call(Promise.resolve({ error: {}, response: res(401) }))).rejects.toMatchObject({ status: 401 });
    expect(spy).toHaveBeenCalledTimes(1);
    window.removeEventListener('crm:unauthorized', spy);
  });
});
