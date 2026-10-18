import { render, screen, fireEvent } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { InsightCard } from './InsightCard';
import { ConversationControls } from './ConversationControls';
import { Transcript } from './Transcript';
import { ProviderNotice } from './ProviderNotice';
import { demoInsight } from '../test/fixtures';

it('shows pending review without falsely claiming provider failure or readiness', () => {
  render(<><ProviderNotice provider="unavailable" pending notice="Preparing the initial member-history review." /><InsightCard pending /></>);
  expect(screen.getByText('Evidence review in progress')).toBeVisible();
  expect(screen.queryByText('Analysis unavailable')).not.toBeInTheDocument();
  expect(screen.getByText('REVIEWING')).toBeVisible();
  expect(screen.queryByText('READY')).not.toBeInTheDocument();
  expect(screen.getByText('Preparing guidance')).toBeVisible();
});

it('shows a failed empty review as unavailable instead of ready', () => {
  const retry = vi.fn();
  render(<InsightCard pending={false} unavailable onRetry={retry} />);
  expect(screen.getByText('UNAVAILABLE')).toBeVisible();
  expect(screen.getByText('Guidance unavailable')).toBeVisible();
  expect(screen.queryByText('READY')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Retry guidance' }));
  expect(retry).toHaveBeenCalledOnce();
});

it('provides a neutral question while waiting and distinguishes a completed empty review', () => {
  const view = render(<InsightCard pending={false} waitingForSpeaker />);
  expect(screen.getByText('IDENTIFYING')).toBeVisible();
  expect(screen.getByText('What led you to make this payment, and how did you confirm the recipient?')).toBeVisible();
  expect(screen.queryByText('REVIEWING')).not.toBeInTheDocument();
  view.rerender(<InsightCard pending={false} reviewed />);
  expect(screen.getByText('No additional concerns established')).toBeVisible();
  expect(screen.queryByText(/Start a review and add/)).not.toBeInTheDocument();
});

it('updates the same transcript bubble by revision, ignoring older duplicate labels', () => {
  const segment = { segment_id: 'same', sequence: 0, text: 'My brother called.', speaker: 'unknown' as const, timestamp: '', source: 'gemini' as const, finalized: true, revision: 0, diarization_status: 'PENDING' as const };
  const view = render(<Transcript partial="" segments={[segment]} />);
  expect(screen.getByText('Identifying speaker...')).toBeVisible();
  view.rerender(<Transcript partial="" segments={[{ ...segment, speaker: 'member', revision: 1, diarization_status: 'CONFIRMED' }, segment]} />);
  expect(screen.getAllByText('My brother called.')).toHaveLength(1);
  expect(screen.getByText('Member')).toBeVisible();
  expect(screen.queryByText('Identifying speaker...')).not.toBeInTheDocument();
});

it('shows possible scenarios separately from observed identity evidence', () => {
  render(<InsightCard pending={false} insight={{ ...demoInsight, signals: [
    { signal_type: 'POSSIBLE_IMPERSONATION', status: 'POSSIBLE', source_type: 'segment', source_id: 's1', evidence_quote: 'It sounded like my brother.', explanation: 'Impersonation is possible, not confirmed.', suggested_question: 'Can we call a known number?' },
    { signal_type: 'IDENTITY_VERIFICATION_NEEDED', status: 'OBSERVED', source_type: 'segment', source_id: 's2', evidence_quote: 'He also sent me a selfie.', explanation: 'A selfie is not independent identity verification.', suggested_question: 'How did you verify his identity?' },
  ] }} />);
  expect(screen.getByText('POSSIBLE')).toBeVisible();
  expect(screen.getByText('OBSERVED')).toBeVisible();
  expect(screen.getByText('Can we call a known number?')).toBeVisible();
  expect(screen.getByText('He also sent me a selfie.')).toBeVisible();
});

it('offers auto roles while keeping manual overrides available', () => {
  render(<ConversationControls status="stopped" live speaker="auto" setSpeaker={vi.fn()} onStart={vi.fn()} onPause={vi.fn()} onStop={vi.fn()} onSubmit={vi.fn()} disabled={false} busy={false} providerReady />);
  expect(screen.getByLabelText('Transcript speaker')).toHaveValue('auto');
  expect(screen.getByText('Audio identifies voices; first voice is Teller')).toBeVisible();
  expect(screen.getByRole('option', { name: 'Member' })).toBeEnabled();
  expect(screen.getByRole('option', { name: 'Teller' })).toBeEnabled();
});

it('does not show a pending auto assignment as a confirmed teller', () => {
  render(<Transcript partial="" segments={[{ segment_id: 's1', sequence: 0, text: 'Hello.', speaker: 'auto', timestamp: '', source: 'gemini', finalized: true }]} />);
  expect(screen.getByText('Identifying speaker...')).toBeVisible();
  expect(screen.queryByText('Teller')).not.toBeInTheDocument();
});

it('lets the teller correct an automatic speaker label', () => {
  const correct = vi.fn();
  render(<Transcript partial="" onCorrectSpeaker={correct} segments={[{ segment_id: 's1', sequence: 0, text: 'Hello.', speaker: 'member', speaker_source: 'automatic', timestamp: '', source: 'gemini', finalized: true }]} />);
  fireEvent.change(screen.getByLabelText('Speaker for turn 1'), { target: { value: 'teller' } });
  expect(correct).toHaveBeenCalledWith('s1', 'teller');
});
