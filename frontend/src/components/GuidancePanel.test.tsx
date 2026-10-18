import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { GuidancePanel } from './GuidancePanel';
import { demoInsight, makeSession } from '../test/fixtures';

it('navigates existing guidance without allowing actions on a superseded recommendation', () => {
  const previous = { ...demoInsight, insight_id: 'old', title: 'Earlier request', status: 'superseded' as const };
  const current = { ...demoInsight, insight_id: 'current', title: 'Verify the current request' };
  const onAcknowledge = vi.fn();
  const view = render(<GuidancePanel session={makeSession({ insights: [previous, current] })} primary={current} busy={false}
    onAction={vi.fn()} onAcknowledge={onAcknowledge} onRetry={vi.fn()} onEnd={vi.fn()} />);
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
    onAction={vi.fn()} onAcknowledge={onAcknowledge} onRetry={vi.fn()} onEnd={vi.fn()} />);
  expect(screen.getByRole('heading', { name: latest.title })).toBeVisible();
});

it('allows retrying an unavailable review with no recommendation and keeps decisions disabled after ending', () => {
  const retry = vi.fn();
  const props = { busy: false, onAction: vi.fn(), onAcknowledge: vi.fn(), onRetry: retry, onEnd: vi.fn() };
  const session = makeSession({ analysis_provider: 'unavailable' });
  const view = render(<GuidancePanel {...props} session={session} />);
  fireEvent.click(screen.getByRole('button', { name: 'Retry guidance' }));
  expect(retry).toHaveBeenCalledOnce();
  view.rerender(<GuidancePanel {...props} session={{ ...session, status: 'ended' }} />);
  expect(screen.getByRole('button', { name: 'Retry guidance' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Mark reviewed as legitimate' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Simulate escalation' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'End review' })).toBeDisabled();
});
