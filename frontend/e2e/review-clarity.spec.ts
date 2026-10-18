import { expect, test } from '@playwright/test';

test('history is readable and a speaker correction removes the mistaken signal', async ({ page, request }) => {
  const backend = process.env.E2E_BACKEND_URL || 'http://localhost:8000';
  const headers = { Authorization: `Bearer ${process.env.E2E_DEV_AUTH_TOKEN || 'wire-assist-local-dev'}` };
  let id: string | undefined;
  try {
    await page.goto('/');
    await page.getByRole('button', { name: 'M1002 · Sep 26' }).click();
    await page.getByLabel('Analysis mode').selectOption('demo');
    await page.getByLabel(/The member consents/).check();
    const created = page.waitForResponse(response => response.url().endsWith('/sessions') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start review' }).click();
    id = (await (await created).json()).session_id;
    await expect(page.getByRole('heading', { name: 'What we know' })).toBeVisible();
    await expect(page.getByText('ASK FIRST')).toBeVisible();
    await expect(page.getByText('How did you confirm the recipient details for this payment?', { exact: true }).first()).toBeVisible();
    await page.getByLabel('Transcript speaker').selectOption('member');
    await page.getByLabel('Manual transcript').fill('He said I have to send the money in the next 30 minutes or something terrible will happen.');
    await page.getByRole('button', { name: 'Add turn' }).click();
    await expect(page.getByText('Reviewing the latest conversation…')).not.toBeVisible();
    await expect(page.getByLabel('Speaker for turn 1')).toHaveValue('member');
    await page.getByLabel('Speaker for turn 1').selectOption('teller');
    await expect.poll(async () => {
      const state = await (await request.get(`${backend}/sessions/${id}`, { headers })).json();
      return !state.analysis_pending && state.segments[0]?.speaker === 'teller' &&
        !state.attention.signals.some((signal: { code: string; status: string }) => signal.code === 'TIME_PRESSURE' && signal.status === 'active');
    }).toBe(true);
    await page.locator('.initial-review').screenshot({ path: 'test-results/initial-review-desktop.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole('heading', { name: 'What we know' })).toBeVisible();
    await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.locator('.initial-review').screenshot({ path: 'test-results/initial-review-mobile.png' });
  } finally {
    if (id) await request.delete(`${backend}/sessions/${id}`, { headers });
  }
});
