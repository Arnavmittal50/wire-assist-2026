import type { FinancialEvent } from '../types/api';

export function latestOpenPayment(events: FinancialEvent[]): FinancialEvent | undefined {
  const ordered = [...events].sort((a, b) => a.timestamp.localeCompare(b.timestamp) || (a.event_sequence ?? 0) - (b.event_sequence ?? 0));
  const latestStatus = new Map<string, string>();
  for (const event of ordered) if (event.payment_id && event.status) latestStatus.set(event.payment_id, event.status);
  return ordered.reverse().find(event => {
    const status = event.payment_id ? latestStatus.get(event.payment_id) : event.status;
    return event.event_type === 'Payment Initiated' && !!event.amount && !!event.payment_rail && (status === 'Pending' || status === 'Held');
  });
}
