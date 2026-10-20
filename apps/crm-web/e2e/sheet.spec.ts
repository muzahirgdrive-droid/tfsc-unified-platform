// Acceptance 1: paste 200 messy rows -> Refine shows the preview -> Refine & Save saves 197 and keeps the 3 red rows
// in the sheet with their reasons.
import { randomInt } from 'node:crypto';
import { test, expect, isMobile } from './helpers';

function messyRows() {
  const exchange = randomInt(200, 999);
  const shapes = [
    (i: number) => [`  sunrise   PEDIATRICS pa ${i}`, `415.${exchange}.${String(1000 + i)}`, ` OFFICE${i}@Sunrise-Peds.COM `],
    (i: number) => [`dr john smith md ${i}`, `(415)${exchange}-${String(1000 + i)}`, ''],
    (i: number) => [`HARBOR family medicine llc ${i}`, `+1 415 ${exchange} ${String(1000 + i)}`, `front.desk${i}@harbor.test`],
  ];
  const rows = Array.from({ length: 197 }, (_, i) => [...shapes[i % 3]!(i), i % 4 === 0 ? 'No' : 'Yes', i % 4 !== 0 && i % 5 === 0 ? 'Yes' : 'No', 'e2e paste']);
  // three rows that must not save: bad phone, no provider name, Connected without Called
  rows.splice(10, 0, ['bad number clinic', '12345', '', 'Yes', 'No', '']);
  rows.splice(50, 0, ['', `415-${exchange}-0001`, '', 'Yes', 'No', '']);
  rows.splice(120, 0, ['contradiction care', `415-${exchange}-0002`, '', 'No', 'Yes', '']);
  return [['Provider Name', 'Phone', 'Email', 'Call Attempt', 'Connected', 'Remarks'], ...rows].map((r) => r.join('\t')).join('\n');
}

test('paste 200 rows, refine, save 197 and keep 3 red rows with reasons', async ({ as }) => {
  const page = await as('agent2');
  await page.context().grantPermissions(['clipboard-read', 'clipboard-write']);
  await page.goto('/sheet');
  await expect(page.getByRole('heading', { name: 'Paste a day. Refine. Save.' })).toBeVisible();
  await page.evaluate((t) => navigator.clipboard.writeText(t), messyRows());
  await page.getByRole('button', { name: 'Paste', exact: true }).click();
  await expect(page.getByText('Pasted 200 rows (headers detected). Press Refine to preview.')).toBeVisible();

  await page.getByRole('button', { name: 'Refine', exact: true }).click();
  await expect(page.getByText(/Refined 200 rows in/)).toBeVisible();
  await expect(page.getByText('3 need fixing')).toBeVisible();

  const saved = page.waitForResponse((r) => r.url().endsWith('/api/activities/batch') && r.request().method() === 'POST');
  await page.getByRole('button', { name: 'Refine & Save' }).click();
  const out = await (await saved).json();
  expect([out.saved, out.rejected, out.review]).toEqual([197, 3, 0]);
  const kept = out.rows.filter((r: { status: string }) => r.status !== 'saved');
  expect(kept.map((r: { issues: { message: string }[] }) => r.issues[0]?.message)).toEqual([
    'Phone must have 10 digits (U.S.).', 'Provider or practice name is required.', 'Connected requires Called = Yes.',
  ]);
  await expect(page.getByText(/^Saved 197 rows in [\d.]+ s · 3 kept with reasons/)).toBeVisible();

  // the three rows stay in the sheet, each with its reason
  if (isMobile(page)) {
    for (const reason of ['Phone must have 10 digits (U.S.).', 'Provider or practice name is required.', 'Connected requires Called = Yes.']) {
      await expect(page.getByText(`Not saved · ${reason}`)).toBeVisible();
    }
    await expect(page.getByPlaceholder('Provider Name').first()).toHaveValue('bad number clinic');
  } else {
    await expect(page.getByRole('gridcell', { name: 'bad number clinic' })).toBeVisible();
    await expect(page.getByRole('gridcell', { name: 'contradiction care' })).toBeVisible();
    const status = page.getByRole('gridcell', { name: /^Not saved · / });
    await page.getByTestId('data-grid-canvas').hover();
    await page.mouse.wheel(4000, 0); // the Status column is at the far right
    await expect(status).toHaveCount(3);
    await expect(status.first()).toContainText('Phone must have 10 digits');
  }
});
