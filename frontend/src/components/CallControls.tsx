import { Mic, MicOff, Phone, PhoneOff } from 'lucide-react';
import type { useTwilioCall } from '../hooks/useTwilioCall';
import { Button } from './ui/button';

export function CallControls({ call, microphoneBusy, onStart, preparing = false }: { call: ReturnType<typeof useTwilioCall>; microphoneBusy: boolean; onStart?: () => void; preparing?: boolean }) {
  const duration = `${Math.floor(call.seconds / 60).toString().padStart(2, '0')}:${(call.seconds % 60).toString().padStart(2, '0')}`;
  return <section className="call-toolbar" aria-label="Phone call">
    <div className="call-heading"><div><strong>Call Member</strong><p className="fine-print">Real Twilio call to the configured test phone</p></div><time aria-label="Call duration">{duration}</time></div>
    <p role={call.phase === 'error' ? 'alert' : 'status'} className="call-status"><span className={`status-dot ${call.phase === 'connected' ? 'green' : ''}`} /><span>{call.phase === 'idle' ? 'Idle' : call.phase[0].toUpperCase() + call.phase.slice(1)} · {call.message}</span></p>
    {call.warning && <p className="fine-print" role="status">{call.warning}</p>}
    <div className="call-buttons">{call.active ? <>
      <Button variant="secondary" size="sm" onClick={call.end}><PhoneOff size={15} />End Call</Button>
      <Button variant="ghost" size="sm" onClick={call.toggleMute} disabled={!['connected', 'reconnecting'].includes(call.phase)} aria-pressed={call.muted}>{call.muted ? <MicOff size={15} /> : <Mic size={15} />}{call.muted ? 'Unmute' : 'Mute'}</Button>
    </> : <Button size="sm" onClick={() => onStart ? onStart() : void call.start()} disabled={microphoneBusy || preparing}><Phone size={15} />Call Member</Button>}</div>
    {microphoneBusy && <p className="fine-print">Stop the transcription microphone before calling.</p>}
  </section>;
}
