import { History } from 'lucide-react';
import type { ScamMatch } from '../types/api';
import { Badge } from './ui/badge';
import { Card } from './ui/card';

export function ScamMatches({ matches }: { matches: ScamMatch[] }) {
  return <Card className="scam-matches-card"><div className="card-heading"><span className="section-title"><History size={17} /> Similar reported scams</span><Badge tone="purple">REFERENCE</Badge></div>
    {matches.length === 0 && <p className="observation">Listening for similarities to past reported scams. Matches appear here once the member describes the situation.</p>}
    {matches.map(m => <div className="question-box" key={m.report_id}><span className="eyebrow">{m.scam_type.toUpperCase()} · {m.is_synthetic ? 'SYNTHETIC TEST' : 'REPORT'} {m.report_id}</span><p>{m.summary}</p>
      <span className="action-label">{m.reasons.join(' · ')}{m.dollars_lost ? ` · reported loss $${m.dollars_lost.toLocaleString()}` : ''}</span></div>)}
    <p className="fine-print">Reference patterns include reported scams and labeled synthetic tests. A match does not establish fraud or change the attention score.</p></Card>;
}
