import { useCallback, useEffect, useRef, useState } from 'react';
import type { ApiClient } from '../services/api';
import type { ConnectionState, Session, SocketEvent } from '../types/api';

export function useSessionSocket(api: ApiClient, sessionId: string | undefined, onSession: (session: Session) => void, onError: (message: string) => void) {
  const [connection, setConnection] = useState<ConnectionState>('idle');
  const [partial, setPartial] = useState('');
  const socketRef = useRef<WebSocket | null>(null);
  const callbacks = useRef({ onSession, onError });
  callbacks.current = { onSession, onError };

  useEffect(() => {
    if (!sessionId) { setConnection('idle'); setPartial(''); return; }
    let cancelled = false;
    let retries = 0;
    let retryTimer: ReturnType<typeof setTimeout>;
    const seen = new Set<string>();
    function connect() {
      if (cancelled) return;
      setConnection('connecting');
      const ws = new WebSocket(api.socketUrl(sessionId!));
      socketRef.current = ws;
      ws.onopen = () => {
        if (cancelled) { ws.close(); return; }
        ws.send(JSON.stringify({ type: 'authenticate', token: api.token }));
      };
      ws.onmessage = (message) => {
        if (cancelled) return;
        let event: SocketEvent;
        try { event = JSON.parse(message.data); } catch { callbacks.current.onError('The server sent an unreadable update. Reconnect to refresh the session.'); return; }
        if (event.session_id !== sessionId || seen.has(event.event_id)) return;
        seen.add(event.event_id);
        if (seen.size > 1000) seen.delete(seen.values().next().value!);
        if (event.type === 'connection_status') {
          setConnection('connected'); retries = 0;
          void api.session(sessionId!).then((session) => { if (!cancelled) callbacks.current.onSession(session); }).catch((error: Error) => callbacks.current.onError(error.message));
        }
        if (event.payload.session) callbacks.current.onSession(event.payload.session);
        if (event.type === 'transcript_partial') setPartial(event.payload.text || '');
        if (event.type === 'transcript_final' || event.type === 'session_status') setPartial('');
        if (event.type === 'error') callbacks.current.onError(event.payload.message || event.payload.detail || 'The live connection encountered an error. Manual text remains available.');
      };
      ws.onerror = () => { if (!cancelled) setConnection('disconnected'); };
      ws.onclose = (event) => {
        if (cancelled) return;
        setConnection('disconnected'); setPartial('');
        if (event.code === 4401 || event.code === 4403 || event.code === 4404 || event.code === 1008) {
          callbacks.current.onError('Session connection was rejected. Verify your access token or open a new session.'); return;
        }
        retryTimer = setTimeout(connect, Math.min(1000 * 2 ** retries++, 10000));
      };
    }
    connect();
    return () => { cancelled = true; clearTimeout(retryTimer); socketRef.current?.close(); socketRef.current = null; };
  }, [api, sessionId]);

  const send = useCallback((message: unknown) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) throw new Error('The live connection is unavailable. Reconnect before recording.');
    socketRef.current.send(JSON.stringify(message));
  }, []);
  const sendAudio = useCallback((audio: ArrayBuffer) => {
    if (socketRef.current?.readyState !== WebSocket.OPEN) throw new Error('Audio connection lost. Recording has stopped.');
    if (socketRef.current.bufferedAmount > 1024 * 1024) throw new Error('Audio connection is too slow. Recording has stopped.');
    socketRef.current.send(audio);
  }, []);
  return { connection, partial, send, sendAudio };
}
