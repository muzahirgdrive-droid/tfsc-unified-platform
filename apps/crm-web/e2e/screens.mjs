// Captures desktop + mobile screenshots of the main screens against a running demo server.
// Usage: node e2e/screens.mjs [baseUrl] [outDir]
// SCREENS_DOMAIN / SCREENS_PASSWORD pick the synthetic accounts (default: tools/seed_demo.py; e2e.test for the E2E stack).
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const base = process.argv[2] ?? 'http://127.0.0.1:8766';
const domain = process.env.SCREENS_DOMAIN ?? 'demo.test';
const password = process.env.SCREENS_PASSWORD ?? 'Demo-password-2026!';
const out = process.argv[3] ?? 'screens';
mkdirSync(out, { recursive: true });
const exe = process.env.PW_CHROMIUM ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const browser = await chromium.launch({ executablePath: exe });
const errors = [];

async function shoot(user, pages, viewport, tag) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, timezoneId: 'Asia/Karachi' });
  const page = await ctx.newPage();
  await page.goto(base + '/login');
  await page.fill('#email', user); await page.fill('#password', password);
  await page.click('button:has-text("Sign in")');
  await page.waitForURL(base + '/');
  // Listen only after sign-in: the signed-out /api/auth/me probe on /login is an expected 401.
  page.on('pageerror', (e) => errors.push(`${tag} ${user}: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('404')) errors.push(`${tag} ${user} console: ${m.text()}`); });
  // A sale celebrated in the last 10 minutes replays once per browser; capture it, then dismiss.
  await page.waitForTimeout(2000);
  const dismiss = page.getByRole('button', { name: 'Dismiss' });
  if (await dismiss.isVisible()) { await page.screenshot({ path: `${out}/${tag}-celebration.png` }); await dismiss.click(); await page.waitForTimeout(400); }
  for (const [path, name, tab] of pages) {
    await page.goto(base + path);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1800);
    if (tab) { await page.getByRole('tab', { name: tab }).click(); await page.waitForTimeout(1200); }
    await page.screenshot({ path: `${out}/${tag}-${name}.png`, fullPage: false });
  }
  await ctx.close();
}

await (async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage(); await p.goto(base + '/login'); await p.waitForTimeout(500); await p.screenshot({ path: `${out}/desktop-login.png` }); await ctx.close();
})();
await shoot('agent1@' + domain, [['/', 'agent-overview'], ['/my-work', 'my-work'], ['/sheet', 'sheet'], ['/followups', 'followups'], ['/leads', 'agent-leads']], { width: 1440, height: 900 }, 'desktop');
await shoot('sup@' + domain, [['/', 'supervisor-overview'], ['/strong', 'strong'], ['/flags', 'flags'], ['/meetings', 'meetings'], ['/people', 'people'], ['/wall-of-fame', 'wall-of-fame'], ['/leads', 'supervisor-leads'], ['/clients', 'clients']], { width: 1440, height: 900 }, 'desktop');
await shoot('super@' + domain, [['/', 'super-overview'], ['/settings', 'settings'], ['/settings', 'legacy-import', 'legacy import'], ['/reports', 'reports'],
  ['/security', 'security'], ['/security', 'security-audit', 'audit']], { width: 1440, height: 900 }, 'desktop');
await shoot('manager@' + domain, [['/reports', 'manager-reports']], { width: 1440, height: 1600 }, 'desktop');
await shoot('agent1@' + domain, [['/', 'agent-overview'], ['/my-work', 'my-work'], ['/sheet', 'sheet']], { width: 390, height: 844 }, 'mobile');
await shoot('sup@' + domain, [['/', 'supervisor-overview'], ['/leads', 'supervisor-leads'], ['/flags', 'flags'], ['/clients', 'clients']], { width: 390, height: 844 }, 'mobile');
// TFSC Agreements screens (optional): SCREENS_TFSC=<origin> SCREENS_TFSC_USER=<email> (password: SCREENS_PASSWORD).
if (process.env.SCREENS_TFSC) {
  for (const [viewport, tag] of [[{ width: 1440, height: 900 }, 'desktop'], [{ width: 390, height: 844 }, 'mobile']]) {
    const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on('pageerror', (e) => errors.push(`tfsc ${tag}: ${e.message}`));
    await page.goto(process.env.SCREENS_TFSC + '/login');
    await page.fill('#email', process.env.SCREENS_TFSC_USER ?? ''); await page.fill('#password', password);
    await page.click('#login-submit'); await page.waitForURL(process.env.SCREENS_TFSC + '/');
    for (const [path, name] of [['/', 'builder'], ['/reviews', 'reviews']]) {
      await page.goto(process.env.SCREENS_TFSC + path); await page.waitForTimeout(1500);
      await page.screenshot({ path: `${out}/tfsc-${tag}-${name}.png` });
    }
    await ctx.close();
  }
}
await browser.close();
console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no page errors');
