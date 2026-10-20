// Browser acceptance tests (§26). `npm run build` first; Playwright starts the stack (../crm-api/tools/e2e_stack.py):
// a fresh `crm_e2e` database with synthetic users, TFSC Agreements when LibreOffice is installed, and the CRM on :8799.
//   npm run e2e                     # desktop + mobile
//   PW_CHROMIUM=/path/to/chrome     # use an already-installed Chromium instead of `npx playwright install chromium`
//   E2E_PYTHON=/path/to/python      # Python with the CRM requirements (default: repo .venv, else python3)
import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'node:fs';

const executablePath = process.env.PW_CHROMIUM || undefined;
const python = process.env.E2E_PYTHON ?? (existsSync('../../.venv/bin/python') ? '../../.venv/bin/python' : 'python3');
const port = Number(process.env.E2E_CRM_PORT ?? 8799);

export default defineConfig({
  testDir: './e2e',
  // One seeded server shared by every test, and some flows span several browsers: run serially.
  workers: 1,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  timeout: 120_000,
  expect: { timeout: 15_000, toHaveScreenshot: { maxDiffPixelRatio: 0.02, animations: 'disabled', caret: 'hide' } },
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : [['list']],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    timezoneId: 'America/Los_Angeles',
    locale: 'en-US',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: { executablePath },
  },
  projects: [
    { name: 'setup', testMatch: /auth\.setup\.ts/ },
    { name: 'desktop', dependencies: ['setup'], testMatch: /.*\.spec\.ts/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 860 }, launchOptions: { executablePath } } },
    { name: 'mobile', dependencies: ['setup'], testMatch: /.*\.spec\.ts/, use: { ...devices['Pixel 7'], launchOptions: { executablePath } } },
  ],
  webServer: {
    command: `${python} ../crm-api/tools/e2e_stack.py`,
    url: `http://127.0.0.1:${port}/api/health`,
    timeout: 240_000,
    reuseExistingServer: !process.env.CI,
    stdout: 'pipe',
    env: { E2E_CRM_PORT: String(port) },
  },
});
