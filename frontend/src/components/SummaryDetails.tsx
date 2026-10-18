import type { SessionSummary } from '../types/api';
import { Badge } from './ui/badge';
import { Transcript } from './Transcript';
import { ScamMatches } from './ScamMatches';
import { seemsSafe } from '../lib/guidance';

export function SummaryDetails({ summary }: { summary: SessionSummary }) {
  return <div className="summary-details">
    <section aria-label="Saved conversation" className="summary-conversation">
      <h3>Conversation</h3>
      {summary.transcript_retained ? <Transcript segments={summary.transcript ?? []} partial="" sessionStartedAt={summary.created_at}
        emptyMessage="No finalized conversation turns were captured." /> : <p className="fine-print">Conversation text was not retained for this session.</p>}
    </section>
    <section aria-label="All saved insights" className="summary-insights">
      <h3>All insights ({summary.insights?.length ?? 0})</h3>
      {summary.insights?.map((insight, index) => <article className="summary-insight" key={`${insight.insight_id}-${index}`}>
        <header><h4>{insight.title}</h4><Badge tone="neutral">{insight.status.replaceAll('_', ' ')}</Badge></header>
        <p>{insight.observation}</p>
        {seemsSafe(insight, summary.payment.rail === 'Wire') ? <p>Seems safe — no further questions suggested.</p>
          : insight.suggested_question && <p><strong>Suggested question:</strong> {insight.suggested_question}</p>}
        <details><summary>Evidence and recommendation</summary>
          {insight.evidence.map((evidence, evidenceIndex) => <blockquote key={evidenceIndex}>{evidence.quote}<cite>{evidence.source_type} · {evidence.source_id}</cite></blockquote>)}
          {insight.signals?.map(signal => <p key={signal.signal_type}><strong>{signal.signal_type.replaceAll('_', ' ')} · {signal.status.replaceAll('_', ' ')}</strong> — {signal.explanation}</p>)}
          <p>{insight.recommended_action.replaceAll('_', ' ')} · {insight.provider.toUpperCase()}</p>
        </details>
      </article>)}
      {!summary.insights?.length && <p className="fine-print">No saved insights were produced.</p>}
    </section>
    {!!summary.scam_matches?.length && <ScamMatches matches={summary.scam_matches} />}
  </div>;
}
