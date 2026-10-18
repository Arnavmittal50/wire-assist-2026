import { useState } from 'react';
import { ArrowLeft, ArrowRight, ArrowUpRight } from 'lucide-react';
import type { ActionInput, Insight, Session } from '../types/api';
import { Button } from './ui/button';
import { InsightCard } from './InsightCard';
import { ProviderNotice } from './ProviderNotice';
import { ScamMatches } from './ScamMatches';

interface Props {
  session: Session | null;
  primary?: Insight;
  busy: boolean;
  onAction: (action: Omit<ActionInput, 'note'>) => void;
  onAcknowledge: (insight: Insight) => void;
  onRetry: () => void;
  onEnd: () => void;
}

export function GuidancePanel({ session, primary, busy, onAction, onAcknowledge, onRetry, onEnd }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const insights = session?.insights ?? [];
  const history = [...insights.filter(item => item.insight_id !== primary?.insight_id), ...(primary ? [primary] : [])];
  const selected = history.find(item => item.insight_id === selectedId) ?? primary;
  const index = selected ? history.findIndex(item => item.insight_id === selected.insight_id) : history.length;
  const disabled = busy || session?.status !== 'active';
  const actionable = selected?.status === 'active' || selected?.status === 'acknowledged';
  const attention = session?.attention;

  return <aside className="guidance-panel workspace-panel" aria-labelledby="guidance-heading">
    <div className="guidance-heading"><h1 id="guidance-heading">Wire Assist Guidance</h1></div>
    <section className={`attention-summary attention-${attention?.level ?? 'unscored'}`} aria-label="Current attention score">
      <span className="eyebrow">Attention score</span>
      <span className="attention-level">{attention ? `${attention.level === 'elevated' ? 'Elevated attention' : attention.level === 'review' ? 'Review suggested' : 'Routine attention'} / rule-based` : 'Awaiting review'}</span>
      <strong>{attention ? <>{attention.score}<span>/100</span></> : <span className="unscored">Not assessed</span>}</strong>
      <p className="fine-print">Illustrative score · not a fraud probability</p>
    </section>
    <InsightCard insight={selected} pending={session?.analysis_pending ?? false} disabled={disabled || !actionable}
      reviewed={!!session?.analyzed_sentences?.length} waitingForSpeaker={session?.guidance_status === 'waiting_for_speaker'} unavailable={session?.analysis_provider === 'unavailable'}
      onRetry={session ? () => { setSelectedId(null); onRetry(); } : undefined} retryDisabled={disabled} compact historical={!!selected && selected.insight_id !== primary?.insight_id}
      onAcknowledge={selected ? () => onAcknowledge(selected) : undefined}
      onDismiss={selected ? () => onAction({ action_type: 'DISMISS', insight_id: selected.insight_id }) : undefined} />
    <nav className="assessment-navigation" aria-label="Guidance history">
      <Button variant="secondary" aria-label="Previous guidance" disabled={index <= 0 || history.length === 0} onClick={() => setSelectedId(history[index - 1].insight_id)}><ArrowLeft size={18} /></Button>
      <div aria-live="polite"><strong>Guidance {selected ? String(index + 1).padStart(2, '0') : '—'} / {String(history.length).padStart(2, '0')}</strong><span>{selected ? selected.insight_id === primary?.insight_id ? 'Current recommendation' : selected.status.replaceAll('_', ' ') : 'No recommendation yet'}</span></div>
      <Button variant="secondary" aria-label="Next guidance" disabled={index >= history.length - 1} onClick={() => setSelectedId(history[index + 1].insight_id === primary?.insight_id ? null : history[index + 1].insight_id)}><ArrowRight size={18} /></Button>
    </nav>
    <section className="session-controls" aria-labelledby="session-controls-heading">
      <div className="card-heading"><h2 id="session-controls-heading">Session controls</h2></div>
      <div className="session-control-buttons">
        <Button className="decision-legitimate" variant="secondary" disabled={disabled || session?.review_outcome === 'legitimate'} onClick={() => onAction({ action_type: 'MARK_LEGITIMATE' })}>{session?.review_outcome === 'legitimate' ? 'Reviewed as legitimate' : 'Mark reviewed as legitimate'}<ArrowUpRight size={16} /></Button>
        <Button className="decision-escalate" variant="secondary" disabled={disabled} onClick={() => onAction({ action_type: 'ESCALATE_CASE' })}>Simulate escalation<ArrowUpRight size={16} /></Button>
        <Button variant="secondary" disabled={disabled} onClick={onEnd}>End review</Button>
        <p className="fine-print">Review actions are simulated. No funds move.</p>
      </div>
    </section>
    {session && <ProviderNotice provider={session.analysis_provider} notice={session.provider_notice} pending={session.analysis_pending} />}
    <ScamMatches matches={session?.scam_matches ?? []} />
  </aside>;
}
