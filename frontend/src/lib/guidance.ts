import type { Insight } from '../types/api';

export function seemsSafe(insight: Insight, requireWireChecks = true): boolean {
  const facts = new Set(['LARGE_TRANSACTION', 'UNUSUAL_PAYMENT_PATTERN']);
  const confirmed = new Set((insight.verification_checks ?? []).filter(check => check.status === 'CONFIRMED'
    && check.evidence.some(evidence => evidence.source_type === 'segment')).map(check => check.check_type));
  return ['CONTINUE_REVIEW', 'NO_ACTION'].includes(insight.recommended_action)
    && (!requireWireChecks || confirmed.has('RECIPIENT_IDENTITY') && confirmed.has('PAYMENT_INSTRUCTIONS'))
    && !(insight.signals ?? []).some(signal => signal.status !== 'NOT_ESTABLISHED' && !facts.has(signal.signal_type))
    && !insight.signal_codes.some(code => !facts.has(code));
}
