import { FileText } from 'lucide-react';
import type { Session } from '../types/api';
import { Card } from './ui/card';

export function InitialReview({ session }: { session: Session }) {
  const review = session.initial_analysis;
  if (!review) return <Card className="context-card"><div className="context-body" role="status">{session.analysis_pending ? 'Preparing member-history summary…' : 'Initial member review is not available yet.'}</div></Card>;
  return <Card className="context-card">
    <div className="card-heading"><span className="section-title"><FileText size={17} /> Starting context</span></div>
    <div className="context-body">
      <p><strong>{review.provider === 'gemini' ? `Real Gemini · ${review.model}` : 'Deterministic demo · no model response'}</strong></p>
      <p>{review.event_count} eligible member events reviewed before conversation analysis.</p>
      {review.summary.length ? review.summary.map((fact, index) => <div key={index}>
        <p>{fact.text}</p>
        <details><summary>Summary evidence</summary>{fact.evidence.map((item, evidenceIndex) => <p key={evidenceIndex}><strong>{item.source_id}</strong>: <q>{item.quote}</q></p>)}</details>
      </div>) : <p>No supported summary facts were returned.</p>}
      <details><summary>Initial potential issues ({review.insights.length})</summary>
        <p>Starting hypotheses for human review; later conversation may clarify them.</p>
        {review.insights.map(insight => <div key={insight.insight_id}><p><strong>{insight.title}</strong> — {insight.observation}</p><p>{insight.suggested_question}</p>{insight.evidence.map((item, index) => <p key={index}><strong>{item.source_id}</strong>: <q>{item.quote}</q></p>)}</div>)}
      </details>
      <details><summary>Initial analysis source</summary><p>{review.notice}</p></details>
    </div>
  </Card>;
}
