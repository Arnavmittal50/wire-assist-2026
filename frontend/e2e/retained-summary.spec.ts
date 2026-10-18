import { expect, test } from '@playwright/test';

test('invoice scam reference and complete conversation remain in the summary after reload', async ({ page, request }) => {
  const backend = process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8000';
  const headers = { Authorization: `Bearer ${process.env.E2E_DEV_AUTH_TOKEN || 'wire-assist-local-dev'}` };
  const texts = [
    'My roofing contractor emailed an amended invoice after the roof repair.',
    'The email replaced the original bank account with a new beneficiary for a wire.',
    'I have not called the contractor using the number in my signed contract.',
  ];
  const response = await request.post(`${backend}/sessions`, { headers, data: { member_id: 'M1002', consent: true, mode: 'demo' } });
  expect(response.ok()).toBe(true);
  const session = await response.json();
  expect(session.retain_transcript).toBe(true);
  try {
    await page.goto('/');
    await page.evaluate(id => localStorage.setItem('wire-assist-session', id), session.session_id);
    await page.reload();
    await expect(page.getByText('Connected', { exact: true })).toBeVisible();
    await page.getByLabel('Transcript speaker').selectOption('member');
    for (const text of texts) {
      await page.getByLabel('Manual transcript').fill(text);
      await page.getByRole('button', { name: 'Add turn' }).click();
      await expect(page.getByRole('log').getByText(text, { exact: true })).toBeVisible();
    }
    await expect(page.getByText(/SYNTHETIC TEST SYNTHETIC-INVOICE-001/)).toBeVisible();
    await page.getByRole('button', { name: 'End review', exact: true }).click();
    await expect(page.getByText('Review complete.', { exact: true })).toBeVisible();
    const summary = await (await request.get(`${backend}/sessions/${session.session_id}/summary`, { headers })).json();
    expect(summary.transcript.map((turn: { text: string }) => turn.text)).toEqual(texts);
    expect(summary.insights.length).toBeGreaterThan(0);
    await page.reload();
    const saved = page.getByRole('region', { name: 'Saved conversation' });
    for (const text of texts) await expect(saved.getByText(text, { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'All saved insights' }).locator('.summary-insight')).toHaveCount(summary.insights.length);
    await expect(page.locator('.summary-card').getByText(/SYNTHETIC TEST SYNTHETIC-INVOICE-001/)).toBeVisible();
  } finally {
    await request.delete(`${backend}/sessions/${session.session_id}`, { headers });
  }
});
