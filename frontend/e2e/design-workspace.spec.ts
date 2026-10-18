import { expect, test } from '@playwright/test';
import { restoreDemoReview } from './helpers/review';

test('Figma workspace keeps its fonts, asset geometry and controls across responsive layouts', async ({ page, request }) => {
  const backend = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8000';
  const headers = { Authorization: `Bearer ${process.env.E2E_DEV_AUTH_TOKEN || 'wire-assist-local-dev'}` };
  let id: string | undefined;
  // No real phone call can be placed by this visual/interaction check.
  await page.route('**/api/twilio/token', route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Test only: calling disabled.' }) }));
  try {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'Start a member review' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Start review' })).toHaveCount(0);
    await page.evaluate(() => document.fonts.ready);
    expect(await page.evaluate(() => document.fonts.check('700 24px "IBM Plex Mono"'))).toBe(true);
    await expect(page.locator('.workspace-grid')).toHaveCSS('gap', '16px');
    await expect(page.locator('.guidance-heading h1')).toHaveCSS('font-size', '32px');
    const assets = await page.locator('img[src^="/assets/figma/"]').evaluateAll(images => images.map(element => {
      const image = element as HTMLImageElement;
      return { src: image.getAttribute('src'), loaded: image.complete && image.naturalWidth > 0, width: image.getBoundingClientRect().width, height: image.getBoundingClientRect().height };
    }));
    expect(assets).toEqual([
      { src: '/assets/figma/wire-assist-mark.svg', loaded: true, width: 36, height: 36 },
      { src: '/assets/figma/microphone.svg', loaded: true, width: 14, height: 18 },
    ]);
    await expect(page.locator('.member-panel .initial-review')).toBeVisible();
    const widths = await page.locator('.workspace-grid > .workspace-panel').evaluateAll(panels => panels.map(panel => panel.getBoundingClientRect().width));
    expect(widths[2]).toBeGreaterThan(widths[1]);
    expect(await page.locator('main > .session-controls').evaluate(element => element.previousElementSibling?.classList.contains('support-grid'))).toBe(true);
    id = await restoreDemoReview(page, request);
    await page.getByLabel('Transcript speaker').selectOption('member');
    await page.getByLabel('Manual transcript').fill('He told me I must send the wire in the next 30 minutes and not tell anyone.');
    await page.getByRole('button', { name: 'Add turn' }).click();
    await expect(page.getByText('Reviewing the latest conversation…')).not.toBeVisible();
    await expect(page.locator('.transcript-turn')).toHaveCount(1);
    await page.locator('.signal-item').first().locator('summary').click();
    await page.locator('.signal-item').first().getByRole('button', { name: 'Resolve' }).click();
    await page.getByLabel('Reason or action note').fill('Synthetic browser test: reviewed the source evidence with the member.');
    await page.getByRole('button', { name: 'Record decision' }).click();
    await expect(page.getByRole('dialog')).not.toBeVisible();
    const state = await (await request.get(`${backend}/sessions/${id}`, { headers })).json();
    expect(state.actions.some((action: { action_type: string }) => action.action_type === 'RESOLVE_SIGNAL')).toBe(true);
    for (const width of [1440, 1024, 768, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await expect(page.getByRole('button', { name: 'End review', exact: true })).toBeEnabled();
      await expect(page.getByLabel('Manual transcript')).toBeEnabled();
      if ([1440, 390].includes(width)) await page.screenshot({ path: `test-results/figma-workspace-${width}.png`, fullPage: true });
    }
  } finally {
    if (id) await request.delete(`${backend}/sessions/${id}`, { headers });
  }
});
