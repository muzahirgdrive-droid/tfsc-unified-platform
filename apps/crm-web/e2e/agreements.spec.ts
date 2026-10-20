// Acceptance 12 in the browser, across both apps: a supervisor with an Agreements grant proceeds from a signed-up lead
// (Agreement Lock), signs in to TFSC from the handoff link, submits for independent review; the TFSC Super Admin
// approves; documents are issued by the worker and the CRM client profile appears via TFSC's signed webhook.
// Needs the TFSC stack (LibreOffice renderer); skipped when the stack started without it.
import { test, expect, makeLead, practice, stack, PW, TFSC_ROOT } from './helpers';

test('proceed to Agreements, review, issue, client profile', async ({ as, api, browser }, testInfo) => {
  const tfsc = stack().tfsc;
  test.skip(!tfsc, 'TFSC Agreements not running (LibreOffice not installed)');
  test.setTimeout(240_000);
  const name = practice('agreement clinic');
  const agentApi = await api('agent1');
  const supApi = await api('sup');
  const { lead } = await makeLead(agentApi, supApi, name, await agentApi.freshPhone());
  const detail = await supApi.get(`/api/leads/${lead.id}`);
  await supApi.send('POST', `/api/leads/${lead.id}/transition`, { version: detail.version, action: 'sign_up' });

  // CRM: Proceed to Agreements (BAA only: a Services Agreement needs services and pricing completed in TFSC first)
  const sup = await as('sup');
  await sup.goto('/leads');
  await sup.getByRole('button', { name: new RegExp(name, 'i') }).first().click();
  await sup.getByRole('dialog', { name: new RegExp(name, 'i') }).getByRole('button', { name: 'Proceed to Agreements' }).click();
  const pre = sup.getByRole('dialog', { name: 'Proceed to Agreements' });
  await pre.getByLabel('Address').fill('100 Example Street, New York, NY 10001');
  await pre.getByLabel('Signatory').fill('Pat Owner');
  await pre.getByLabel('Title').fill('Owner');
  await pre.getByLabel('Services Agreement').uncheck();
  await expect(pre.getByLabel('TFSC contracting company')).toHaveValue('SCRIBE ALIGN LLC');
  await pre.getByRole('button', { name: 'Lock & open in TFSC Agreements' }).click();
  await expect(sup.getByText('Draft created in TFSC Agreements. Complete services and pricing there.')).toBeVisible();
  const opened = sup.context().waitForEvent('page');
  await sup.getByRole('button', { name: 'Open Agreements' }).click();
  const t = await opened;

  // TFSC: not signed in yet -> login, then straight back to the handoff, which redeems itself
  await t.waitForURL(/\/login\?next=\/reviews/);
  await t.fill('#email', 'sup@e2e.test');
  await t.fill('#password', PW);
  await t.click('#login-submit');
  await t.waitForURL(/\/reviews/);
  await expect(t.locator('#redeemed')).toContainText('Received', { timeout: 20_000 });
  const clientId = new URL(await t.locator('#redeemed a').getAttribute('href') ?? '', tfsc!).searchParams.get('client')!;
  await t.locator('#clients').selectOption(clientId);
  await t.click('#submit');
  const article = t.locator('#reviews article').filter({ hasText: new RegExp(name, 'i') }).first();
  await expect(article.locator('h3')).toContainText('· submitted');

  // TFSC Super Admin approves the exact revision (independent reviewer)
  const rctx = await browser.newContext({ viewport: testInfo.project.use.viewport, isMobile: testInfo.project.use.isMobile });
  const r = await rctx.newPage();
  await r.goto(`${tfsc}/login`);
  await r.fill('#email', TFSC_ROOT.email);
  await r.fill('#password', TFSC_ROOT.password);
  await r.click('#login-submit');
  await r.waitForURL(`${tfsc}/`);
  await r.goto(`${tfsc}/reviews`);
  const item = r.locator('#reviews article').filter({ hasText: new RegExp(name, 'i') }).first();
  r.once('dialog', (d) => d.accept('Client details checked by the E2E reviewer.'));
  await item.getByRole('button', { name: 'Approve exact revision' }).click();
  await expect(item.locator('h3')).toContainText('· approved');
  await rctx.close();

  // Submitter generates; the worker renders and issues
  await t.click('#refresh');
  await article.getByRole('button', { name: 'Generate approved documents' }).click();
  await expect.poll(async () => { await t.click('#refresh'); return article.locator('h3').textContent(); },
    { timeout: 150_000, intervals: [2_000] }).toContain('· issued');

  // CRM: the client profile appears without any manual sync, with the issued agreement
  await sup.bringToFront();
  await expect.poll(async () => {
    await sup.goto('/clients');
    await expect(sup.getByRole('heading', { name: 'Signed relationships' })).toBeVisible();
    await expect(sup.locator('main .skeleton')).toHaveCount(0); // loaded
    return sup.getByRole('button', { name: new RegExp(name, 'i') }).count();
  }, { timeout: 60_000, intervals: [2_000] }).toBeGreaterThan(0);
  await sup.getByRole('button', { name: new RegExp(name, 'i') }).first().click();
  const profile = sup.getByRole('dialog', { name: new RegExp(name, 'i') });
  await expect(profile.getByText('issued', { exact: true }).first()).toBeVisible();
});
