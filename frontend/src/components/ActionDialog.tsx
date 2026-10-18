import { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';
import type { ActionInput } from '../types/api';
import { Button } from './ui/button';

export const actionLabels: Record<string, string> = {
  ASK_FOLLOW_UP: 'Ask a follow-up question', OFFER_VERIFICATION: 'Offer independent verification', OFFER_SPECIALIST: 'Offer a specialist', CONSIDER_HOLD: 'Consider a simulated hold', OFFER_TRUSTED_CONTACT: 'Offer a trusted contact', ESCALATE_CASE: 'Simulate escalation', CONTINUE_REVIEW: 'Continue review', NO_ACTION: 'Record no action needed', ACKNOWLEDGE: 'Acknowledge guidance', DISMISS: 'Dismiss recommendation', RESOLVE_SIGNAL: 'Resolve signal', DISMISS_SIGNAL: 'Dismiss signal', MARK_LEGITIMATE: 'Mark reviewed as legitimate',
};
export function ActionDialog({ action, busy, onClose, onConfirm }: { action: Omit<ActionInput, 'note'>; busy: boolean; onClose: () => void; onConfirm: (note: string) => Promise<boolean> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [note, setNote] = useState('');
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  const legitimate = action.action_type === 'MARK_LEGITIMATE';
  return <dialog ref={dialog} className="action-dialog" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }} aria-labelledby="action-title"><div className="dialog-heading"><span className="eyebrow">TELLER DECISION · SIMULATED</span><Button variant="ghost" size="icon" onClick={onClose} disabled={busy} aria-label="Close action"><X size={17} /></Button></div><h2 id="action-title">{actionLabels[action.action_type]}</h2><p>{legitimate ? 'Record the independent checks supporting this decision. Existing concerns will be resolved; new evidence can prompt another review. This records your judgment and does not transfer funds.' : action.action_type === 'CONSIDER_HOLD' ? 'Record a simulated request for an authorized employee to review a hold, limited to four hours. No funds are held or moved.' : action.action_type === 'OFFER_TRUSTED_CONTACT' ? 'A trusted contact is voluntary and does not gain control over the member’s funds. Record the member’s preference.' : 'Explain the evidence or conversation supporting your choice. This action is recorded in the review activity.'}</p><label htmlFor="action-note">{legitimate ? 'Independent verification and review rationale' : 'Reason or action note'}<textarea id="action-note" autoFocus rows={4} maxLength={2000} value={note} onChange={event => setNote(event.target.value)} placeholder={legitimate ? 'For example: verified invoice and recipient through an independently known channel…' : 'Describe what was checked, resolved, or agreed…'} disabled={busy} /></label><div className="dialog-buttons"><Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button><Button onClick={() => void onConfirm(note.trim())} disabled={busy || !note.trim()}>{busy ? 'Recording…' : 'Record decision'}</Button></div></dialog>;
}
