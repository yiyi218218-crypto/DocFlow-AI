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

test('clicking a deadline highlights its source span on the correct page', async ({
  page,
  isMobile,
}) => {
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

  // The card is visibly linked to what it highlighted. On a phone the list is
  // behind a tab once the document shows, so go back the way a user would —
  // getByRole deliberately ignores anything hidden from the a11y tree.
  if (isMobile) {
    await expect(page.getByRole('tab', { name: /document/i })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await page.getByRole('tab', { name: /insights/i }).click();
  }
  await expect(page.getByRole('button', { name: /15 Sept 2026/ }).first()).toHaveAttribute(
    'aria-current',
    'true',
  );
});

test('the highlight survives a page reload', async ({ page }) => {
  // Also covers the mobile case: a ?fact= link must reopen on the document,
  // not on the list that hides it.
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

test('the source is reachable on a phone', async ({ page, isMobile }) => {
  test.skip(!isMobile, 'covers the tabbed mobile layout only');

  await signIn(page);
  await page.locator('[data-filename="du-admission-circular-2026.pdf"]').click();
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}/);

  // A phone shows tabs, not a split: 50/50 leaves ~330px for each pane, which
  // is too little to read a page or scan a list.
  const tabs = page.getByRole('tab');
  await expect(tabs).toHaveCount(2);
  await expect(page.getByRole('tab', { name: /insights/i })).toHaveAttribute(
    'aria-selected',
    'true',
  );

  // Tapping a fact must actually reveal the source, not just highlight a pane
  // the user cannot see.
  await page.getByRole('button', { name: /15 Sept 2026/ }).first().click();
  await expect(page.getByRole('tab', { name: /document/i })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByTestId('citation-highlight').first()).toBeVisible({ timeout: 20_000 });

  // The page fits the screen; nothing scrolls sideways.
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(overflow).toBeLessThanOrEqual(0);

  const pageWidth = await page
    .locator('[data-page]')
    .first()
    .evaluate((el) => el.getBoundingClientRect().width);
  const viewport = page.viewportSize()!.width;
  expect(pageWidth).toBeGreaterThan(viewport * 0.8);
  expect(pageWidth).toBeLessThanOrEqual(viewport);
});

test('switching between facts does not bounce back to the previous one', async ({
  page,
  isMobile,
}) => {
  test.skip(isMobile, 'the split viewer scroller only exists on desktop');

  await signIn(page);
  await page.locator('[data-filename="du-admission-circular-2026.pdf"]').click();
  await page.waitForURL(/\/documents\/[0-9a-f-]{36}/);
  await expect(page.getByRole('heading', { name: /required/i })).toBeVisible();

  const scroller = page.locator('div.overflow-y-auto').first();
  const cards = page.locator('aside button');

  // Settle on one fact, then jump to a much later one.
  await cards.nth(0).click();
  await page.waitForTimeout(1200);
  const first = await scroller.evaluate((el) => Math.round(el.scrollTop));

  await cards.nth(4).click();

  // Sample while it animates. The URL mirrors the selection one render late,
  // so an unguarded restore effect reads the STALE fact and drags the viewer
  // back — which shows up as the scroll returning to where it started.
  const samples: number[] = [];
  for (let i = 0; i < 14; i++) {
    await page.waitForTimeout(110);
    samples.push(await scroller.evaluate((el) => Math.round(el.scrollTop)));
  }

  const settled = samples.at(-1)!;
  expect(Math.abs(settled - first)).toBeGreaterThan(8);

  // And it must not reverse direction on the way there.
  let reversals = 0;
  for (let i = 2; i < samples.length; i++) {
    const before = samples[i - 1]! - samples[i - 2]!;
    const after = samples[i]! - samples[i - 1]!;
    if (Math.abs(before) > 3 && Math.abs(after) > 3 && Math.sign(before) !== Math.sign(after)) {
      reversals += 1;
    }
  }
  expect(reversals).toBe(0);

  // The card the user clicked is the one that stayed selected.
  await expect(cards.nth(4)).toHaveAttribute('aria-current', 'true');
});
