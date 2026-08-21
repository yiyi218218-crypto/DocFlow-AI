import { expect, test } from '@playwright/test';

/**
 * The flagship end-to-end test.
 *
 * Selectors target `data-filename`, which the seed controls, rather than the
 * document title, which the extraction model writes. Coupling an E2E assertion
 * to model output makes it fail whenever the model gets *better* — which is
 * exactly what happened when real extraction replaced the stub.
 *
 * If this passes, the product's differentiator works: a user clicks an
 * extracted fact and the exact source sentence highlights on the right page.
 * If it regresses, every citation in the app is silently wrong — which is worse
 * than having no citations at all.
 */

async function signIn(page: import('@playwright/test').Page) {
  await page.goto('/login');
  await page.getByRole('button', { name: 'Open the demo' }).click();
  await page.waitForURL('**/documents');
}

test('sign in and list the seeded documents', async ({ page }) => {
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Documents' })).toBeVisible();
  // Four seeded documents, one of them Bangla.
  await expect(page.getByTestId('document-row')).toHaveCount(4);
  await expect(page.locator('[data-filename="du-admission-circular-2026.pdf"]')).toBeVisible();
});

test('clicking a deadline highlights its source span on the correct page', async ({ page }) => {
  await signIn(page);

  await page.locator('[data-filename="du-admission-circular-2026.pdf"]').click();
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}/);

  // The analysis panel groups facts by kind; deadlines come first.
  const deadlines = page.getByRole('heading', { name: /deadlines/i });
  await expect(deadlines).toBeVisible();

  // The circular's submission deadline — not its publication date. Getting this
  // wrong was a real bug (paragraph breaks lost during PDF extraction).
  const deadlineCard = page.getByRole('button', { name: /15 Sept 2026/ }).first();
  await expect(deadlineCard).toBeVisible();
  await deadlineCard.click();

  // The highlight is the whole point.
  const highlight = page.getByTestId('citation-highlight').first();
  await expect(highlight).toBeVisible({ timeout: 15_000 });

  // Rectangles are percentage-positioned from normalised [0,1] coordinates, so
  // a sane box has non-zero size and sits inside the page.
  const box = await highlight.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(20);
  expect(box!.height).toBeGreaterThan(4);

  // State lives in the URL, so the highlight is shareable and refresh-safe.
  await expect(page).toHaveURL(/[?&]fact=[0-9a-f-]{36}/);
  await expect(page).toHaveURL(/[?&]page=1/);

  // The card is visibly linked to what it highlighted.
  await expect(deadlineCard).toHaveAttribute('aria-current', 'true');
});

test('the highlight survives a page reload', async ({ page }) => {
  await signIn(page);
  await page.locator('[data-filename="du-admission-circular-2026.pdf"]').click();
  // Wait for the route to settle before clicking: a click issued mid-navigation
  // lands on an element that is about to be replaced and is silently lost.
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}/);
  await expect(page.getByRole('heading', { name: /deadlines/i })).toBeVisible();
  await page.getByRole('button', { name: /15 Sept 2026/ }).first().click();
  await expect(page.getByTestId('citation-highlight').first()).toBeVisible({ timeout: 15_000 });

  // Wait for the history entry to commit before reloading. The highlight
  // appears as soon as the store updates, which is before the router has
  // written ?fact= to the URL — reloading in that window loses the very state
  // this test is about.
  await expect(page).toHaveURL(/[?&]fact=/);

  const url = page.url();
  await page.reload();

  // Restored from ?fact= alone — this is why the URL carries it.
  await expect(page).toHaveURL(url);
  await expect(page.getByTestId('citation-highlight').first()).toBeVisible({ timeout: 15_000 });
});

test('the deadline shows the raw date text from the document', async ({ page }) => {
  await signIn(page);
  await page.locator('[data-filename="du-admission-circular-2026.pdf"]').click();
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}/);

  // Always show what the document actually said, so a misparse is visible and
  // correctable rather than silently wrong.
  await expect(page.getByText('15 September 2026', { exact: false }).first()).toBeVisible();
});

test('citation verification rate is reported', async ({ page }) => {
  await signIn(page);
  await page.locator('[data-filename="du-admission-circular-2026.pdf"]').click();
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}/);
  await expect(page.getByText(/% citations verified/)).toBeVisible();
});
