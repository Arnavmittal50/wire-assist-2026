import { useCallback, useEffect, useRef, useState } from 'react';
import type { Call, Device } from '@twilio/voice-sdk';
import type { ApiClient } from '../services/api';

export type CallPhase = 'idle' | 'connecting' | 'ringing' | 'connected' | 'reconnecting' | 'ended' | 'error';
interface Attempt {
  ended: boolean; reconnecting?: boolean; device?: Device; call?: Call; started?: number; finished?: number;
  polling?: ReturnType<typeof setInterval>; clock?: ReturnType<typeof setInterval>;
  deadline?: ReturnType<typeof setTimeout>; fetching: boolean;
}
const terminal: Record<string, string> = {
  completed: 'Call ended', busy: 'Member line is busy', 'no-answer': 'No answer',
  failed: 'Call failed. Check Twilio call logs and the configured numbers.', canceled: 'Call canceled',
};
function release(attempt: Attempt) {
  clearInterval(attempt.polling); clearInterval(attempt.clock); clearTimeout(attempt.deadline);
  attempt.ended = true;
  attempt.device?.destroy();
}
function callError(error: unknown) {
  const failure = error as { name?: string; code?: number; message?: string };
  if (failure?.name === 'NotAllowedError' || failure?.name === 'SecurityError') return 'Allow microphone access for this site, then try Call Member again.';
  if (failure?.name === 'NotFoundError') return 'No microphone found. Connect a microphone or headset and try again.';
  if (failure?.name === 'NotReadableError') return 'The microphone is unavailable. Check your device settings and close other apps using it.';
  // SDK error messages may contain identifiers; expose a useful code, not raw payloads.
  if (typeof failure?.code === 'number') return `Twilio call error ${failure.code}. Check microphone permission, network access, and Twilio call logs.`;
  return 'The call could not connect. Check your microphone, backend, and Twilio setup.';
}

export function useTwilioCall(api: ApiClient) {
  const current = useRef<Attempt | null>(null);
  const [phase, setPhase] = useState<CallPhase>('idle');
  const [message, setMessage] = useState('Ready to call the configured test phone');
  const [muted, setMuted] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [warning, setWarning] = useState('');
  const active = ['connecting', 'ringing', 'connected', 'reconnecting'].includes(phase);

  useEffect(() => () => {
    const attempt = current.current;
    current.current = null;
    if (attempt) release(attempt);
  }, [api]);

  const end = useCallback(() => {
    const attempt = current.current;
    if (!attempt || attempt.ended) return;
    // Invalidate before destroy: synchronous SDK disconnect events cannot revive it.
    current.current = null;
    release(attempt);
    setMuted(false); setPhase('ended'); setMessage('Call ended by teller'); setWarning('');
  }, []);

  const start = useCallback(async (processingTicket?: string) => {
    if (current.current && !current.current.ended) return;
    if (current.current) release(current.current);
    const attempt: Attempt = { ended: false, fetching: false };
    current.current = attempt;
    const valid = () => current.current === attempt;
    const live = () => valid() && !attempt.ended;
    setPhase('connecting'); setMessage('Preparing call...'); setMuted(false); setSeconds(0); setWarning('');
    const finish = (next: 'ended' | 'error', message: string) => {
      if (!live()) return;
      attempt.ended = true; attempt.finished = Date.now();
      clearInterval(attempt.clock); clearTimeout(attempt.deadline);
      setPhase(next); setMessage(message); setMuted(false);
      attempt.device?.destroy();
      // Keep status polling briefly for the signed busy/no-answer/completed callback.
    };
    const updateTimer = () => {
      if (valid() && attempt.started) setSeconds(Math.max(0, Math.floor((Date.now() - attempt.started) / 1000)));
    };
    const connected = (stamp: string | null) => {
      if (!live()) return;
      if (!attempt.started) {
        attempt.started = stamp && Number.isFinite(Date.parse(stamp)) ? Date.parse(stamp) : Date.now();
        attempt.clock = setInterval(updateTimer, 1000);
      }
      clearTimeout(attempt.deadline);
      if (!attempt.reconnecting) { setPhase('connected'); setMessage('Connected to member'); }
      updateTimer();
    };
    const poll = async () => {
      if (!valid() || (attempt.finished && Date.now() - attempt.finished > 5000)) {
        clearInterval(attempt.polling); return;
      }
      const sid = attempt.call?.parameters.CallSid;
      if (!sid || attempt.fetching) return;
      attempt.fetching = true;
      try {
        const status = await api.twilioCallStatus(sid);
        if (!valid()) return;
        if (terminal[status.status]) {
          if (!attempt.ended) finish('ended', terminal[status.status]);
          else setMessage(terminal[status.status]);
          if (status.connected_at && status.ended_at) setSeconds(Math.max(0, Math.floor((Date.parse(status.ended_at) - Date.parse(status.connected_at)) / 1000)));
          clearInterval(attempt.polling);
        } else if (live()) {
          if (status.status === 'in-progress') connected(status.connected_at);
          else if (status.status === 'ringing') { setPhase('ringing'); setMessage('Member phone is ringing'); }
          setWarning('');
        }
      } catch {
        if (live()) setWarning('Waiting for verified phone status. Check the backend and tunnel if this persists.');
      } finally { attempt.fetching = false; }
    };
    let stage: 'token' | 'microphone' | 'sdk' = 'token';
    try {
      const credentials = await api.twilioToken();
      if (!live()) return;
      stage = 'microphone';
      if (!navigator.mediaDevices?.getUserMedia) throw new Error('Unsupported browser');
      setMessage('Requesting microphone permission...');
      const permission = await navigator.mediaDevices.getUserMedia({ audio: true });
      permission.getTracks().forEach(track => track.stop());
      if (!live()) return;
      stage = 'sdk';
      const { Device } = await import('@twilio/voice-sdk');
      if (!live()) return;
      const device = new Device(credentials.token, { logLevel: 'silent', tokenRefreshMs: 60000 });
      attempt.device = device;
      device.on('error', (error: unknown) => finish('error', callError(error)));
      device.on('tokenWillExpire', () => {
        void api.twilioToken().then(next => { if (live()) device.updateToken(next.token); })
          .catch(() => { if (live()) setWarning('Call authorization could not refresh. Check the backend connection.'); });
      });
      setMessage('Connecting through Twilio...');
      attempt.deadline = setTimeout(() => finish('error', 'No confirmed phone connection. Check the TwiML App URL and public tunnel, then retry.'), 45000);
      // Destination stays server-owned. The optional opaque ticket binds consent and session.
      const call = processingTicket ? await device.connect({ params: { ProcessingTicket: processingTicket } }) : await device.connect();
      if (!live()) { call.disconnect(); device.destroy(); return; }
      attempt.call = call;
      call.on('ringing', () => { if (live() && !attempt.started) { setPhase('ringing'); setMessage('Member phone is ringing'); } });
      // SDK accept means media setup, not proof the phone answered. The signed
      // Number status callback confirms the phone leg before starting the timer.
      call.on('accept', () => { if (live()) void poll(); });
      call.on('disconnect', () => finish('ended', 'Call disconnected'));
      call.on('cancel', () => finish('ended', 'Call canceled'));
      call.on('reject', () => finish('ended', 'Call rejected'));
      call.on('error', (error: unknown) => finish('error', callError(error)));
      call.on('reconnecting', () => { if (live()) { attempt.reconnecting = true; setPhase('reconnecting'); setMessage('Audio connection interrupted; reconnecting...'); } });
      call.on('reconnected', () => { if (live()) { attempt.reconnecting = false; setMessage('Audio reconnected; checking phone status...'); void poll(); } });
      call.on('mute', (value: boolean) => { if (live()) setMuted(value); });
      attempt.polling = setInterval(() => void poll(), 1000);
      void poll();
    } catch (error) {
      // Token errors are controlled backend configuration messages; SDK errors are sanitized.
      finish('error', stage === 'token' && error instanceof Error ? error.message : callError(error));
    }
  }, [api]);

  const toggleMute = useCallback(() => {
    const attempt = current.current;
    if (!attempt?.call || attempt.ended || !attempt.started) return;
    attempt.call.mute(!attempt.call.isMuted());
    setMuted(attempt.call.isMuted());
  }, []);

  return { phase, active, message, warning, muted, seconds, start, end, toggleMute };
}
