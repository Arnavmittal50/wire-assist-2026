import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { makeSession, makeSummary, demoInsight } from '../test/fixtures';
import { EmployeeActions } from './EmployeeActions';
import { SessionSummaryCard } from './SessionSummaryCard';

class QuietSocket { static OPEN = 1; readyState = 1; bufferedAmount = 0; close() {} send() {} }
afterEach(() => vi.unstubAllGlobals());

describe('review lifecycle', () => {
  it('records a reasoned legitimate review, ends the session, shows summary, and deletes it', async () => {
    vi.stubGlobal('WebSocket', QuietSocket);
    let session = makeSession({ insights: [{ ...demoInsight }] });
    let deleted = false;
    const actions: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string, init: RequestInit) => {
      let result: unknown;
      if (input.endsWith('/health')) result = { status: 'ok', analysis_provider: 'demo', transcription_provider: 'manual' };
      else if (input.endsWith('/members')) result = [{ member_id: 'M1002', label: 'Member M1002', data_source: 'huskyhack_csv' }];
      else if (input.includes('/events?')) result = [];
      else if (input.endsWith('/actions')) {
        const action = JSON.parse(String(init.body)); actions.push(action);
        session = { ...session, revision: session.revision + 1, review_outcome: action.action_type === 'MARK_LEGITIMATE' ? 'legitimate' : session.review_outcome, attention: { ...session.attention, score: 0 }, insights: [], actions: [...session.actions, { ...action, action_id: 'a1', timestamp: '2026-10-17T20:01:00Z', simulated: true }] }; result = session;
      } else if (input.endsWith('/end')) { session = { ...session, status: 'ended', revision: session.revision + 1, ended_at: '2026-10-17T20:02:00Z' }; result = session; }
      else if (input.endsWith('/summary')) result = makeSummary(session);
      else if (init.method === 'DELETE') { deleted = true; result = undefined; }
      else result = session;
      return { ok: true, status: result === undefined ? 204 : 200, json: async () => result };
    }));
    render(<App />);
    await screen.findByRole('option', { name: 'Member M1002' });
    fireEvent.click(screen.getByLabelText(/The member consents/)); fireEvent.click(screen.getByRole('button', { name: /Start review/ }));
    await screen.findByRole('button', { name: 'Mark reviewed as legitimate' });
    fireEvent.click(screen.getByRole('button', { name: 'Mark reviewed as legitimate' }));
    expect(screen.getByRole('button', { name: 'Record decision' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Independent verification and review rationale'), { target: { value: 'Verified invoice through the independently known contractor number.' } });
    fireEvent.click(screen.getByRole('button', { name: 'Record decision' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reviewed as legitimate' })).toBeDisabled());
    expect(actions).toEqual([expect.objectContaining({ action_type: 'MARK_LEGITIMATE', note: expect.stringContaining('Verified invoice') })]);
    expect(localStorage.getItem('wire-assist-session')).toBe('session-1');
    fireEvent.click(screen.getByRole('button', { name: 'End review' }));
    expect(await screen.findByText('Review complete.')).toBeVisible();
    expect(screen.getByText('Not retained after this review')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Delete session data' }));
    fireEvent.click(screen.getByRole('button', { name: 'Permanently delete session' }));
    await screen.findByRole('button', { name: /Start review/ });
    expect(deleted).toBe(true); expect(localStorage.getItem('wire-assist-session')).toBeNull();
  });
  it('disables simulated hold review for payment rails that cannot be held', () => {
    const session = makeSession(); session.payment.rail = 'P2P';
    render(<EmployeeActions session={session} busy={false} onAction={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Simulate hold review' })).toBeDisabled();
    expect(screen.getByText(/No action here moves or controls funds/)).toBeVisible();
  });
  it('requires a specific UI confirmation before deleting stored session data', () => {
    const onDelete = vi.fn();
    render(<SessionSummaryCard summary={makeSummary(makeSession({ status: 'ended' }))} busy={false} onNew={vi.fn()} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole('button', { name: 'Delete session data' }));
    expect(onDelete).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Permanently delete session' }));
    expect(onDelete).toHaveBeenCalledOnce();
  });
});
