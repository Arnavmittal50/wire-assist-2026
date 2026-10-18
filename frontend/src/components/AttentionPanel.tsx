import { ChevronDown, ShieldCheck } from 'lucide-react';
import type { Attention, Signal } from '../types/api';
import { Badge } from './ui/badge';
import { Button } from './ui/button';

export function AttentionPanel({ attention, onResolve, onDismiss, disabled }: { attention: Attention; onResolve?: (signal: Signal) => void; onDismiss?: (signal: Signal) => void; disabled?: boolean }) {
  const active = attention.signals.filter(signal => signal.status === 'active');
  return <div className="attention-details">
    <section aria-labelledby="evidence-heading">
      <div className="card-heading"><h2 id="evidence-heading">Evidence flags</h2><span className="active-count">{String(active.length).padStart(2, '0')} active</span></div>
      <div className="signal-list">{active.length ? active.map(signal => <article className="signal-item" key={signal.signal_id}>
        <h3>{signal.title}</h3>
        {signal.evidence[0] && <p className="signal-quote">{signal.evidence[0].quote}</p>}
        <details className="signal-detail"><summary>Evidence &amp; actions</summary><span className="fine-print">{signal.origin} · {signal.points} pts</span>
          {signal.evidence.map((evidence, index) => <blockquote key={`${evidence.source_id}-${index}`}>“{evidence.quote}”<cite>{evidence.source_type} · {evidence.source_id}</cite></blockquote>)}
          {(onResolve || onDismiss) && <div className="signal-actions"><Button variant="secondary" size="sm" disabled={disabled || !onResolve} onClick={() => onResolve?.(signal)}>Resolve</Button><Button variant="ghost" size="sm" disabled={disabled || !onDismiss} onClick={() => onDismiss?.(signal)}>Dismiss signal</Button></div>}
        </details>
      </article>) : <p className="muted">No active signals. Continue the member-led review.</p>}</div>
    </section>
    <details className="disclosure"><summary><span><ShieldCheck size={16} /> Attention score explanation <span className="count">{attention.score} pts</span></span><ChevronDown size={16} /></summary><div className="attention-explanation"><div className="attention-score"><strong>{attention.score}<span>/100</span></strong><Badge tone={attention.level === 'elevated' ? 'amber' : attention.level === 'review' ? 'purple' : 'green'}>{attention.level.toUpperCase()}</Badge></div><div className="score-track"><span style={{ width: `${Math.min(100, Math.max(0, attention.score))}%` }} /></div><p>{attention.explanation}</p><p className="score-change">{attention.change}</p><ul className="rule-breakdown">{attention.signals.map(signal => <li key={signal.signal_id}><span>{signal.title}<small>{signal.status !== 'active' ? ` · ${signal.status}` : ''}</small></span><strong>{signal.status === 'active' ? `+${signal.points}` : '0'}</strong></li>)}</ul><p className="fine-print">Explicit rules determine this illustrative score. Repeated observations do not add duplicate points. This is not a calibrated fraud probability.</p></div></details>
  </div>;
}
