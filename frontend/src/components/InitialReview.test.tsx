import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { InitialReview } from './InitialReview';
import { makeSession, demoInsight } from '../test/fixtures';

describe('initial member review', () => {
  it('keeps each real evidence flag with its related insight and preserves unrelated flags and actions', () => {
    const session = makeSession({ initial_analysis: { provider: 'demo', model: null, notice: 'Demo.', event_count: 1,
      summary: [], insights: [demoInsight] } });
    const flag = { signal_id: 'urgency', code: 'URGENCY', title: 'A deadline was reported', points: 20,
      evidence: demoInsight.evidence, status: 'active' as const, origin: 'conversation' as const };
    const other = { ...flag, signal_id: 'new', code: 'NEW_RECIPIENT', title: 'A new recipient was recorded',
      evidence: [{ source_id: 'E2', source_type: 'event' as const, quote: 'new_recipient: true' }] };
    const resolve = vi.fn();
    render(<InitialReview embedded session={session} attention={{ ...session.attention, signals: [flag, other] }} onResolve={resolve} />);
    const question = screen.getByText(demoInsight.suggested_question).closest('li')!;
    expect(within(question).getByText(flag.title)).toBeVisible();
    expect(within(question).queryByText(other.title)).not.toBeInTheDocument();
    expect(screen.getAllByRole('heading', { name: flag.title })).toHaveLength(1);
    expect(screen.getByRole('region', { name: 'Additional evidence flags' })).toHaveTextContent(other.title);
    fireEvent.click(within(question).getByText('Evidence & actions'));
    fireEvent.click(within(question).getByRole('button', { name: 'Resolve' }));
    expect(resolve).toHaveBeenCalledWith(flag);
  });
  it('puts facts and the suggested opening question ahead of expandable details', () => {
    render(<InitialReview session={makeSession({ initial_analysis: {
      provider: 'gemini', model: 'test-model', notice: 'Review complete.', event_count: 37,
      summary: [{ text: 'The recipient is new.', evidence: [{ source_type: 'event', source_id: 'e1', quote: 'new_recipient' }] }],
      insights: [demoInsight],
    } })} />);
    expect(screen.getByText('$1,500')).toBeVisible();
    expect(screen.getByRole('heading', { name: 'What we know' })).toBeVisible();
    expect(screen.getByText('The recipient is new.')).toBeVisible();
    expect(screen.getByText('ASK FIRST')).toBeVisible();
    expect(screen.getByText(demoInsight.suggested_question)).toBeVisible();
    expect(screen.getByText(demoInsight.observation)).not.toBeVisible();
  });
  it('shows an unavailable live review without presenting a demo classification', () => {
    render(<InitialReview session={makeSession({ mode: 'live', initial_analysis: {
      provider: 'unavailable', model: null, notice: 'Model access denied (HTTP 403).',
      event_count: 37, summary: [], insights: [],
    } })} />);
    expect(screen.getByText('Analysis unavailable')).toBeVisible();
    expect(screen.getByText(/available; model review did not complete/)).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('HTTP 403');
    expect(screen.queryByText(/Deterministic demo/)).not.toBeInTheDocument();
  });
  it('shows the initial model and inspectable citations independently of later analysis', () => {
    render(<InitialReview session={makeSession({ initial_analysis: {
      provider: 'gemini', model: 'gemini-3.5-flash-lite', notice: 'Real initial analysis.', event_count: 37,
      summary: [{ text: 'An outgoing wire was recorded.', evidence: [{ source_type: 'event', source_id: 'E123', quote: 'Wire' }] }], insights: [],
    } })} />);
    expect(screen.getByText('Real Gemini · gemini-3.5-flash-lite')).toBeInTheDocument();
    expect(screen.getByText('An outgoing wire was recorded.')).toBeInTheDocument();
    expect(screen.getByText('Record E123')).toBeInTheDocument();
    expect(screen.getByText(/37 eligible member events/)).toBeInTheDocument();
  });

  it('labels fallback output and pending preparation', () => {
    const { rerender } = render(<InitialReview session={makeSession({ analysis_pending: true })} />);
    expect(screen.getByRole('status')).toHaveTextContent('Preparing member-history summary');
    rerender(<InitialReview session={makeSession({ initial_analysis: {
      provider: 'demo', model: null, notice: 'No key configured.', event_count: 0, summary: [], insights: [],
    } })} />);
    expect(screen.getByText('Deterministic demo · no model response')).toBeInTheDocument();
    expect(screen.getByText('No supported summary facts were returned.')).toBeInTheDocument();
  });
});
