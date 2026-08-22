import { expect, test } from '@playwright/test';

/**
 * Account creation and password recovery.
 *
 * The reset *token* round-trip is covered in the API suite
 * (tests/security/test_password_reset.py), where the link can be read directly.
 * What only a browser can check is here: that the pages are reachable from the
 * login screen, that a new account lands in a working session, and — the one
 * that matters — that the forgot-password screen looks identical whether or not
 * the address has an account. A UI that says "sent" for real users and
 * "not found" for the rest is an enumeration oracle no matter how careful the
 * API response is.
 */

function freshEmail() {
  // Playwright projects run the same spec twice (desktop + mobile), so the
  // address has to be unique per test, not per run.
  return `e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@docflow-e2e.dev`;
}

test('create an account from the login page', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('link', { name: 'Create an account' }).click();
  await page.waitForURL('**/signup');

  await page.getByLabel('Name').fill('Nusrat Jahan');
  await page.getByLabel('Email').fill(freshEmail());
  await page.getByLabel('Password').fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Create account' }).click();

  // A new workspace, signed in — not bounced back to the login form.
  await page.waitForURL('**/documents');
  await expect(page.getByRole('heading', { name: 'Documents' })).toBeVisible();
});

test('signup rejects a duplicate address without losing what was typed', async ({ page }) => {
  await page.goto('/signup');
  await page.getByLabel('Email').fill('demo@docflow.ai');
  await page.getByLabel('Password').fill('correct-horse-battery');
  await page.getByRole('button', { name: 'Create account' }).click();

  await expect(page.getByTestId('form-error')).toBeVisible();
  await expect(page).toHaveURL(/\/signup/);
  // Re-typing an email after a server-side rejection is a small thing that
  // makes a form feel broken.
  await expect(page.getByLabel('Email')).toHaveValue('demo@docflow.ai');
});

test('forgot-password answers the same for a real and an unknown address', async ({ page }) => {
  async function confirmationFor(email: string) {
    await page.goto('/forgot-password');
    await page.getByLabel('Email').fill(email);
    await page.getByRole('button', { name: 'Send reset link' }).click();
    const heading = page.getByRole('heading', { name: 'Check your email' });
    await expect(heading).toBeVisible();
    // Strip the echoed address; everything else must match byte for byte.
    const body = await page.locator('main').innerText();
    return body.replaceAll(email, '{email}');
  }

  const known = await confirmationFor('demo@docflow.ai');
  const unknown = await confirmationFor('nobody-8391@docflow-e2e.dev');
  expect(known).toBe(unknown);
});

test('reset-password says what is wrong when the link has no token', async ({ page }) => {
  await page.goto('/reset-password');
  await expect(page.getByRole('heading', { name: 'This link is incomplete' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Request a new link' })).toBeVisible();
});

test('forgot-password is reachable from the sign-in form', async ({ page }) => {
  await page.goto('/login');
  await page.getByRole('link', { name: 'Forgot password?' }).click();
  await page.waitForURL('**/forgot-password');
  await expect(page.getByRole('heading', { name: 'Reset your password' })).toBeVisible();
});
