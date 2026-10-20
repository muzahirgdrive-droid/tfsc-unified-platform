// Sign-in with a password (local accounts) and axe accessibility checks on the main screens.
import AxeBuilder from '@axe-core/playwright';
import { test, expect, PW, NAMES } from './helpers';

const serious = (r: Awaited<ReturnType<AxeBuilder['analyze']>>) =>
  r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical').map((v) => `${v.id}: ${v.help} (${v.nodes.length}× e.g. ${v.nodes[0]?.target})`);

test('password sign-in lands on the home screen; a wrong password is refused', async ({ page }) => {
  await page.goto('/login');
  await page.fill('#email', 'agent3@e2e.test');
  await page.fill('#password', 'not-the-password');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('alert').or(page.getByText(/incorrect|invalid|not match/i)).first()).toBeVisible();
  await page.fill('#password', PW);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.waitForURL((u) => u.pathname === '/');
  await expect(page.getByText(NAMES.agent3.split(' ')[0]!).first()).toBeVisible();
});

test('login page has no serious accessibility violations', async ({ page }) => {
  await page.goto('/login');
  await expect(page.locator('#email')).toBeVisible();
  expect(serious(await new AxeBuilder({ page }).analyze())).toEqual([]);
});

for (const [who, path] of [['agent1', '/'], ['agent1', '/sheet'], ['sup', '/'], ['sup', '/leads'], ['super', '/security']] as const) {
  test(`${path} as ${who} has no serious accessibility violations`, async ({ as }) => {
    const page = await as(who);
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    expect(serious(await new AxeBuilder({ page }).analyze())).toEqual([]);
  });
}
