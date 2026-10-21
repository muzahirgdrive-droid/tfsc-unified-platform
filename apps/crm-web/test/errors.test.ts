import { describe, expect, it } from 'vitest';
import { scrub } from '@/lib/errors';

describe('browser error reports', () => {
  it('carry no cookies, headers, bodies, query strings or user', () => {
    const e = scrub({
      type: undefined,
      request: { url: 'https://crm.example.com/leads?phone=4155550101', cookies: { crm_session: 'x' }, headers: { Authorization: 'y' }, data: '{"name":"A"}', query_string: 'phone=4155550101' },
      user: { email: 'a@b.test', id: '1' },
    });
    expect(e.request).toEqual({ url: 'https://crm.example.com/leads' });
    expect(e.user).toBeUndefined();
  });
});
