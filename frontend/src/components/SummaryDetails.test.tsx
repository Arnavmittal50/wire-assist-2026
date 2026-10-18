import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { SummaryDetails } from './SummaryDetails';
import { demoInsight, makeSession, makeSummary } from '../test/fixtures';

it('shows the saved conversation and includes dismissed and superseded insights', () => {
  const summary = { ...makeSummary(makeSession({ retain_transcript: true })),
    transcript: [{ segment_id: 'turn-1', sequence: 0, speaker: 'member' as const, text: 'The contractor sent a changed invoice.',
      timestamp: '2026-10-18T12:00:00Z', source: 'manual' as const, finalized: true }],
    insights: [{ ...demoInsight, title: 'Earlier invoice question', status: 'superseded' as const },
               { ...demoInsight, insight_id: 'i2', title: 'Reviewed verification question', status: 'dismissed' as const }],
  };
  render(<SummaryDetails summary={summary} />);
  expect(within(screen.getByRole('region', { name: 'Saved conversation' })).getByText(summary.transcript[0].text)).toBeVisible();
  const insights = screen.getByRole('region', { name: 'All saved insights' });
  expect(within(insights).getByText('Earlier invoice question')).toBeVisible();
  expect(within(insights).getByText('Reviewed verification question')).toBeVisible();
  expect(within(insights).getByText('superseded')).toBeVisible();
  expect(within(insights).getByText('dismissed')).toBeVisible();
});
