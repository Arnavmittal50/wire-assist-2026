import { ArrowUpRight } from 'lucide-react';
import type { ActionInput, Session } from '../types/api';
import { Button } from './ui/button';

interface Props {
  session: Session | null;
  busy: boolean;
  onAction: (action: Omit<ActionInput, 'note'>) => void;
  onEnd: () => void;
}

export function SessionControls({ session, busy, onAction, onEnd }: Props) {
  const disabled = busy || session?.status !== 'active';
  return (
    <section className="session-controls workspace-panel" aria-labelledby="session-controls-heading">
      <div className="card-heading"><h2 id="session-controls-heading">Session controls</h2></div>
      <div className="session-control-buttons">
        <Button className="decision-legitimate" variant="secondary" disabled={disabled || session?.review_outcome === 'legitimate'} onClick={() => onAction({ action_type: 'MARK_LEGITIMATE' })}>{session?.review_outcome === 'legitimate' ? 'Reviewed as legitimate' : 'Mark reviewed as legitimate'}<ArrowUpRight size={16} /></Button>
        <Button className="decision-escalate" variant="secondary" disabled={disabled} onClick={() => onAction({ action_type: 'ESCALATE_CASE' })}>Simulate escalation<ArrowUpRight size={16} /></Button>
        <Button variant="secondary" disabled={disabled} onClick={onEnd}>End review</Button>
        <p className="fine-print">Review actions are simulated. No funds move.</p>
      </div>
    </section>
  );
}
