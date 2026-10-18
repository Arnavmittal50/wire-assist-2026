import { MessageSquareText } from 'lucide-react';
import type { Segment } from '../types/api';

export function Transcript({ segments, partial, emptyMessage }: { segments: Segment[]; partial: string; emptyMessage?: string }) {
  const unique = [...new Map(segments.map(segment => [segment.segment_id, segment])).values()].sort((a, b) => a.sequence - b.sequence);
  return <div className="transcript-stream" role="log" aria-label="Conversation transcript" aria-live="polite">
    {unique.length === 0 && !partial && <div className="transcript-empty"><span className="empty-icon"><MessageSquareText size={27} /></span><h3>{emptyMessage || 'A good conversation starts here.'}</h3><p>{emptyMessage ? 'The session summary preserves the outcome and review counts.' : <>Listen, ask, and give members room to explain.<br />Finalized conversation turns will appear here.</>}</p></div>}
    {unique.map(segment => <article key={segment.segment_id} className={`transcript-turn speaker-${segment.speaker}`}><div className="turn-meta"><span className="speaker-avatar">{segment.speaker === 'member' ? 'M' : segment.speaker === 'teller' ? 'T' : '?'}</span><strong>{segment.speaker === 'unknown' ? 'Unassigned speaker' : segment.speaker === 'member' ? 'Member' : 'Teller'}</strong><span>{segment.source === 'manual' ? 'Manual · finalized' : 'Gemini · finalized'}</span></div><p>{segment.text}</p></article>)}
    {partial && <article className="transcript-turn provisional"><div className="turn-meta"><strong>Unassigned speaker</strong><span>Provisional · not scored</span></div><p>{partial}</p></article>}
  </div>;
}
