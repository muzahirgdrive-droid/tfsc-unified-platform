// Signs each synthetic user in once and saves the session, so tests do not hit the per-account sign-in limit
// (10 per 15 minutes). The web app reads its CSRF token from /api/auth/me, so the session cookie is enough.
import { test as setup, expect } from '@playwright/test';
import { PW, USERS, stateFile } from './helpers';

for (const key of USERS) {
  setup(`sign in ${key}`, async ({ request }) => {
    const r = await request.post('/api/auth/login', { data: { email: `${key}@e2e.test`, password: PW } });
    expect(r.status(), await r.text()).toBe(200);
    await request.storageState({ path: stateFile(key) });
  });
}
