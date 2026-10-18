import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import type { Session } from '../types/api';
import { Button } from './ui/button';
import { Badge } from './ui/badge';
import { dateTimeLabel, money } from '../lib/utils';

export function SessionHistoryDialog({ sessions, activeSessionId, busy, onLoad, onClose }: { sessions: Session[]; activeSessionId?: string; busy: boolean; onLoad: (id: string) => void; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  return <dialog ref={dialog} className="action-dialog history-dialog" onCancel={event => { event.preventDefault(); onClose(); }} aria-labelledby="history-title"><div className="dialog-heading"><span className="eyebrow">SAVED LOCALLY IN SQLITE</span><Button variant="ghost" size="icon" onClick={onClose} aria-label="Close saved sessions"><X size={17} /></Button></div><h2 id="history-title">Saved review sessions</h2>{activeSessionId && <p>End the current review before opening another session.</p>}{sessions.length ? <div className="saved-sessions">{sessions.map(session => <article key={session.session_id}><div><strong>{session.member_id} <span>{money(session.payment.amount)}</span></strong><small>{dateTimeLabel(session.created_at)}</small><Badge tone={session.status === 'active' ? 'purple' : 'green'}>{session.status.toUpperCase()}</Badge></div><Button variant="secondary" size="sm" disabled={busy || (!!activeSessionId && activeSessionId !== session.session_id)} onClick={() => onLoad(session.session_id)}>Open review</Button></article>)}</div> : <p>{busy ? 'Loading saved sessions…' : 'No saved sessions yet. Your first review will appear here.'}</p>}</dialog>;
}

