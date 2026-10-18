import { expect } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';

// Keep exercising backend review support after its manual start panel was removed.
export async function restoreDemoReview(page: Page, request: APIRequestContext) {
  const response = await request.post(`${process.env.E2E_BACKEND_URL || 'http://127.0.0.1:8000'}/sessions`, {
    headers: { Authorization: `Bearer ${process.env.E2E_DEV_AUTH_TOKEN || 'wire-assist-local-dev'}` },
    data: {
      member_id: await page.getByLabel('SYNTHETIC MEMBER').inputValue(),
      as_of: await page.getByLabel('As-of timestamp (UTC)').inputValue(),
      consent: true, retain_transcript: false, mode: 'demo',
      payment: {
        amount: Number(await page.getByLabel('Payment amount').inputValue()),
        rail: await page.getByLabel('Payment rail').inputValue(),
        recipient_id: await page.getByLabel('Recipient identifier').inputValue(),
        purpose: await page.getByLabel("Member's stated purpose").inputValue(),
      },
    },
  });
  expect(response.ok()).toBe(true);
  const session = await response.json();
  await page.evaluate(id => localStorage.setItem('wire-assist-session', id), session.session_id);
  await page.reload();
  await expect(page.getByText('Connected', { exact: true })).toBeVisible();
  return session.session_id as string;
}
