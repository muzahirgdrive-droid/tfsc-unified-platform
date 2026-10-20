// Compose trial in a real browser (see infra/keycloak/trial_seed.py): invitation emails caught by Mailpit -> set a
// password (+ TOTP enrolment for Supervisor+) -> CRM SSO; password recovery by email; TFSC SSO.
// Usage: node e2e/sso-invites.mjs <invites|tfsc> <outDir>   (TRIAL_CRM / TRIAL_TFSC / TRIAL_MAIL override the URLs)
import { chromium } from '@playwright/test';
import { createHmac } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
const [phase, out] = process.argv.slice(2);
mkdirSync(out, { recursive: true });
const CRM = process.env.TRIAL_CRM ?? 'http://crm.localhost:8766', TFSC = process.env.TRIAL_TFSC ?? 'http://tfsc.localhost:8765';
const MAIL = process.env.TRIAL_MAIL ?? 'http://127.0.0.1:8025';
const PW = 'Invited-password-2026!', PW2 = 'Recovered-password-2026!';
const secretsFile = `${out}/totp.json`;
const totpSecrets = existsSync(secretsFile) ? JSON.parse(readFileSync(secretsFile, 'utf8')) : {};
const b32 = (s) => { const A = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'; let bits = ''; for (const ch of s.replace(/[\s=]/g, '').toUpperCase()) bits += A.indexOf(ch).toString(2).padStart(5, '0'); const out = []; for (let i = 0; i + 8 <= bits.length; i += 8) out.push(parseInt(bits.slice(i, i + 8), 2)); return Buffer.from(out); };
function totp(key, t = Date.now()) {
  const c = Buffer.alloc(8); c.writeBigUInt64BE(BigInt(Math.floor(t / 30000)));
  const h = createHmac('sha1', key).update(c).digest(); const o = h[h.length - 1] & 0xf;
  return String(((h.readUInt32BE(o) & 0x7fffffff) % 1_000_000)).padStart(6, '0');
}
// Keycloak refuses a code reused within its 30 s window, so each sign-in waits for a step it has not used yet.
const usedStep = {};
async function freshCode(email) {
  let step = Math.floor(Date.now() / 30000);
  if (usedStep[email] !== undefined && step <= usedStep[email]) {
    await new Promise((res) => setTimeout(res, (usedStep[email] + 1) * 30000 - Date.now() + 500));
    step = Math.floor(Date.now() / 30000);
  }
  usedStep[email] = step;
  return totp(b32(totpSecrets[email]));
}
async function mailLink(to, subjectRe, after = 0) {
  for (let i = 0; i < 40; i++) {
    const list = await (await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent('to:' + to)}`)).json();
    const m = list.messages.filter((x) => subjectRe.test(x.Subject) && Date.parse(x.Created) > after).sort((a, b) => Date.parse(b.Created) - Date.parse(a.Created))[0];
    if (m) {
      const full = await (await fetch(`${MAIL}/api/v1/message/${m.ID}`)).json();
      const link = full.Text.match(/https?:\/\/\S+action-token\S+/)[0];
      return { link, subject: m.Subject, text: full.Text };
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error(`no email for ${to} matching ${subjectRe}`);
}
const browser = await chromium.launch({ executablePath: process.env.PW_CHROMIUM ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
const log = [];
const ctxFor = async () => { const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 } }); return [ctx, await ctx.newPage()]; };

async function acceptInvite(email, mfa) {
  const mail = await mailLink(email, /set up your|update your account/i);
  log.push(`${email}: invitation email "${mail.subject}" received`);
  const [ctx, p] = await ctxFor();
  await p.goto(mail.link);
  await p.screenshot({ path: `${out}/${email.split('@')[0]}-1-invite.png` });
  await p.getByRole('link', { name: /click here to proceed/i }).click();
  // Keycloak runs the required actions in realm priority order (TOTP before password here)
  for (let step = 0; step < 4; step++) {
    await p.locator('#totp').or(p.locator('#password-new')).or(p.getByText(/account has been updated/i)).first().waitFor({ timeout: 20000 });
    if (await p.locator('#password-new').isVisible()) {
      await p.fill('#password-new', PW); await p.fill('#password-confirm', PW);
      await p.screenshot({ path: `${out}/${email.split('@')[0]}-2-password.png` });
      await p.locator('input[type=submit], button[type=submit]').first().click();
    } else if (await p.locator('#totp').isVisible()) {
      await p.getByText(/unable to scan/i).click();
      const key = (await p.locator('#kc-totp-secret-key').textContent()).trim();
      totpSecrets[email] = key; writeFileSync(secretsFile, JSON.stringify(totpSecrets));
      await p.screenshot({ path: `${out}/${email.split('@')[0]}-3-totp.png` });
      await p.fill('#totp', await freshCode(email));
      await p.locator('#userLabel').fill('Trial phone').catch(() => {});
      await p.locator('input[type=submit], button[type=submit]').first().click();
    } else break;
    await p.waitForLoadState();
  }
  if (mfa && !totpSecrets[email]) throw new Error('TOTP enrolment was not offered');
  await p.waitForURL(/crm\.localhost:8766\/login|account has been updated|Back to/i, { timeout: 20000 }).catch(() => {});
  await p.screenshot({ path: `${out}/${email.split('@')[0]}-4-done.png` });
  log.push(`${email}: invitation completed (password set${mfa ? ', TOTP enrolled' : ''}) -> ${p.url().split('?')[0]}`);
  await ctx.close();
}

async function ssoSignIn(email, password, mfa, app = 'crm') {
  const [ctx, p] = await ctxFor();
  if (app === 'crm') { await p.goto(CRM + '/login'); await p.getByRole('link', { name: 'Sign in with company SSO' }).click(); }
  else { await p.goto(TFSC + '/login'); await p.getByRole('link', { name: 'Sign in with company SSO' }).click(); }
  await p.waitForURL(/sso\.localhost:8081\/realms\/tfsc\//);
  await p.fill('#username', email); await p.fill('#password', password); await p.click('#kc-login');
  if (mfa) { await p.waitForSelector('#otp'); await p.fill('#otp', await freshCode(email)); await p.click('#kc-login'); }
  const home = app === 'crm' ? CRM + '/' : TFSC + '/';
  await p.waitForURL((u) => u.href.startsWith(home) && !u.pathname.startsWith('/login') && !u.pathname.startsWith('/api/'), { timeout: 30000 });
  await p.waitForLoadState();
  const me = await p.evaluate(async () => (await (await fetch('/api/auth/me')).json()));
  const who = me.user?.email;
  if (who !== email) throw new Error(`${app}: signed in as ${who}, expected ${email}`);
  await p.waitForTimeout(1200);
  await p.screenshot({ path: `${out}/${app}-sso-${email.split('@')[0]}.png` });
  log.push(`${email}: ${app.toUpperCase()} SSO sign-in OK (${me.user.role ?? me.user.role_label ?? ''})`);
  await ctx.close();
}

async function recover(email) {
  const t0 = Date.now();
  const [ctx, p] = await ctxFor();
  await p.goto(CRM + '/login'); await p.getByRole('link', { name: 'Sign in with company SSO' }).click();
  await p.waitForURL(/sso\.localhost:8081/);
  await p.getByRole('link', { name: /forgot password/i }).click();
  await p.fill('#username', email);
  await p.screenshot({ path: `${out}/recover-1-request.png` });
  await p.locator('input[type=submit], button[type=submit]').first().click();
  await p.waitForTimeout(800);
  await p.screenshot({ path: `${out}/recover-2-sent.png` });
  const mail = await mailLink(email, /reset (your .*)?password/i, t0 - 2000);
  log.push(`${email}: password reset email "${mail.subject}" received`);
  await p.goto(mail.link);
  const proceed = p.getByRole('link', { name: /click here to proceed/i });
  await Promise.race([p.waitForSelector('#password-new'), proceed.waitFor()]);
  if (await proceed.isVisible()) await proceed.click();
  await p.fill('#password-new', PW2); await p.fill('#password-confirm', PW2);
  await p.locator('input[type=submit], button[type=submit]').first().click();
  await p.waitForURL((u) => u.href.startsWith(CRM), { timeout: 30000 }).catch(() => {});
  await p.screenshot({ path: `${out}/recover-3-after.png` });
  log.push(`${email}: password reset completed -> ${p.url().split('?')[0]}`);
  await ctx.close();
}

try {
  if (phase === 'invites') {
    await acceptInvite('agent@trial.test', false);
    await acceptInvite('sup@trial.test', true);
    await ssoSignIn('agent@trial.test', PW, false);
    await ssoSignIn('sup@trial.test', PW, true);
    await recover('agent@trial.test');
    await ssoSignIn('agent@trial.test', PW2, false);
  } else if (phase === 'tfsc') {
    await acceptInvite('late@trial.test', false);
    await ssoSignIn('late@trial.test', PW, false);           // first CRM SSO -> identity linked -> the CRM re-sends the grant to TFSC
    await new Promise((r) => setTimeout(r, 4000));             // outbox -> TFSC
    await ssoSignIn('sup@trial.test', PW, true, 'tfsc');
    await ssoSignIn('late@trial.test', PW, false, 'tfsc');
  }
} finally {
  console.log(log.join('\n'));
  await browser.close();
}
