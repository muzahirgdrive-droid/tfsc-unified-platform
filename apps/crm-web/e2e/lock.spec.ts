// Acceptance 2-3 in the browser: a row with no toggles is data hunting (no timer); Called = No starts the edit timer
// without counting an attempt; Called = Yes later counts the attempt and keeps the deadline; the row locks after 60 min.
import { test, expect, practice } from './helpers';

const attempts = async (page: import('@playwright/test').Page) =>
  Number((await page.getByText(/\d+ attempts · \d+ connected/).textContent())!.match(/(\d+) attempts/)![1]);

test('toggle lock countdown', async ({ as, api }) => {
  const agent = await api('agent3');
  const page = await as('agent3');
  await page.clock.install();
  await page.goto('/my-work');
  await expect(page.getByRole('heading', { name: "Today's conversations" })).toBeVisible();
  const form = page.getByRole('form', { name: 'Quick entry' });

  const research = practice('research only');
  await form.locator('#q-name').fill(research);
  await form.locator('#q-phone').fill(await agent.freshPhone());
  await form.getByRole('button', { name: /^Save/ }).click();
  const researchRow = page.locator('li').filter({ hasText: research });
  await expect(researchRow.getByText('Data hunting', { exact: true })).toBeVisible(); // no timer
  await expect(researchRow.getByText('0 pts · Data hunting')).toBeVisible(); // no points

  const before = await attempts(page);
  const name = practice('timer clinic');
  await form.locator('#q-name').fill(name);
  await form.locator('#q-phone').fill(await agent.freshPhone());
  const called = form.getByRole('switch', { name: /^Called:/ });
  await called.focus();
  await called.press('n');
  await expect(called).toHaveAccessibleName('Called: No');
  await form.getByRole('button', { name: /^Save/ }).click();
  const row = page.locator('li').filter({ hasText: name });
  await expect(row.getByText(/^Editable \d+:\d\d$/)).toBeVisible();
  await expect.poll(() => attempts(page)).toBe(before); // Called = No: timer, no attempt

  const id = (await agent.get<{ items: { id: string; provider_name: string; edit_deadline: string }[] }>('/api/activities?limit=20'))
    .items.find((a) => a.provider_name.toLowerCase() === name.toLowerCase())!; // refine title-cases the name
  await row.getByRole('button', { name: 'Edit' }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit entry' });
  const calledEdit = dialog.getByRole('switch', { name: /^Called:/ });
  await calledEdit.focus();
  await calledEdit.press('y');
  await dialog.getByRole('button', { name: 'Save changes' }).click();
  await expect(page.getByText('Updated')).toBeVisible();
  await expect.poll(() => attempts(page)).toBe(before + 1); // attempt counted when Called = Yes
  const after = await agent.get<{ edit_deadline: string }>(`/api/activities/${id.id}`);
  expect(after.edit_deadline).toBe(id.edit_deadline); // deadline unchanged

  await page.clock.fastForward(61 * 60_000);
  await expect(row.getByText('Locked')).toBeVisible();
  await expect(row.getByRole('button', { name: 'Edit' })).toHaveCount(0); // gone at once, not at the next refresh
});
