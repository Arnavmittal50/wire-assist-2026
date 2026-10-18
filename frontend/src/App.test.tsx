import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { Transcript } from './components/Transcript';
import { InsightCard } from './components/InsightCard';
import type { Insight, Segment } from './types/api';

const members = [{ member_id: 'M1002', label: 'Member M1002', data_source: 'huskyhack_csv' }, { member_id: 'M1057', label: 'Member M1057', data_source: 'huskyhack_csv' }];
const event = { event_id: 'E1', member_id: 'M1002', timestamp: '2026-09-08T12:00:00Z', event_type: 'P2P', amount: 87.95, channel: 'P2P', recipient_id: 'R1', description: 'Recorded payment' };
beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200, json: async () => url.endsWith('/health') ? { status: 'ok', analysis_provider: 'demo', transcription_provider: 'manual', data_source: 'huskyhack_csv' } : url.endsWith('/members') ? members : [event] })));
});
afterEach(() => vi.unstubAllGlobals());

describe('workspace', () => {
  it('does not overwrite a chosen mode when backend discovery finishes later', async () => {
    let resolveHealth!: (value: unknown) => void;
    const health = new Promise(resolve => { resolveHealth = resolve; });
    vi.stubGlobal('fetch', vi.fn(async (url: string) => ({ ok: true, status: 200,
      json: async () => url.endsWith('/health') ? health : url.endsWith('/members') ? members : [event] })));
    render(<App />);
    fireEvent.change(screen.getByLabelText('Analysis mode'), { target: { value: 'demo' } });
    resolveHealth({ status: 'ok', analysis_provider: 'gemini', transcription_provider: 'gemini_live', data_source: 'huskyhack_csv' });
    await screen.findByRole('option', { name: 'Member M1002' });
    expect(screen.getByLabelText('Analysis mode')).toHaveValue('demo');
  });
  it('loads synthetic members and displays real API history', async () => {
    render(<App />);
    await screen.findByRole('option', { name: 'Member M1002' });
    expect(await screen.findByText('$87.95')).toBeVisible();
    expect(screen.getByRole('button', { name: /Start review/ })).toBeDisabled();
    expect(screen.getByText('DEMO MODE')).toBeVisible();
  });
  it('changes the selected member and requests their as-of history', async () => {
    render(<App />);
    await screen.findByRole('option', { name: 'Member M1057' });
    fireEvent.change(screen.getByLabelText('SYNTHETIC MEMBER'), { target: { value: 'M1057' } });
    await waitFor(() => expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/members/M1057/events?as_of='), expect.anything()));
  });
  it('enables session creation only after explicit consent', async () => {
    render(<App />);
    await screen.findByRole('option', { name: 'Member M1002' });
    fireEvent.click(screen.getByLabelText(/The member consents/));
    expect(screen.getByRole('button', { name: /Start review/ })).toBeEnabled();
  });
});

describe('conversation rendering', () => {
  const segment: Segment = { segment_id: 's1', sequence: 1, text: 'I would like to review this transfer.', speaker: 'member', timestamp: '2026-09-26T12:00:00Z', source: 'manual', finalized: true };
  it('deduplicates transcript segments and labels provisional text', () => {
    render(<Transcript segments={[segment, segment]} partial="They mentioned a deadline" />);
    expect(screen.getAllByText(segment.text)).toHaveLength(1);
    expect(screen.getByText('Member')).toBeVisible();
    expect(screen.getByText('Provisional · not scored')).toBeVisible();
  });
  it('renders a single evidence-backed recommendation with explicit provider labeling', () => {
    const insight: Insight = { insight_id: 'i1', title: 'Verify the recipient relationship', observation: 'The relationship has not been confirmed.', evidence: [{ source_type: 'segment', source_id: 's1', quote: segment.text }], source_segment_ids: ['s1'], source_event_ids: [], suggested_question: 'How do you know the recipient?', recommended_action: 'ASK_FOLLOW_UP', priority: 'medium', confidence_in_observation: .8, requires_human_review: true, signal_codes: [], status: 'active', provider: 'demo' };
    const dismiss = vi.fn();
    render(<InsightCard insight={insight} pending={false} onDismiss={dismiss} />);
    expect(screen.getByText('DEMO')).toBeVisible();
    expect(screen.getByText('“How do you know the recipient?”')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(dismiss).toHaveBeenCalledOnce();
  });
});


it('orders simultaneous call transcripts by media time and labels them from verified tracks', () => {
  const first: Segment = { timestamp: '2026-09-27T00:00:00Z', finalized: true, segment_id: 'teller-early', sequence: 2, text: 'Earlier teller question.', speaker: 'teller',
    source: 'gemini', speaker_source: 'twilio_track', diarization_status: 'CONFIRMED', call_sid: 'CA-test', start_time: 1, end_time: 2 };
  const second: Segment = { ...first, segment_id: 'member-later', sequence: 1, text: 'Later member response.', speaker: 'member', start_time: 3, end_time: 4 };
  render(<Transcript segments={[second, first, first]} partial="" />);
  const turns = screen.getByRole('log').querySelectorAll('article');
  expect(turns).toHaveLength(2);
  expect(turns[0]).toHaveTextContent('Teller');
  expect(turns[0]).toHaveTextContent('Earlier teller question.');
  expect(turns[1]).toHaveTextContent('Member');
  expect(screen.getAllByText('Call audio: verified track')).toHaveLength(2);
});
