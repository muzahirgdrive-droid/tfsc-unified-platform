// Acceptance 9 + 11 in the browser: agent requests a strong follow-up -> supervisor confirms and converts (owner = first
// connector) -> schedules a meeting -> confirms the sale -> a second, already-open browser shows the celebration with the
// agent's and the supervisor's names within 3 s.
import { test, expect, practice, NAMES } from './helpers';

test('strong follow-up to lead to meeting to sale, celebrated in another browser', async ({ as, api }) => {
  const agentApi = await api('agent1');
  const name = practice('pipeline clinic');
  const agent = await as('agent1');
  await agent.goto('/my-work');
  const form = agent.getByRole('form', { name: 'Quick entry' });
  await form.locator('#q-name').fill(name);
  await form.locator('#q-phone').fill(await agentApi.freshPhone());
  await form.getByRole('switch', { name: /^Connected:/ }).click(); // Connected = Yes also sets Called = Yes
  await form.getByRole('button', { name: /^Save/ }).click();
  const row = agent.locator('li').filter({ hasText: name });
  await row.getByRole('button', { name: 'Request strong follow-up' }).click();
  const req = agent.getByRole('dialog', { name: 'Request strong follow-up' });
  await req.locator('textarea').fill('Wants a billing demo next week');
  await req.getByRole('button', { name: 'Send to supervisor' }).click();
  await expect(agent.getByText('Strong follow-up requested. Your supervisor will confirm it.')).toBeVisible();

  const sup = await as('sup');
  await sup.goto('/strong');
  const card = sup.locator('article, .card').filter({ has: sup.getByRole('heading', { name }) }).first();
  await card.getByRole('button', { name: 'Confirm' }).click();
  await expect(sup.getByText('Decision recorded')).toBeVisible();
  await sup.getByRole('button', { name: 'confirmed', exact: true }).click();
  await sup.locator('article, .card').filter({ has: sup.getByRole('heading', { name }) }).first()
    .getByRole('button', { name: `Convert to lead · owner: ${NAMES.agent1}` }).click();
  await sup.getByRole('dialog', { name: 'Convert to lead' }).getByRole('button', { name: 'Convert & lock' }).click();
  await expect(sup.getByText('Converted to lead (first connector)')).toBeVisible();

  await sup.goto('/leads');
  await sup.getByRole('button', { name: new RegExp(name, 'i') }).first().click();
  const lead = sup.getByRole('dialog', { name: new RegExp(name, 'i') });
  await lead.getByRole('button', { name: 'Schedule meeting' }).click();
  await sup.getByRole('dialog', { name: 'Schedule meeting' }).getByRole('button', { name: 'Create meeting' }).click();
  await expect(sup.getByText(/^Meeting (recorded|created)/)).toBeVisible();
  await expect(lead.getByText(/^Meeting · owner/)).toBeVisible();

  // a colleague's browser is already open; the celebration must reach it within 3 s
  const colleague = await as('agent2', { dismissOverlays: false });
  await colleague.goto('/');
  await expect(colleague.getByRole('heading', { name: /^Good (morning|afternoon|evening), Bilal\.$/ })).toBeVisible();
  await colleague.waitForTimeout(1000);
  const t0 = Date.now();
  await lead.getByRole('button', { name: 'Confirm sale (Signed Up)' }).click();
  const party = colleague.getByRole('alertdialog', { name: 'Sale celebration' });
  await expect(party).toBeVisible({ timeout: 3_000 });
  expect(Date.now() - t0).toBeLessThan(3_000);
  await expect(party).toContainText(`Congratulations to ${NAMES.agent1}`);
  await expect(party).toContainText(NAMES.sup);
  await expect(party).not.toContainText(name, { ignoreCase: true }); // no client details in the celebration
});
