import type { ReactNode } from 'react';
import type { Member, Payment } from '../types/api';
import { dateTimeLabel, money } from '../lib/utils';
import { Badge } from './ui/badge';

interface Props {
  members: Member[];
  memberId: string;
  onSelect: (id: string) => void;
  disabled: boolean;
  payment: Payment;
  asOf: string;
  eventCount: number;
  loading: boolean;
  newRecipient: boolean;
  children: ReactNode;
}

export function MemberPanel({ members, memberId, onSelect, disabled, payment, asOf, eventCount, loading, newRecipient, children }: Props) {
  return <section className="member-panel workspace-panel" aria-labelledby="member-heading">
    <div className="card-heading"><h2 id="member-heading">Member information</h2><Badge tone="neutral">Synthetic</Badge></div>
    <div className="member-profile">
      <label htmlFor="member-select" className="sr-only">SYNTHETIC MEMBER</label>
      <select id="member-select" value={memberId} onChange={event => onSelect(event.target.value)} disabled={disabled}>
        {!members.some(member => member.member_id === memberId) && <option value={memberId}>{memberId}</option>}
        {members.map(member => <option key={member.member_id} value={member.member_id}>{member.label.replace(/^Synthetic member /, 'Member ')}</option>)}
      </select>
      <dl className="profile-facts">
        <div><dt>Member ID</dt><dd>{memberId}</dd></div>
        <div><dt>History available</dt><dd>{loading ? 'Loading…' : `${eventCount} events`}</dd></div>
      </dl>
      <p className="review-date"><span>Review as of</span><time dateTime={asOf}>{dateTimeLabel(asOf)}</time></p>
    </div>
    <section className="payment-panel" aria-labelledby="payment-heading">
      <div className="card-heading"><h2 id="payment-heading">{payment.rail} request</h2>{newRecipient && <Badge tone="amber">New recipient</Badge>}</div>
      <div className="payment-body">
        <div className="payment-heading"><strong>{money(payment.amount)}</strong></div>
        <dl className="payment-facts">
          <div><dt>Recipient</dt><dd>{payment.recipient_id || 'Not entered'}</dd></div>
          <div><dt>Payment rail</dt><dd>{payment.rail}</dd></div>
          <div><dt>Purpose</dt><dd>{payment.purpose || 'Not provided'}</dd></div>
        </dl>
      </div>
    </section>
    {children}
  </section>;
}
