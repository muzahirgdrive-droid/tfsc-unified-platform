// Visual regression for dashboards (§26). sup2 and agent4 are untouched by other tests and their data is seeded at stack
// start, so their dashboards are stable. Masked: anything showing the clock or the date (greeting, world clock, chart
// canvases with time axes) and org-wide widgets.
import { test, expect } from './helpers';

for (const who of ['sup2', 'agent4'] as const) {
  test(`dashboard snapshot (${who})`, async ({ as }) => {
    const page = await as(who);
    await page.goto('/');
    const hello = page.getByRole('heading', { name: /^Good (morning|afternoon|evening),/ });
    await expect(hello).toBeVisible();
    // watermark (name + timestamp) hidden; the fixed mobile bottom bar would land mid-page in a full-page capture
    await page.addStyleTag({ content: '.watermark,nav[aria-label="Quick"]{display:none!important}' });
    await expect(page.getByText(/Today's attempts|Live team board/).first()).toBeVisible();
    // Org-wide widgets change (and grow) with every other test: leave them out of the picture entirely.
    await page.evaluate((titles) => {
      for (const h of document.querySelectorAll('section h3')) {
        if (titles.some((t) => h.textContent?.trim().startsWith(t))) (h.closest('section') as HTMLElement).style.display = 'none';
      }
    }, ['Productivity leaderboard', 'Recent celebrations']);
    await page.waitForTimeout(2500); // chart animations (not covered by reduced motion) finish
    await expect(page).toHaveScreenshot(`overview-${who}.png`, {
      fullPage: true,
      mask: [
        hello, page.getByRole('button', { name: /World clock/ }), page.locator('main canvas'), page.locator('time'),
        page.getByRole('img', { name: /^(active|idle|offline)$/ }), // activity dots turn idle 15 min after the seed
      ],
    });
  });
}
