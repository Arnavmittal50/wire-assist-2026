import { FileText, MessageCircle, ArrowUpRight, CheckCircle2 } from 'lucide-react';
import type { Evidence, Session } from '../types/api';
import { Card } from './ui/card';

function Sources({ evidence }: { evidence: Evidence[] }) {
  return <details className="review-sources"><summary>View evidence ({evidence.length})</summary>
    {evidence.map((item, index) => <div className="review-source" key={`${item.source_id}-${index}`}><span>Record {item.source_id}</span><blockquote>{item.quote}</blockquote></div>)}
  </details>;
}

export function InitialReview({ session }: { session: Session }) {
  const review = session.initial_analysis;
  const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(session.payment.amount);
  return <Card className="context-card initial-review">
    <div className="card-heading"><span className="section-title"><FileText size={17} /> Before you begin</span><span className="review-tag">MEMBER CONTEXT</span></div>
    <div className="context-body">
      <div className="review-payment"><div><span className="eyebrow">PROPOSED {session.payment.rail.toUpperCase()}</span><strong>{money}</strong></div><ArrowUpRight size={24} /></div>
      {!review ? <p role="status">{session.analysis_pending ? 'Preparing member-history summary...' : 'Member history has not been reviewed yet.'}</p> : <>
        <p className="review-scope">{review.event_count} eligible member events {review.provider === 'unavailable' ? 'available; model review did not complete.' : 'reviewed'} · As of {new Date(session.as_of).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' })}</p>
        {review.provider === 'unavailable' ? <div className="review-failure" role="alert"><strong>Gemini analysis unavailable</strong><p>{review.notice}</p></div> : <>
          <h3 className="review-heading"><CheckCircle2 size={16} /> What we know</h3>
          {review.summary.length ? <ul className="review-facts">{review.summary.map((fact, index) => <li key={index}><p>{fact.text}</p><Sources evidence={fact.evidence} /></li>)}</ul> : <p className="fine-print">No supported summary facts were returned.</p>}
          <h3 className="review-heading"><MessageCircle size={16} /> Clarify with the member <span>{review.insights.length}</span></h3>
          {review.insights.length ? <><p className="review-caption">Questions to explore, not findings of fraud.</p><ol className="review-questions">{review.insights.map((insight, index) => <li key={insight.insight_id}>
            <span className="review-question-label">{index === 0 ? 'ASK FIRST' : 'FOLLOW UP'}</span>
            <h4>{insight.title}</h4><p className="review-question">{insight.suggested_question}</p>
            <details className="review-reason"><summary>Why this matters</summary><p>{insight.observation}</p><Sources evidence={insight.evidence} /></details>
          </li>)}</ol></> : <p className="review-caption">No evidence-backed follow-up questions were returned.</p>}
        </>}
        <details className="review-model"><summary>About this review</summary><strong>{review.provider === 'unavailable' ? 'Review unavailable' : review.provider === 'gemini' ? `Real Gemini · ${review.model}` : 'Deterministic demo · no model response'}</strong><p>{review.notice}</p></details>
      </>}
    </div>
  </Card>;
}
