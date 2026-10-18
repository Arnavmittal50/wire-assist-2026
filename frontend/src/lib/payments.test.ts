import { describe, expect, it } from 'vitest';
import { latestOpenPayment } from './payments';
import type { FinancialEvent } from '../types/api';

const initiation: FinancialEvent = { event_id: 'e1', member_id: 'M1002', timestamp: '2026-09-20T10:00:00Z', event_sequence: 1, event_type: 'Payment Initiated', status: 'Pending', payment_id: 'p1', amount: 200, payment_rail: 'P2P', channel: 'Mobile', recipient_id: 'r1', description: 'P2P request' };
describe('reviewable payment selection', () => {
  it('never reopens a historical pending initiation that was completed before the cutoff', () => {
    expect(latestOpenPayment([initiation, { ...initiation, event_id: 'e2', event_sequence: 2, event_type: 'Payment Completed', status: 'Completed' }])).toBeUndefined();
  });
  it('selects the latest still-open request based on each payment lifecycle', () => {
    const held = { ...initiation, event_id: 'e3', event_type: 'Payment Held', status: 'Held', event_sequence: 2 };
    expect(latestOpenPayment([held, initiation])).toEqual(initiation);
    expect(latestOpenPayment([initiation, { ...initiation, event_id: 'e4', payment_id: 'p2', amount: 1500, timestamp: '2026-09-26T11:00:00Z' }])?.amount).toBe(1500);
  });
});
