import { MessageSquareText } from 'lucide-react';
import type { Segment } from '../types/api';

export function Transcript({ segments, partial, emptyMessage, speakerNotice, diarizationPending = 0, diarizationNotice, onCorrectSpeaker, disabled = false, memberName, sessionStartedAt }: { memberName?: string; sessionStartedAt?: string; segments: Segment[]; partial: string; emptyMessage?: string; speakerNotice?: string; diarizationPending?: number; diarizationNotice?: string; onCorrectSpeaker?: (segmentId: string, speaker: 'member' | 'teller' | 'unknown') => void; disabled?: boolean }) {
  function elapsed(segment: Segment) {
    const seconds = segment.start_time ?? (sessionStartedAt ? (Date.parse(segment.timestamp) - Date.parse(sessionStartedAt)) / 1000 : NaN);
    if (!Number.isFinite(seconds)) return '—';
    const value = Math.max(0, Math.floor(seconds));
    return `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}`;
  }
  const latest = new Map<string, Segment>();
  for (const segment of segments) {
    const previous = latest.get(segment.segment_id);
    if (!previous || (segment.revision ?? 0) >= (previous.revision ?? 0)) latest.set(segment.segment_id, segment);
  }
  const unique = [...latest.values()].sort((a, b) => (a.call_sid && b.call_sid ? (a.start_time ?? 0) - (b.start_time ?? 0) : 0) || a.sequence - b.sequence);
  return <div className="transcript-stream" role="log" aria-label="Conversation transcript" aria-live="polite">
    {(diarizationPending > 0 || segments.some(s => s.diarization_status === 'PENDING')) && <p className="speaker-notice" role="status">Speaker identification processing</p>}
    {diarizationNotice && diarizationNotice !== 'Speaker identification processing' && <p className="speaker-notice" role="status">{diarizationNotice}</p>}
    {speakerNotice && <p className="speaker-notice" role="status">{speakerNotice}</p>}
    {unique.length === 0 && !partial && <div className="transcript-empty"><span className="empty-icon"><MessageSquareText size={27} /></span><h3>{emptyMessage || 'A good conversation starts here.'}</h3><p>{emptyMessage ? 'The session summary preserves the outcome and review counts.' : <>Listen, ask, and give members room to explain.<br />Finalized conversation turns will appear here.</>}</p></div>}
    {unique.map(segment => <article key={segment.segment_id} className={`transcript-turn speaker-${segment.speaker}`}><time className="turn-time" dateTime={segment.timestamp || undefined}>{elapsed(segment)}</time><div className="turn-content"><div className="turn-meta"><span className="speaker-avatar">{segment.speaker === 'member' ? 'M' : segment.speaker === 'teller' ? 'T' : '?'}</span><strong>{segment.diarization_status === 'PENDING' || segment.speaker === 'auto' ? 'Identifying speaker...' : segment.speaker === 'unknown' ? 'Unknown speaker' : segment.speaker === 'member' ? memberName || 'Member' : 'Teller'}</strong><span>{segment.diarization_status === 'FAILED' ? 'Identification failed' : segment.speaker_source === 'twilio_track' ? 'Call audio: verified track' : segment.speaker_id ? segment.speaker_id.replace('speaker_', 'Voice ') : segment.speaker_source === 'automatic' ? 'Unverified label' : segment.source === 'manual' ? 'Manual · finalized' : 'Gemini · finalized'}</span>{onCorrectSpeaker && <select className="speaker-correction" aria-label={`Speaker for turn ${segment.sequence + 1}`} value={segment.speaker === 'auto' ? 'unknown' : segment.speaker} disabled={disabled || segment.speaker_source === 'twilio_track'} onChange={event => onCorrectSpeaker(segment.segment_id, event.target.value as 'member' | 'teller' | 'unknown')}><option value="unknown">Unknown</option><option value="teller">Teller</option><option value="member">Member</option></select>}</div><p>{segment.text}</p></div></article>)}
    {partial && <article className="transcript-turn provisional"><div className="turn-meta"><strong>Unassigned speaker</strong><span>Provisional · not scored</span></div><p>{partial}</p></article>}
  </div>;
}
