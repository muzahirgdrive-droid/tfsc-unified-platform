// Acceptance 6-7 in the browser. Amber: an agent saves a number in another agent's follow-up -> neutral notice that never
// names the other agent; the supervisor sees the flag. Red: an agent saves another agent's lead -> protected-lead warning
// before and after saving, and the supervisor's open browser gets a blocking red alert.
import { test, expect, makeLead, practice, NAMES } from './helpers';

test('amber: number in another agent\'s follow-up', async ({ as, api }) => {
  const owner = await api('agent1');
  const phone = await owner.freshPhone();
  const name = practice('follow clinic');
  await owner.send('POST', '/api/activities', { name, phone, called: true, followup_needed: true, next_followup: '2026-12-01' });

  const page = await as('agent2');
  await page.goto('/my-work');
  const form = page.getByRole('form', { name: 'Quick entry' });
  await form.locator('#q-name').fill(name);
  await form.locator('#q-phone').fill(phone);
  await expect(form.getByRole('status')).toHaveText('In follow-up');
  await form.getByRole('switch', { name: /^Called:/ }).click();
  await form.getByRole('button', { name: /^Save/ }).click();
  await expect(page.getByText('Already in follow-up — supervisor notified')).toBeVisible();
  await expect(page.locator('body')).not.toContainText(NAMES.agent1);

  const sup = await as('sup');
  await sup.goto('/flags');
  await expect(sup.getByRole('row').filter({ hasText: name }).filter({ hasText: 'amber' })).toBeVisible();
});

test('red: another agent\'s lead raises a blocking alert for the supervisor', async ({ as, api }) => {
  const name = practice('lead practice');
  const phone = await (await api('agent1')).freshPhone();
  await makeLead(await api('agent1'), await api('sup'), name, phone);

  const sup = await as('sup', { dismissOverlays: false });
  await sup.goto('/');
  await expect(sup.getByRole('heading', { name: /^Good (morning|afternoon|evening), Samir\.$/ })).toBeVisible();
  await sup.waitForTimeout(1000); // live connection open

  const page = await as('agent3');
  await page.goto('/my-work');
  const form = page.getByRole('form', { name: 'Quick entry' });
  await form.locator('#q-name').fill(name);
  await form.locator('#q-phone').fill(phone);
  await expect(form.getByRole('status')).toHaveText('Protected lead – do not pursue');
  await form.getByRole('switch', { name: /^Called:/ }).click();
  await form.getByRole('button', { name: /^Save/ }).click();
  await expect(page.getByText('Protected lead — do not pursue. Your entry was recorded and sent to your supervisor.')).toBeVisible();
  await expect(page.locator('body')).not.toContainText(NAMES.agent1);

  const alert = sup.getByRole('alertdialog', { name: /Red alert: protected lead contacted/ });
  await expect(alert).toBeVisible({ timeout: 10_000 });
  await expect(alert).not.toContainText(name, { ignoreCase: true }); // no client details in the alert
  await sup.keyboard.press('Escape');
  await expect(alert).toBeVisible(); // blocking: Escape does not close it
  await alert.getByRole('button', { name: 'Review in Flags center' }).click();
  await expect(sup).toHaveURL(/\/flags$/);
  await expect(sup.getByRole('alert')).toContainText('red alert');
  await expect(sup.getByRole('row').filter({ hasText: name }).filter({ hasText: 'red' })).toBeVisible();
});
