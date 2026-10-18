import { expect, test } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

const backend = process.env.E2E_BACKEND_URL || 'http://localhost:8000';
const headers = { Authorization: `Bearer ${process.env.E2E_DEV_AUTH_TOKEN || 'wire-assist-local-dev'}` };
const created = new Set<string>();
async function start(page: Page) {
  await page.getByLabel(/The member consents/).check();
  const response = page.waitForResponse(response => response.url().endsWith('/sessions') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Start review' }).click();
  const session = await (await response).json(); created.add(session.session_id);
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  return session.session_id as string;
}
async function addTurn(page: Page, text: string, speaker = 'member') {
  await page.getByLabel('Transcript speaker').selectOption(speaker);
  await page.getByLabel('Manual transcript').fill(text);
  await page.getByRole('button', { name: 'Add turn' }).click();
  await expect(page.getByRole('log').getByText(text, { exact: true })).toBeVisible();
  await expect(page.getByText('Reviewing the latest conversation…')).not.toBeVisible();
}
async function cleanup(request: APIRequestContext) {
  for (const id of created) await request.delete(`${backend}/sessions/${id}`, { headers });
  created.clear();
}
test.afterEach(async ({ request }) => cleanup(request));

test('complete manual review with microphone lifecycle, evidence, decisions, summary, reload and deletion', async ({ page, request }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()); });
  await page.goto('/');
  await expect(page.getByRole('button', { name: 'Start review' })).toBeDisabled();
  await expect(page.getByText('$87.95', { exact: true }).first()).toBeVisible();
  await page.getByLabel('SYNTHETIC MEMBER').selectOption('M1001');
  await expect(page.getByLabel('SYNTHETIC MEMBER')).toHaveValue('M1001');
  await page.getByRole('button', { name: 'M1002 · Sep 26' }).click();
  const id = await start(page);
  await expect(page.getByText('Starting context', { exact: true })).toBeVisible();
  const initial = await (await request.get(`${backend}/sessions/${id}`, { headers })).json();
  expect(initial.initial_analysis.provider).toBe('demo');
  expect(initial.initial_analysis.event_count).toBeGreaterThan(30);
  await page.getByRole('button', { name: 'Start microphone' }).click();
  await expect(page.getByText('Local microphone preview', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Pause microphone' }).click();
  await expect(page.getByText('Microphone paused', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Resume', exact: true }).click();
  await expect(page.getByText('Local microphone preview', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Stop microphone' }).click();
  await expect(page.getByText('Microphone stopped', { exact: true })).toBeVisible();
  await addTurn(page, 'Someone I met online said I must send the wire today to protect my investment, and asked me not to tell anyone.');
  await expect(page.getByText('Deterministic demo analysis', { exact: true })).toBeVisible();
  await expect(page.locator('.insight-card .question-box')).toBeVisible();
  await addTurn(page, 'Can you tell me how you verified the recipient independently?', 'teller');
  await page.locator('.insight-card').getByRole('button', { name: 'Acknowledge', exact: true }).click();
  await expect(page.locator('.insight-card').getByRole('button', { name: 'Acknowledged' })).toBeDisabled();
  await page.screenshot({ path: 'test-results/dashboard.png', fullPage: true });
  await page.locator('.insight-card').getByRole('button', { name: 'Dismiss', exact: true }).click();
  await page.getByLabel('Reason or action note').fill('Documented for the demonstration; the teller will verify independently.');
  await page.getByRole('button', { name: 'Record decision' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  const afterDismiss = await (await request.get(`${backend}/sessions/${id}`, { headers })).json();
  expect(afterDismiss.actions.some((action: { action_type: string }) => action.action_type === 'DISMISS')).toBe(true);
  await page.getByRole('button', { name: 'Offer verification', exact: true }).click();
  await page.getByLabel('Reason or action note').fill('Offered a callback through an independently obtained number.');
  await page.getByRole('button', { name: 'Record decision' }).click();
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await page.getByRole('button', { name: 'Mark reviewed as legitimate', exact: true }).click();
  await page.getByLabel('Independent verification and review rationale').fill('Synthetic test: independently verified the invoice and recipient via a previously known number; member chose to proceed.');
  await page.getByRole('button', { name: 'Record decision' }).click();
  await expect(page.getByRole('button', { name: 'Reviewed as legitimate' })).toBeDisabled();
  await page.getByRole('button', { name: 'End review', exact: true }).click();
  await expect(page.getByText('Review complete.', { exact: true })).toBeVisible();
  await expect(page.getByText('Not retained after this review')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Review complete.', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Conversation text was not retained.' })).toBeVisible();
  await page.getByRole('button', { name: 'Delete session data' }).click();
  await page.getByRole('button', { name: 'Permanently delete session' }).click();
  await expect(page.getByRole('button', { name: 'Start review' })).toBeVisible();
  expect((await request.get(`${backend}/sessions/${id}`, { headers })).status()).toBe(404);
  created.delete(id);
  expect(errors).toEqual([]);
});

test('M1057 review preset works at its as-of date and the dashboard remains usable on mobile', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'M1057 · Oct 19' }).click();
  await expect(page.getByLabel('SYNTHETIC MEMBER')).toHaveValue('M1057');
  await expect(page.locator('.payment-heading').getByText('$51,586.52', { exact: false })).toBeVisible();
  const id = await start(page);
  const session = await (await request.get(`${backend}/sessions/${id}`, { headers })).json();
  expect(session.as_of).toContain('2026-10-19T16:41:00');
  expect(session.payment.recipient_id).toBe('R1057-10');
  await addTurn(page, 'This is my established contractor. I verified their invoice and called the number I already had. Nobody is pressuring me.');
  const analyzed = await (await request.get(`${backend}/sessions/${id}`, { headers })).json();
  expect(analyzed.analyzed_sentences).toHaveLength(3);
  await expect(page.getByText('Deterministic demo analysis', { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: 'test-results/mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'End review', exact: true }).click();
  await expect(page.getByText('Review complete.', { exact: true })).toBeVisible();
});
