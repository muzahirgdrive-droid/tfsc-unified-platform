// Captures desktop + mobile screenshots of the main screens against a running demo server.
// Usage: node e2e/screens.mjs [baseUrl] [outDir]
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';

const base = process.argv[2] ?? 'http://127.0.0.1:8766';
const out = process.argv[3] ?? 'screens';
mkdirSync(out, { recursive: true });
const exe = process.env.PW_CHROMIUM ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';
const browser = await chromium.launch({ executablePath: exe });
const errors = [];

async function shoot(user, pages, viewport, tag) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, timezoneId: 'Asia/Karachi' });
  const page = await ctx.newPage();
  await page.goto(base + '/login');
  await page.fill('#email', user); await page.fill('#password', 'Demo-password-2026!');
  await page.click('button:has-text("Sign in")');
  await page.waitForURL(base + '/');
  // Listen only after sign-in: the signed-out /api/auth/me probe on /login is an expected 401.
  page.on('pageerror', (e) => errors.push(`${tag} ${user}: ${e.message}`));
  page.on('console', (m) => { if (m.type() === 'error' && !m.text().includes('404')) errors.push(`${tag} ${user} console: ${m.text()}`); });
  // A sale celebrated in the last 10 minutes replays once per browser; capture it, then dismiss.
  await page.waitForTimeout(2000);
  const dismiss = page.getByRole('button', { name: 'Dismiss' });
  if (await dismiss.isVisible()) { await page.screenshot({ path: `${out}/${tag}-celebration.png` }); await dismiss.click(); await page.waitForTimeout(400); }
  for (const [path, name] of pages) {
    await page.goto(base + path);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1800);
    await page.screenshot({ path: `${out}/${tag}-${name}.png`, fullPage: false });
  }
  await ctx.close();
}

await (async () => {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage(); await p.goto(base + '/login'); await p.waitForTimeout(500); await p.screenshot({ path: `${out}/desktop-login.png` }); await ctx.close();
})();
await shoot('agent1@demo.test', [['/', 'agent-overview'], ['/my-work', 'my-work'], ['/sheet', 'sheet'], ['/followups', 'followups'], ['/leads', 'agent-leads']], { width: 1440, height: 900 }, 'desktop');
await shoot('sup@demo.test', [['/', 'supervisor-overview'], ['/strong', 'strong'], ['/flags', 'flags'], ['/meetings', 'meetings'], ['/people', 'people'], ['/wall-of-fame', 'wall-of-fame']], { width: 1440, height: 900 }, 'desktop');
await shoot('super@demo.test', [['/', 'super-overview'], ['/settings', 'settings'], ['/reports', 'reports']], { width: 1440, height: 900 }, 'desktop');
await shoot('agent1@demo.test', [['/', 'agent-overview'], ['/my-work', 'my-work'], ['/sheet', 'sheet']], { width: 390, height: 844 }, 'mobile');
await browser.close();
console.log(errors.length ? 'ERRORS:\n' + errors.join('\n') : 'no page errors');
