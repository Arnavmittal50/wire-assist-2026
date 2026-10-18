import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import App from '../App';
import { makeSession, makeSummary, demoInsight } from '../test/fixtures';
import { EmployeeActions } from './EmployeeActions';
import { SessionSummaryCard } from './SessionSummaryCard';

class QuietSocket { static OPEN = 1; readyState = 1; bufferedAmount = 0; close() {} send() {} }
afterEach(() => vi.unstubAllGlobals());

describe('review lifecycle', () => {
  it.each(['active', 'ended'] as const)('can switch from a %s review to member 57 while preserving the previous review', async status => {
    vi.stubGlobal('WebSocket', QuietSocket);
    let saved = makeSession({ status, retain_transcript: true, segment_count: 1,
      segments: [{ segment_id: 'saved-turn', sequence: 0, speaker: 'member', text: 'Keep this conversation.',
        timestamp: '2026-09-27T00:00:00Z', source: 'manual', finalized: true }] });
    const request = vi.fn(async (url: string, _init: RequestInit) => {
      let result: unknown = saved;
      if (url.endsWith('/health')) result = { status: 'ok', analysis_provider: 'demo', transcription_provider: 'manual' };
      else if (url.endsWith('/members')) result = ['M1002', 'M1057', 'M1003'].map(member_id => ({ member_id, label: `Member ${member_id}`, data_source: 'test' }));
      else if (url.includes('/events?')) result = [];
      else if (url.endsWith('/pre-analysis')) result = { provider: 'demo', model: null, event_count: 0, summary: [], insights: [], notice: 'Test' };
      else if (url.endsWith('/end')) { saved = { ...saved, status: 'ended' }; result = saved; }
      else if (url.endsWith('/summary')) result = makeSummary(saved);
      return { ok: true, status: 200, json: async () => result };
    });
    vi.stubGlobal('fetch', request);
    localStorage.setItem('wire-assist-session', saved.session_id);
    render(<App />);
    await screen.findByText('Keep this conversation.');
    expect(screen.getByLabelText('SYNTHETIC MEMBER')).toBeEnabled();
    fireEvent.change(screen.getByLabelText('SYNTHETIC MEMBER'), { target: { value: 'M1057' } });
    await waitFor(() => expect(screen.getByLabelText('SYNTHETIC MEMBER')).toHaveValue('M1057'));
    expect(saved.status).toBe('ended');
    expect(saved.segments[0].text).toBe('Keep this conversation.');
    expect(request.mock.calls.filter(([url]) => url.endsWith('/end'))).toHaveLength(status === 'active' ? 1 : 0);
    expect(request.mock.calls.some(([, init]) => init.method === 'DELETE')).toBe(false);
    expect(localStorage.getItem('wire-assist-session')).toBeNull();
    expect(screen.getByRole('region', { name: 'Wire request' })).toHaveTextContent('$51,586.52');
    await waitFor(() => expect(request).toHaveBeenCalledWith(expect.stringContaining('/members/M1057/events?as_of=2026-10-19T16%3A41'), expect.anything()));
    fireEvent.change(screen.getByLabelText('SYNTHETIC MEMBER'), { target: { value: 'M1003' } });
    await waitFor(() => expect(screen.getByLabelText('SYNTHETIC MEMBER')).toHaveValue('M1003'));
  });

  it('keeps the current review when saving it before a member switch fails', async () => {
    vi.stubGlobal('WebSocket', QuietSocket);
    const saved = makeSession();
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      const result = url.endsWith('/health') ? { status: 'ok', analysis_provider: 'demo', transcription_provider: 'manual' }
        : url.endsWith('/members') ? ['M1002', 'M1057'].map(member_id => ({ member_id, label: `Member ${member_id}`, data_source: 'test' }))
        : url.includes('/events?') ? [] : url.endsWith('/end') ? { detail: 'Could not save the review.' } : saved;
      return { ok: !url.endsWith('/end'), status: url.endsWith('/end') ? 500 : 200, json: async () => result };
    }));
    localStorage.setItem('wire-assist-session', saved.session_id);
    render(<App />);
    await screen.findByRole('button', { name: 'End review' });
    fireEvent.change(screen.getByLabelText('SYNTHETIC MEMBER'), { target: { value: 'M1057' } });
    expect(await screen.findByText('Could not save the review.')).toBeVisible();
    expect(screen.getByLabelText('SYNTHETIC MEMBER')).toHaveValue('M1002');
    expect(localStorage.getItem('wire-assist-session')).toBe(saved.session_id);
  });

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
    localStorage.setItem('wire-assist-session', session.session_id);
    render(<App />);
    await screen.findByRole('option', { name: 'Member M1002' });
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
    await screen.findByRole('heading', { name: 'Ready for a conversation' });
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
