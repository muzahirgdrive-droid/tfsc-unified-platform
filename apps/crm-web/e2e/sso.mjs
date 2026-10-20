// End-to-end SSO against a real Keycloak (infra/compose trial stack seeded with infra/keycloak/dev_seed.py).
// Usage: node e2e/sso.mjs <crmOrigin> <totpSecret> [outDir]
import { chromium } from '@playwright/test';
import { createHmac } from 'node:crypto';
import { mkdirSync } from 'node:fs';

const base = process.argv[2] ?? 'http://localhost:8866';
const totpSecret = process.argv[3];
const out = process.argv[4] ?? 'screens';
mkdirSync(out, { recursive: true });
const PW = 'Kc-trial-password-2026!';

// RFC 6238 TOTP; Keycloak keys the HMAC with the UTF-8 bytes of the stored secret.
function totp(secret, t = Date.now()) {
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(t / 1000 / 30)));
  const h = createHmac('sha1', Buffer.from(secret, 'utf8')).update(counter).digest();
  const o = h[h.length - 1] & 0xf;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000)).padStart(6, '0');
}

const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
const results = [];
async function signIn(email, { otp = false, expectError = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await ctx.newPage();
  await page.goto(base + '/login');
  await page.getByRole('link', { name: 'Sign in with company SSO' }).click();
  await page.waitForURL(/\/realms\/tfsc\//);
  await page.fill('#username', email);
  await page.fill('#password', PW);
  await page.click('#kc-login');
  if (otp) {
    await page.waitForSelector('#otp');
    await page.screenshot({ path: `${out}/sso-otp-prompt.png` });
    await page.fill('#otp', totp(totpSecret));
    await page.click('#kc-login');
  }
  if (expectError) {
    await page.waitForURL(new RegExp(`/login\\?sso_error=${expectError}`));
    await page.waitForSelector('[role=alert]');
    await page.screenshot({ path: `${out}/sso-error-${expectError}.png` });
    results.push(`${email}: refused (${expectError}) as expected`);
  } else {
    await page.waitForURL(base + '/');
    const me = await page.evaluate(async () => (await fetch('/api/auth/me')).json());
    if (me.user?.email !== email) throw new Error(`signed in as ${me.user?.email}, expected ${email}`);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${out}/sso-${email.split('@')[0]}.png` });
    results.push(`${email}: signed in as ${me.user.role}`);
  }
  await ctx.close();
}

await signIn('agent@stack.test');
await signIn('sup@stack.test', { otp: true });
await signIn('nototp@stack.test', { expectError: 'unknown_user' });
await browser.close();
console.log(results.join('\n'));
