import { Check, ChevronRight, X } from 'lucide-react';
import type { Insight } from '../types/api';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { Card } from './ui/card';

interface Props {
  insight?: Insight; pending: boolean; onAcknowledge?: () => void; onDismiss?: () => void;
  disabled?: boolean; unavailable?: boolean; waitingForSpeaker?: boolean; reviewed?: boolean;
  onRetry?: () => void; retryDisabled?: boolean; compact?: boolean; historical?: boolean;
}

export function InsightCard({ insight, pending, onAcknowledge, onDismiss, disabled = false, unavailable = false, waitingForSpeaker = false, reviewed = false, onRetry, retryDisabled = disabled, compact = false, historical = false }: Props) {
  return <Card className="insight-card">
    <div className="insight-meta"><span className="eyebrow">{historical ? 'Previous guidance' : 'Current note'}</span><Badge tone="neutral">{insight ? insight.provider === 'groq' ? 'GROQ' : insight.provider === 'gemini' ? 'GEMINI' : 'DEMO' : pending ? 'REVIEWING' : waitingForSpeaker ? 'IDENTIFYING' : unavailable ? 'UNAVAILABLE' : 'READY'}</Badge></div>
    {pending && <div className="analysis-progress" role="status"><span className="spinner" /> Reviewing the latest conversation…</div>}
    {insight ? <>
      <h2>{insight.title}</h2>
      <div className="question-box"><span className="eyebrow">Suggested question</span><p>“{insight.suggested_question}”</p></div>
      <p className="observation">Reason: {insight.observation}</p>
      <details className="guidance-evidence" open={!compact}>
        <summary>Evidence &amp; recommendation</summary>
        {insight.signals?.map(signal => <div className="evidence-block" key={signal.signal_type}><Badge tone="amber">{signal.status.replaceAll('_', ' ')}</Badge><strong>{signal.signal_type.toLowerCase().replaceAll('_', ' ')}</strong><p>{signal.explanation}</p><blockquote>{signal.evidence_quote}</blockquote><p>{signal.suggested_question}</p></div>)}
        <div className="evidence-block"><span className="eyebrow">WHAT WE HEARD / OBSERVED</span>{insight.evidence.map((e, i) => <blockquote key={`${e.source_id}-${i}`}>“{e.quote}”<cite>{e.source_type === 'segment' ? 'Conversation' : e.source_id === 'proposed_payment' ? 'Entered payment' : 'Transaction history'} · {e.source_id.slice(0, 12)}</cite></blockquote>)}</div>
        <span className="action-label"><ChevronRight size={14} />{insight.recommended_action.toLowerCase().replaceAll('_', ' ')}</span>
        <p className="fine-print">{Math.round(insight.confidence_in_observation * 100)}% confidence in this observation. This is not the likelihood of a scam. A teller reviews every action.</p>
      </details>
      <div className="insight-buttons"><Button variant="secondary" size="sm" onClick={onAcknowledge} disabled={disabled || !onAcknowledge || insight.status === 'acknowledged'}><Check size={15} />{insight.status === 'acknowledged' ? 'Acknowledged' : 'Acknowledge'}</Button><Button variant="ghost" size="sm" onClick={onDismiss} disabled={disabled || !onDismiss}><X size={15} />Dismiss</Button></div>
    </> : <div className="insight-empty">
      <h2>{waitingForSpeaker ? 'Identifying who is speaking' : pending ? 'Preparing guidance' : unavailable ? 'Guidance unavailable' : reviewed ? 'No additional concerns established' : 'Ready when you are.'}</h2>
      <p>{waitingForSpeaker ? 'Words appear live. Guidance follows when the audio confirms the member. You can also assign a role beside a turn.' : pending ? 'Waiting for the current evidence review to finish.' : unavailable ? 'The model review could not complete. See the provider message before retrying.' : reviewed ? 'The latest reviewed evidence did not support an additional recommendation. Continue the conversation; new member statements will be reviewed.' : 'Start a review and add a conversation turn. Your copilot will suggest one useful, evidence-backed next step.'}</p>
      {(pending || waitingForSpeaker) && <div className="question-box"><span className="eyebrow">WHILE WAITING, ASK</span><p>What led you to make this payment, and how did you confirm the recipient?</p><span className="fine-print">General conversation starter</span></div>}
    </div>}
    {!pending && onRetry && <Button className="retry-guidance" variant="secondary" size="sm" disabled={retryDisabled} onClick={onRetry}>{unavailable ? 'Retry guidance' : 'Refresh Analysis'}</Button>}
  </Card>;
}
