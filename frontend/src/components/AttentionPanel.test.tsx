import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AttentionPanel } from './AttentionPanel';
import { ProviderNotice } from './ProviderNotice';
import type { Attention } from '../types/api';

describe('attention and provider transparency', () => {
  it('shows active points, zeroes resolved rules, and explains the non-probabilistic score', () => {
    const attention: Attention = { score: 15, level: 'review', explanation: 'Explicit review rules.', change: 'One concern was resolved.', signals: [{ signal_id: 's1', code: 'FIRST_WIRE', title: 'First wire in account history', points: 15, evidence: [{ source_id: 'E1', source_type: 'event', quote: 'First recorded wire request' }], status: 'active', origin: 'history' }, { signal_id: 's2', code: 'URGENCY', title: 'Urgency was reported', points: 20, evidence: [], status: 'resolved', origin: 'conversation' }] };
    render(<AttentionPanel attention={attention} />);
    expect(screen.getByText('15 pts')).toBeInTheDocument();
    expect(screen.getByText('+15')).toBeInTheDocument();
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText(/not a calibrated fraud probability/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Resolve' })).not.toBeInTheDocument();
  });
  it('labels fallback analysis as deterministic demo even in a live review', () => {
    render(<ProviderNotice provider="demo" notice="Gemini is unavailable. Using the local demo adapter." />);
    expect(screen.getByText('Deterministic demo analysis')).toBeVisible();
    expect(screen.queryByText('Real Gemini analysis')).not.toBeInTheDocument();
    expect(screen.getByText(/Gemini is unavailable/)).toBeVisible();
  });
  it('labels verified provider responses separately', () => {
    render(<ProviderNotice provider="gemini" notice="The configured Gemini model returned this analysis." />);
    expect(screen.getByText('Real Gemini analysis')).toBeVisible();
  });
});
