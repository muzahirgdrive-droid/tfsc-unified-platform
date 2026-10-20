// Shared fixtures for the browser acceptance tests. Users and passwords are the synthetic ones that
// ../crm-api/tools/e2e_stack.py seeds into a throwaway database.
import { test as base, expect, type APIRequestContext, type BrowserContext, type Page } from '@playwright/test';
import { randomInt } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

export const PW = 'E2E-password-2026!';
export const USERS = ['super', 'director', 'manager', 'sup', 'sup2', 'agent1', 'agent2', 'agent3', 'agent4'] as const;
export type User = (typeof USERS)[number];
export const NAMES: Record<User, string> = {
  super: 'Sara Super', director: 'Daniel Director', manager: 'Mona Manager', sup: 'Samir Supervisor', sup2: 'Selena Supervisor',
  agent1: 'Ava Rahman', agent2: 'Bilal Lopez', agent3: 'Carmen Siddiqui', agent4: 'Daniyal Chen',
};
export const TFSC_ROOT = { email: 'root@tfsc.e2e', password: PW };
export const stateFile = (key: User) => fileURLToPath(new URL(`./.auth/${key}.json`, import.meta.url));

export function stack(): { crm: string; tfsc: string | null; ids: Record<string, string> } {
  return JSON.parse(readFileSync(fileURLToPath(new URL('./.stack.json', import.meta.url)), 'utf8'));
}

/** A signed-in API client for arranging state (sends the CSRF header on writes). */
export class Api {
  private constructor(readonly http: APIRequestContext, private csrf: string) {}

  static async as(playwright: typeof import('@playwright/test'), baseURL: string, key: User): Promise<Api> {
    const http = await playwright.request.newContext({ baseURL, storageState: stateFile(key) });
    const me = await http.get('/api/auth/me');
    expect(me.status()).toBe(200);
    return new Api(http, (await me.json()).csrf);
  }

  async get<T = any>(path: string): Promise<T> {
    const r = await this.http.get(path);
    expect(r.status(), `${path}: ${await r.text()}`).toBe(200);
    return r.json();
  }

  async send<T = any>(method: 'POST' | 'PUT' | 'PATCH', path: string, data: unknown, status = 200): Promise<T> {
    const r = await this.http.fetch(path, { method, data, headers: { 'X-CSRF-Token': this.csrf } });
    expect(r.status(), `${method} ${path}: ${await r.text()}`).toBe(status);
    return r.json();
  }

  /** A phone number nobody in the workspace has touched (the pre-dial check says "new"). */
  async freshPhone(): Promise<string> {
    for (;;) {
      const n = `(415) 5${String(randomInt(10, 100))}-${String(randomInt(0, 10000)).padStart(4, '0')}`;
      const r = await this.get(`/api/precheck?phone=${encodeURIComponent(n)}`);
      if (r.state === 'new') return n;
    }
  }

  async dispose() { await this.http.dispose(); }
}

type AsOptions = {
  /** Auto-dismiss a sale celebration that covers the page when the test acts (default true). Turn off on pages that
   * assert overlays: Playwright runs handlers before assertions too. */
  dismissOverlays?: boolean;
};

type Fixtures = {
  /** Open a page signed in as `key`, with this project's device settings (desktop or mobile). Celebrations of sales made
   * before the page opened are marked as seen (they would otherwise replay for 10 minutes and cover the page). */
  as: (key: User, opts?: AsOptions) => Promise<Page>;
  /** API client signed in as `key`. */
  api: (key: User) => Promise<Api>;
};

export const test = base.extend<Fixtures>({
  as: async ({ browser }, use, testInfo) => {
    const contexts: BrowserContext[] = [];
    const u = testInfo.project.use;
    await use(async (key, { dismissOverlays = true } = {}) => {
      const ctx = await browser.newContext({
        baseURL: u.baseURL, viewport: u.viewport, userAgent: u.userAgent, deviceScaleFactor: u.deviceScaleFactor, isMobile: u.isMobile,
        hasTouch: u.hasTouch, timezoneId: u.timezoneId, locale: u.locale, storageState: stateFile(key), reducedMotion: 'reduce',
      });
      contexts.push(ctx);
      const recent = await (await ctx.request.get('/api/celebrations')).json();
      const seen = (recent.recent ?? []).map((c: { id: string }) => c.id);
      await ctx.addInitScript((ids) => { try { localStorage.setItem('crm.celebrations.seen', JSON.stringify(ids)); } catch { /* ignore */ } }, seen);
      const page = await ctx.newPage();
      page.on('pageerror', (e) => testInfo.annotations.push({ type: 'pageerror', description: `${key}: ${e.message}` }));
      if (dismissOverlays) {
        await page.addLocatorHandler(page.getByRole('alertdialog', { name: 'Sale celebration' }), async (o) => {
          await o.getByRole('button', { name: 'Dismiss' }).click();
        });
      }
      return page;
    });
    for (const c of contexts) await c.close();
  },
  api: async ({ playwright }, use, testInfo) => {
    const made: Api[] = [];
    await use(async (key) => {
      const a = await Api.as(playwright, testInfo.project.use.baseURL!, key);
      made.push(a);
      return a;
    });
    for (const a of made) await a.dispose();
  },
});

export { expect };

export const isMobile = (page: Page) => (page.viewportSize()?.width ?? 1280) < 768;

/** Navigate with the app's own links (main nav on desktop, the Menu drawer on mobile). */
export async function nav(page: Page, name: string) {
  if (isMobile(page)) await page.getByRole('button', { name: 'Menu' }).click();
  await page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name, exact: true }).click();
}

/** Arrange a lead owned by `owner` through the API: connected entry -> strong FU -> supervisor confirms -> convert. */
export async function makeLead(agent: Api, sup: Api, name: string, phone: string) {
  const act = (await agent.send('POST', '/api/activities', { name, phone, called: true, connected: true })).activity;
  const strong = await agent.send('POST', `/api/activities/${act.id}/strong`, { target_window: '2-3 weeks', note: 'Wants a demo' });
  const pending = (await sup.get<any[]>('/api/strong')).find((x) => x.id === strong.id);
  await sup.send('POST', `/api/strong/${strong.id}/decide`, { version: pending.version, decision: 'confirmed' });
  const lead = await sup.send('POST', `/api/strong/${strong.id}/convert`, {});
  return { activity: act, lead };
}

/** Unique, readable practice name for this test run. */
export const practice = (label: string) => `${label} ${randomInt(1000, 9999)}`; // no legal suffix: the server refines "llc" to ", LLC"
