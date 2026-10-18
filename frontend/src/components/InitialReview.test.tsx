import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { InitialReview } from './InitialReview';
import { makeSession } from '../test/fixtures';

describe('initial member review', () => {
  it('shows the initial model and inspectable citations independently of later analysis', () => {
    render(<InitialReview session={makeSession({ initial_analysis: {
      provider: 'gemini', model: 'gemini-3.8-flash', notice: 'Real initial analysis.', event_count: 37,
      summary: [{ text: 'An outgoing wire was recorded.', evidence: [{ source_type: 'event', source_id: 'E123', quote: 'Wire' }] }], insights: [],
    } })} />);
    expect(screen.getByText('Real Gemini · gemini-3.8-flash')).toBeInTheDocument();
    expect(screen.getByText('An outgoing wire was recorded.')).toBeInTheDocument();
    expect(screen.getByText('E123')).toBeInTheDocument();
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
