import { useCallback, useEffect, useRef, useState } from 'react';
import type { ConnectionState, Speaker } from '../types/api';
import { PcmPackets } from '../lib/pcmPackets';

export type MicrophoneState = 'stopped' | 'starting' | 'connecting' | 'recording' | 'paused';
interface Options {
  enabled: boolean;
  live: boolean;
  connection: ConnectionState;
  speaker: Speaker;
  serverAudioStatus: 'stopped' | 'recording' | 'paused';
  sendControl: (message: unknown) => void;
  sendAudio: (audio: ArrayBuffer) => void;
  onError: (message: string) => void;
}

function startupError(failure: unknown, stage: 'device' | 'processing' | 'provider'): string {
  const name = failure instanceof Error || failure instanceof DOMException ? failure.name : '';
  if (stage === 'provider') return 'Microphone access succeeded, but the transcription connection could not start. Reconnect to the backend and retry, or use manual text.';
  if (stage === 'processing') return 'Microphone access succeeded, but browser audio processing could not start. Reload this page in Chrome or Edge on localhost and retry, or use manual text.';
  switch (name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return 'Microphone permission is blocked. Allow Microphone for this site using the icon beside the address bar. In Windows Settings > Privacy & security > Microphone, enable microphone access and desktop app access. Reload and retry, or use manual text.';
    case 'NotFoundError':
      return 'No microphone was found. Connect or enable an input device in Windows Settings > System > Sound, then retry or use manual text.';
    case 'NotReadableError':
    case 'AbortError':
      return 'The microphone could not be opened. Check the selected input device in Windows Sound settings and close other apps using it, then retry or use manual text.';
    case 'OverconstrainedError':
      return 'The selected microphone does not support the requested audio settings. Select another input device in your browser, then retry or use manual text.';
    default:
      return 'Could not start the microphone. Check the input device and browser permissions, then retry or use manual text.';
  }
}

export function useMicrophone(options: Options) {
  const [status, setStatus] = useState<MicrophoneState>('stopped');
  const statusRef = useRef<MicrophoneState>('stopped');
  const current = useRef(options); current.current = options;
  const stream = useRef<MediaStream | null>(null);
  const context = useRef<AudioContext | null>(null);
  const source = useRef<MediaStreamAudioSourceNode | null>(null);
  const processor = useRef<AudioWorkletNode | null>(null);
  const generation = useRef(0);
  const providerTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const packets = useRef(new PcmPackets());
  const updateStatus = useCallback((next: MicrophoneState) => { statusRef.current = next; setStatus(next); }, []);
  const release = useCallback(() => {
    clearTimeout(providerTimer.current); providerTimer.current = undefined;
    generation.current++;
    stream.current?.getTracks().forEach(track => track.stop()); stream.current = null;
    if (statusRef.current === 'recording' && current.current.live && current.current.serverAudioStatus === 'recording') {
      const tail = packets.current.flush();
      if (tail) { try { current.current.sendAudio(tail); } catch { /* Stop still releases every microphone resource. */ } }
    }
    packets.current.reset();
    if (processor.current) { processor.current.port.onmessage = null; processor.current.disconnect(); processor.current = null; }
    source.current?.disconnect(); source.current = null;
    if (context.current) { void context.current.close().catch(() => undefined); context.current = null; }
  }, []);
  const stop = useCallback(() => {
    const wasActive = statusRef.current !== 'stopped';
    release(); updateStatus('stopped');
    if (wasActive && current.current.live && current.current.connection === 'connected') {
      try { current.current.sendControl({ type: 'audio_control', command: 'stop', speaker: current.current.speaker }); } catch { /* Resources are already released. */ }
    }
  }, [release, updateStatus]);
  const start = useCallback(async () => {
    const config = current.current;
    if (!config.enabled || statusRef.current === 'recording' || statusRef.current === 'starting' || statusRef.current === 'connecting') return;
    if (config.live && config.connection !== 'connected') { config.onError('Connect to the backend before starting live audio. Manual text is still available.'); return; }
    if (globalThis.isSecureContext === false) { config.onError('Open this app at http://localhost:5173 or http://127.0.0.1:5173 to allow microphone access, or use HTTPS. Plain HTTP on a network IP cannot capture the microphone.'); return; }
    if (!navigator.mediaDevices?.getUserMedia || !globalThis.AudioContext || !globalThis.AudioWorkletNode) { config.onError('Microphone capture requires a supported browser on localhost or HTTPS. Use manual text instead.'); return; }
    const resume = statusRef.current === 'paused';
    const requestGeneration = ++generation.current;
    updateStatus('starting');
    let stage: 'device' | 'processing' | 'provider' = 'device';
    try {
      const media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true }, video: false });
      if (generation.current !== requestGeneration) { media.getTracks().forEach(track => track.stop()); return; }
      stream.current = media;
      stage = 'processing';
      // Use the device's native rate. The worklet resamples to PCM16/16 kHz;
      // forcing 16 kHz here can prevent a native-rate input from connecting.
      const audio = new AudioContext();
      context.current = audio;
      await audio.audioWorklet.addModule(`${import.meta.env.BASE_URL}worklets/pcm-recorder.js`);
      if (generation.current !== requestGeneration) return;
      const worklet = new AudioWorkletNode(audio, 'pcm-recorder'); processor.current = worklet;
      worklet.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (statusRef.current !== 'recording' || !current.current.live || current.current.serverAudioStatus !== 'recording') return;
        try { for (const packet of packets.current.push(event.data)) current.current.sendAudio(packet); }
        catch (failure) { stop(); current.current.onError((failure as Error).message); }
      };
      source.current = audio.createMediaStreamSource(media);
      const silence = audio.createGain(); silence.gain.value = 0;
      source.current.connect(worklet); worklet.connect(silence); silence.connect(audio.destination);
      await audio.resume();
      if (generation.current !== requestGeneration) return;
      if (config.live) {
        stage = 'provider';
        config.sendControl({ type: 'audio_control', command: resume ? 'resume' : 'start', speaker: config.speaker });
        updateStatus('connecting');
        providerTimer.current = setTimeout(() => {
          if (statusRef.current !== 'connecting') return;
          stop(); current.current.onError('Transcription did not connect in time. Microphone capture stopped. Continue with manual text or retry.');
        }, 35000);
      } else updateStatus('recording');
    } catch (failure) {
      if (generation.current !== requestGeneration) return;
      release(); updateStatus('stopped');
      config.onError(startupError(failure, stage));
    }
  }, [release, stop, updateStatus]);
  const pause = useCallback(() => {
    if (statusRef.current !== 'recording') return;
    release(); updateStatus('paused');
    if (current.current.live) {
      try { current.current.sendControl({ type: 'audio_control', command: 'pause', speaker: current.current.speaker }); }
      catch (failure) { current.current.onError((failure as Error).message); }
    }
  }, [release, updateStatus]);
  useEffect(() => {
    if (!options.enabled || (options.connection === 'disconnected' && options.live)) stop();
  }, [options.enabled, options.connection, options.live, stop]);
  useEffect(() => {
    if (!options.live) return;
    if (status === 'connecting' && options.serverAudioStatus === 'recording') {
      clearTimeout(providerTimer.current); providerTimer.current = undefined; updateStatus('recording');
    } else if (status === 'recording' && options.serverAudioStatus !== 'recording') stop();
  }, [options.live, options.serverAudioStatus, status, stop, updateStatus]);
  useEffect(() => () => release(), [release]);
  return { status, start, pause, stop };
}
