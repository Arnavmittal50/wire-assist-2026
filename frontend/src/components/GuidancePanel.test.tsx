import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { GuidancePanel } from './GuidancePanel';
import { SessionControls } from './SessionControls';
import { demoInsight, makeSession } from '../test/fixtures';

it('preserves reported scam references alongside the new guidance and session controls', () => {
  const session = makeSession({ scam_matches: [{ report_id: 'reference-1', scam_type: 'Family/Friend Emergency',
    persona: '', payment_methods: 'Wire', dollars_lost: null, summary: 'A caller posed as a relative and requested a wire.',
    similarity: 0.4, reasons: ['Same payment type (Wire)'] }] });
  render(<GuidancePanel session={session} busy={false} onAction={vi.fn()} onAcknowledge={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.getByText('Similar reported scams')).toBeVisible();
  expect(screen.getByText(session.scam_matches![0].summary)).toBeVisible();
  expect(screen.queryByRole('button', { name: 'End review' })).not.toBeInTheDocument();
});

it('navigates existing guidance without allowing actions on a superseded recommendation', () => {
  const previous = { ...demoInsight, insight_id: 'old', title: 'Earlier request', status: 'superseded' as const };
  const current = { ...demoInsight, insight_id: 'current', title: 'Verify the current request' };
  const onAcknowledge = vi.fn();
  const view = render(<GuidancePanel session={makeSession({ insights: [previous, current] })} primary={current} busy={false}
    onAction={vi.fn()} onAcknowledge={onAcknowledge} onRetry={vi.fn()} />);
  expect(screen.getByRole('heading', { name: current.title })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Next guidance' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Previous guidance' }));
  expect(screen.getByRole('heading', { name: previous.title })).toBeVisible();
  expect(screen.getByRole('button', { name: 'Acknowledge' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Dismiss' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Next guidance' }));
  fireEvent.click(screen.getByRole('button', { name: 'Acknowledge' }));
  expect(onAcknowledge).toHaveBeenCalledWith(current);
  const latest = { ...current, insight_id: 'latest', title: 'A new member statement was reviewed' };
  view.rerender(<GuidancePanel session={makeSession({ insights: [previous, current, latest] })} primary={latest} busy={false}
    onAction={vi.fn()} onAcknowledge={onAcknowledge} onRetry={vi.fn()} />);
  expect(screen.getByRole('heading', { name: latest.title })).toBeVisible();
});

it('allows retrying an unavailable review with no recommendation and keeps decisions disabled after ending', () => {
  const retry = vi.fn();
  const props = { busy: false, onAction: vi.fn(), onAcknowledge: vi.fn(), onRetry: retry, onEnd: vi.fn() };
  const session = makeSession({ analysis_provider: 'unavailable' });
  const view = render(<><GuidancePanel {...props} session={session} /><SessionControls {...props} session={session} /></>);
  fireEvent.click(screen.getByRole('button', { name: 'Retry guidance' }));
  expect(retry).toHaveBeenCalledOnce();
  const ended = { ...session, status: 'ended' as const };
  view.rerender(<><GuidancePanel {...props} session={ended} /><SessionControls {...props} session={ended} /></>);
  expect(screen.getByRole('button', { name: 'Retry guidance' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Mark reviewed as legitimate' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Simulate escalation' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'End review' })).toBeDisabled();
});

it('returns from history to the latest guidance when new member evidence is reviewed', () => {
  const previous = { ...demoInsight, insight_id: 'old', title: 'Routing details', status: 'superseded' as const };
  const current = { ...demoInsight, insight_id: 'current', title: 'Verify recipient and investment' };
  const props = { busy: false, onAction: vi.fn(), onAcknowledge: vi.fn(), onRetry: vi.fn() };
  const view = render(<GuidancePanel {...props} session={makeSession({ insights: [previous, current] })} primary={current} />);
  fireEvent.click(screen.getByRole('button', { name: 'Previous guidance' }));
  expect(screen.getByRole('heading', { name: previous.title })).toBeVisible();
  const updated = { ...current, title: 'Independent verification explained', suggested_question: 'Anything else to clarify?' };
  view.rerender(<GuidancePanel {...props} session={makeSession({ insights: [previous, updated], analyzed_sentences: ['s1:0'] })} primary={updated} />);
  expect(screen.getByRole('heading', { name: updated.title })).toBeVisible();
});

it('shows Seems safe after a supported continue-review result, without another question', () => {
  const safe = { ...demoInsight, title: 'Recipient details checked', recommended_action: 'CONTINUE_REVIEW' as const,
    signal_codes: [], signals: [], suggested_question: 'Anything else you would like to ask?',
    verification_checks: [
      { check_type: 'RECIPIENT_IDENTITY' as const, status: 'CONFIRMED' as const, evidence: demoInsight.evidence },
      { check_type: 'PAYMENT_INSTRUCTIONS' as const, status: 'CONFIRMED' as const, evidence: demoInsight.evidence }] };
  const props = { busy: false, onAction: vi.fn(), onAcknowledge: vi.fn(), onRetry: vi.fn() };
  const view = render(<GuidancePanel {...props} session={makeSession({ insights: [safe], analyzed_sentences: ['m1:0'] })} primary={safe} />);
  expect(screen.getByRole('heading', { name: 'Seems safe' })).toBeVisible();
  expect(screen.getByText('No further questions suggested right now.')).toBeVisible();
  expect(screen.queryByText('Suggested question')).not.toBeInTheDocument();
  expect(screen.queryByText(/Anything else you would like to ask/)).not.toBeInTheDocument();
  view.rerender(<GuidancePanel {...props} session={makeSession({ insights: [safe], analyzed_sentences: ['m1:0'], analysis_provider: 'unavailable' })} primary={safe} />);
  expect(screen.queryByRole('heading', { name: 'Seems safe' })).not.toBeInTheDocument();
});

it('does not show Seems safe when only the agent identity is confirmed', () => {
  const identityOnly = { ...demoInsight, recommended_action: 'CONTINUE_REVIEW' as const, signal_codes: [], signals: [],
    verification_checks: [{ check_type: 'RECIPIENT_IDENTITY' as const, status: 'CONFIRMED' as const, evidence: demoInsight.evidence }] };
  render(<GuidancePanel session={makeSession({ insights: [identityOnly], analyzed_sentences: ['m1:0'] })} primary={identityOnly}
    busy={false} onAction={vi.fn()} onAcknowledge={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.queryByRole('heading', { name: 'Seems safe' })).not.toBeInTheDocument();
});

it('keeps a question when an unresolved risk remains despite a continue-review action', () => {
  const unresolved = { ...demoInsight, recommended_action: 'CONTINUE_REVIEW' as const };
  render(<GuidancePanel session={makeSession({ insights: [unresolved], analyzed_sentences: ['m1:0'] })} primary={unresolved}
    busy={false} onAction={vi.fn()} onAcknowledge={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.queryByRole('heading', { name: 'Seems safe' })).not.toBeInTheDocument();
  expect(screen.getByText('Suggested question')).toBeVisible();
});
