import { expect, test } from '@playwright/test';

test('pre-call context and explicit AI consent prepare a review without dialing during tests', async ({ page, request }) => {
  let sessionId: string | undefined;
  await page.route('**/pre-analysis', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({
    provider: 'groq', model: 'mocked-model', notice: 'Mocked browser-test analysis', event_count: 1, insights: [],
    summary: [{ text: 'A new recipient appears in the supplied history.', evidence: [{ source_type: 'event', source_id: 'fixture', quote: 'new_recipient' }] }],
  }) }));
  // This test must never acquire a real Twilio token or make a phone call.
  await page.route('**/api/twilio/token', route => route.fulfill({ status: 503, contentType: 'application/json',
    body: JSON.stringify({ detail: 'Test interception: no phone call was made.' }) }));
  try {
    await page.goto('/');
    await expect(page.getByText('A new recipient appears in the supplied history.')).toBeVisible();
    const consent = page.getByLabel(/Enable AI call assistance/);
    await expect(consent).not.toBeChecked();
    await consent.check();
    const prepared = page.waitForResponse(response => response.url().endsWith('/api/twilio/prepare'));
    const created = page.waitForResponse(response => response.url().endsWith('/sessions') && response.request().method() === 'POST');
    await page.getByRole('button', { name: 'Call Member', exact: true }).click();
    sessionId = (await (await created).json()).session_id;
    const preparation = await prepared;
    expect(preparation.status()).toBe(200);
    expect(preparation.request().postDataJSON()).toEqual({ session_id: sessionId, participants_consent: true });
    await expect(page.getByText('Test interception: no phone call was made.', { exact: false })).toBeVisible();
    await expect(page.getByLabel('Call duration')).toHaveText('00:00');
    await expect(page.getByRole('button', { name: 'End Call' })).toHaveCount(0);
  } finally {
    if (sessionId) await request.delete(`${process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8000'}/sessions/${sessionId}`,
      { headers: { Authorization: `Bearer ${process.env.E2E_DEV_AUTH_TOKEN || 'wire-assist-local-dev'}` } });
  }
});
