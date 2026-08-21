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
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3100',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  // Run against a PRODUCTION build, not `next dev`.
  //
  // In dev, `router.push` waits on on-demand route compilation, so a
  // client-side navigation can stall past the test timeout after an edit —
  // producing failures that have nothing to do with the app. A production build
  // has no compile step and is what actually ships.
  //
  // Skipped when E2E_BASE_URL points at a deployed environment.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: 'npm run build && npm run start -- --port 3100',
        url: 'http://localhost:3100/login',
        reuseExistingServer: !process.env.CI,
        timeout: 180_000,
        stdout: 'ignore',
      },
});
