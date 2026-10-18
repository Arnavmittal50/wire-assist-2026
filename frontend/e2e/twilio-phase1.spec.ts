import { expect, test } from '@playwright/test';

test('call control shows setup failure without pretending the phone rang', async ({ page }) => {
  // Always intercept tokens: this browser test must never place a real call.
  await page.route('**/api/twilio/token', route => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ detail: 'Twilio setup incomplete. Set TWILIO_TWIML_APP_SID in backend/.env.' }) }));
  await page.goto('/');
  await page.getByRole('button', { name: 'Call Member', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('TWILIO_TWIML_APP_SID');
  await expect(page.getByLabel('Call duration')).toHaveText('00:00');
  await expect(page.getByRole('button', { name: 'End Call' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Call Member', exact: true })).toBeEnabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.locator('.call-toolbar').screenshot({ path: 'test-results/twilio-phase1-mobile.png' });
});
