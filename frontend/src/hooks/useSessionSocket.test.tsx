import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSessionSocket } from './useSessionSocket';
import { ApiClient } from '../services/api';

class TestSocket {
  static OPEN = 1;
  static instances: TestSocket[] = [];
  readyState = 1; bufferedAmount = 0;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn(); close = vi.fn();
  constructor() { TestSocket.instances.push(this); }
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); TestSocket.instances = []; });
describe('session socket', () => {
  it('authenticates without putting tokens in the URL and reports disconnection', () => {
    vi.stubGlobal('WebSocket', TestSocket); vi.useFakeTimers();
    const api = new ApiClient('test-token');
    const { result } = renderHook(() => useSessionSocket(api, 's1', vi.fn(), vi.fn()));
    const socket = TestSocket.instances[0];
    act(() => socket.onopen?.());
    expect(socket.send).toHaveBeenCalledWith(JSON.stringify({ type: 'authenticate', token: 'test-token' }));
    expect(api.socketUrl('s1')).not.toContain('test-token');
    act(() => socket.onclose?.({ code: 1006 }));
    expect(result.current.connection).toBe('disconnected');
    act(() => vi.advanceTimersByTime(1000)); expect(TestSocket.instances).toHaveLength(2);
  });
  it('ignores duplicate events and events for other sessions', () => {
    vi.stubGlobal('WebSocket', TestSocket);
    const api = new ApiClient('token'); const onSession = vi.fn();
    renderHook(() => useSessionSocket(api, 's1', onSession, vi.fn()));
    const socket = TestSocket.instances[0];
    const event = { type: 'transcript_final', event_id: 'e1', session_id: 's1', payload: { session: { session_id: 's1', revision: 1 } } };
    act(() => { socket.onmessage?.({ data: JSON.stringify(event) }); socket.onmessage?.({ data: JSON.stringify(event) }); socket.onmessage?.({ data: JSON.stringify({ ...event, event_id: 'e2', session_id: 'other' }) }); });
    expect(onSession).toHaveBeenCalledOnce();
  });
});
