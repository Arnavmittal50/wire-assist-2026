import { Mic, MicOff, Phone, PhoneOff } from 'lucide-react';
import type { useTwilioCall } from '../hooks/useTwilioCall';
import { Button } from './ui/button';
import { Badge } from './ui/badge';

interface Props {
  call: ReturnType<typeof useTwilioCall>; microphoneBusy: boolean; onStart?: () => void; preparing?: boolean;
  title?: string; recording?: boolean; mode?: 'demo' | 'live';
}
export function CallControls({ call, microphoneBusy, onStart, preparing = false, title = 'Wire transfer verification', recording = false, mode = 'demo' }: Props) {
  const duration = `${Math.floor(call.seconds / 60).toString().padStart(2, '0')}:${(call.seconds % 60).toString().padStart(2, '0')}`;
  const audioActive = recording || (call.phase === 'connected' && !call.muted);
  return <section className="call-toolbar" aria-label="Phone call">
    <div className="capture-state"><span><span className={`status-dot ${recording || call.phase === 'connected' ? 'green' : ''}`} />{recording ? mode === 'demo' ? 'Microphone preview' : 'Recording' : call.active ? 'Call in progress' : 'Ready to connect'}</span><Badge tone="neutral">{mode === 'live' ? 'Live' : 'Demo'}</Badge></div>
    <div className="call-heading"><h2>{title}</h2><p className="fine-print">Real Twilio call to the configured test phone</p></div>
    <div className="call-audio-row"><div className="audio-indicator"><img src="/assets/figma/microphone.svg" alt="" width="14" height="18" />{audioActive && <span className="audio-waveform" aria-hidden="true">{[5, 12, 21, 9, 28, 17, 7, 22, 13, 25, 10, 5, 16, 8].map((height, index) => <span key={index} style={{ height }} />)}</span>}<span className={audioActive ? 'sr-only' : undefined}>{recording ? mode === 'demo' ? 'Local preview' : 'Audio capture active' : call.muted ? 'Call muted' : call.active ? 'Call audio' : 'Microphone off'}</span></div><time aria-label="Call duration">{duration}</time></div>
    <p role={call.phase === 'error' ? 'alert' : 'status'} className="call-status"><span className={`status-dot ${call.phase === 'connected' ? 'green' : ''}`} /><span>{call.phase === 'idle' ? 'Idle' : call.phase[0].toUpperCase() + call.phase.slice(1)} · {call.message}</span></p>
    {call.warning && <p className="fine-print" role="status">{call.warning}</p>}
    <div className="call-buttons">{call.active ? <>
      <Button variant="secondary" size="sm" onClick={call.end}><PhoneOff size={15} />End Call</Button>
      <Button variant="ghost" size="sm" onClick={call.toggleMute} disabled={!['connected', 'reconnecting'].includes(call.phase)} aria-pressed={call.muted}>{call.muted ? <MicOff size={15} /> : <Mic size={15} />}{call.muted ? 'Unmute' : 'Mute'}</Button>
    </> : <Button size="sm" onClick={() => onStart ? onStart() : void call.start()} disabled={microphoneBusy || preparing}><Phone size={15} />Call Member</Button>}</div>
    {microphoneBusy && <p className="fine-print">Stop the transcription microphone before calling.</p>}
  </section>;
}
