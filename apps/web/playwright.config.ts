import { defineConfig, devices } from '@playwright/test';

/**
 * E2E runs against the seeded demo workspace, which is populated entirely by
 * the stub provider — so the suite is deterministic, free and needs no API keys.
 *
 * Assumes `make up && make migrate && make seed` and a running api + web.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
