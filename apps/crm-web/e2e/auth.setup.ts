// Signs each synthetic user in once and saves the session, so tests do not hit the per-account sign-in limit
// (10 per 15 minutes). The web app reads its CSRF token from /api/auth/me, so the session cookie is enough.
// A saved session that still works is reused (local re-runs against an already-running stack).
import { test as setup, expect } from '@playwright/test';
import { existsSync } from 'node:fs';
import { PW, USERS, stateFile } from './helpers';

for (const key of USERS) {
  setup(`sign in ${key}`, async ({ request, playwright, baseURL }) => {
    if (existsSync(stateFile(key))) {
      const saved = await playwright.request.newContext({ baseURL, storageState: stateFile(key) });
      const ok = (await saved.get('/api/auth/me')).status() === 200;
      await saved.dispose();
      if (ok) return;
    }
    const r = await request.post('/api/auth/login', { data: { email: `${key}@e2e.test`, password: PW } });
    expect(r.status(), await r.text()).toBe(200);
    await request.storageState({ path: stateFile(key) });
  });
}
