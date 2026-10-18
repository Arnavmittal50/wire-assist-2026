import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useMicrophone } from './useMicrophone';
import type { ConnectionState, Speaker } from '../types/api';

const stopTrack = vi.fn();
const getUserMedia = vi.fn();
let lastNode: { port: { onmessage: ((event: { data: ArrayBuffer }) => void) | null }; disconnect: ReturnType<typeof vi.fn> };
const options = () => ({ enabled: true, live: false, connection: 'connected' as ConnectionState, speaker: 'member' as Speaker, serverAudioStatus: 'recording' as const, sendControl: vi.fn(), sendAudio: vi.fn(), onError: vi.fn() });
beforeEach(() => {
  stopTrack.mockReset(); getUserMedia.mockReset().mockResolvedValue({ getTracks: () => [{ stop: stopTrack }] });
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
  vi.stubGlobal('AudioContext', class {
    audioWorklet = { addModule: vi.fn().mockResolvedValue(undefined) };
    destination = {};
    close = vi.fn().mockResolvedValue(undefined);
    resume = vi.fn().mockResolvedValue(undefined);
    createMediaStreamSource = () => ({ connect: vi.fn(), disconnect: vi.fn() });
    createGain = () => ({ connect: vi.fn(), gain: { value: 1 } });
  });
  vi.stubGlobal('AudioWorkletNode', class {
    port = { onmessage: null as ((event: { data: ArrayBuffer }) => void) | null };
    connect = vi.fn(); disconnect = vi.fn();
    constructor() { lastNode = this; }
  });
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

describe('microphone lifecycle', () => {
  it('requests access only on Start and never transmits local demo preview', async () => {
    const config = options(); const { result } = renderHook(() => useMicrophone(config));
    expect(getUserMedia).not.toHaveBeenCalled();
    await act(() => result.current.start());
    expect(result.current.status).toBe('recording');
    act(() => lastNode.port.onmessage?.({ data: new ArrayBuffer(3200) }));
    expect(config.sendAudio).not.toHaveBeenCalled(); expect(config.sendControl).not.toHaveBeenCalled();
    act(() => result.current.stop());
    expect(stopTrack).toHaveBeenCalledOnce(); expect(result.current.status).toBe('stopped');
  });
  it('releases microphone immediately on Pause and acquires a fresh stream on Resume', async () => {
    const config = { ...options(), live: true }; const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    expect(config.sendControl).toHaveBeenCalledWith({ type: 'audio_control', command: 'start', speaker: 'member' });
    act(() => result.current.pause());
    expect(stopTrack).toHaveBeenCalledOnce(); expect(result.current.status).toBe('paused');
    await act(() => result.current.start());
    expect(getUserMedia).toHaveBeenCalledTimes(2); expect(result.current.status).toBe('recording');
    expect(config.sendControl).toHaveBeenLastCalledWith({ type: 'audio_control', command: 'resume', speaker: 'member' });
  });
  it('reports permission denial with a manual fallback', async () => {
    getUserMedia.mockRejectedValue(Object.assign(new Error('denied'), { name: 'NotAllowedError' }));
    const config = options(); const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    expect(result.current.status).toBe('stopped'); expect(config.onError).toHaveBeenCalledWith(expect.stringContaining('manual text'));
  });
  it.each([
    ['NotAllowedError', 'Microphone permission is blocked'],
    ['NotFoundError', 'No microphone was found'],
    ['NotReadableError', 'microphone could not be opened'],
    ['OverconstrainedError', 'does not support the requested audio settings'],
  ])('explains %s instead of just showing stopped', async (name, message) => {
    getUserMedia.mockRejectedValue(new DOMException('test', name));
    const config = options(); const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    expect(result.current.status).toBe('stopped');
    expect(config.onError).toHaveBeenCalledWith(expect.stringContaining(message));
    expect(config.sendControl).not.toHaveBeenCalled();
  });
  it('identifies insecure pages before requesting a device', async () => {
    vi.stubGlobal('isSecureContext', false);
    const config = options(); const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(config.onError).toHaveBeenCalledWith(expect.stringContaining('http://localhost:5173'));
  });
  it('distinguishes audio processing failure from denied microphone permission', async () => {
    vi.stubGlobal('AudioContext', class { constructor() { throw new DOMException('test', 'NotSupportedError'); } });
    const config = options(); const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    expect(stopTrack).toHaveBeenCalledOnce();
    expect(config.onError).toHaveBeenCalledWith(expect.stringContaining('Microphone access succeeded, but browser audio processing'));
    expect(config.sendControl).not.toHaveBeenCalled();
  });
  it('stops live capture on disconnection and cleans up on unmount', async () => {
    const config = { ...options(), live: true };
    const { result, rerender, unmount } = renderHook(({ connection }) => useMicrophone({ ...config, connection }), { initialProps: { connection: 'connected' as ConnectionState } });
    await act(() => result.current.start());
    rerender({ connection: 'disconnected' });
    await waitFor(() => expect(result.current.status).toBe('stopped'));
    expect(stopTrack).toHaveBeenCalledOnce();
    rerender({ connection: 'connected' }); await act(() => result.current.start()); unmount();
    expect(stopTrack).toHaveBeenCalledTimes(2);
  });
  it('prevents duplicate starts and cancels late permission results', async () => {
    let resolvePermission!: (value: unknown) => void;
    getUserMedia.mockReturnValue(new Promise(resolve => { resolvePermission = resolve; }));
    const { result } = renderHook(() => useMicrophone(options()));
    let pending!: Promise<void>;
    act(() => { pending = result.current.start(); void result.current.start(); });
    expect(getUserMedia).toHaveBeenCalledOnce();
    act(() => result.current.stop());
    await act(async () => { resolvePermission({ getTracks: () => [{ stop: stopTrack }] }); await pending; });
    expect(stopTrack).toHaveBeenCalledOnce(); expect(result.current.status).toBe('stopped');
  });
  it('waits for provider acknowledgement and releases capture if it never arrives', async () => {
    vi.useFakeTimers();
    const config = { ...options(), live: true, serverAudioStatus: 'stopped' as const };
    const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    expect(result.current.status).toBe('connecting');
    act(() => lastNode.port.onmessage?.({ data: new ArrayBuffer(3200) }));
    expect(config.sendAudio).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(35000));
    expect(stopTrack).toHaveBeenCalledOnce(); expect(result.current.status).toBe('stopped');
    expect(config.onError).toHaveBeenCalledWith(expect.stringContaining('did not connect in time'));
  });
  it('releases live capture when the server ends provider processing', async () => {
    const config = { ...options(), live: true };
    const { result, rerender } = renderHook(({ serverAudioStatus }) => useMicrophone({ ...config, serverAudioStatus }), { initialProps: { serverAudioStatus: 'recording' as 'recording' | 'stopped' } });
    await act(() => result.current.start());
    expect(result.current.status).toBe('recording');
    rerender({ serverAudioStatus: 'stopped' });
    expect(stopTrack).toHaveBeenCalledOnce(); expect(result.current.status).toBe('stopped');
  });
  it('flushes a partial PCM packet after releasing tracks and before stopping the provider', async () => {
    const config = { ...options(), live: true }; const { result } = renderHook(() => useMicrophone(config));
    await act(() => result.current.start());
    act(() => lastNode.port.onmessage?.({ data: new ArrayBuffer(256) }));
    expect(config.sendAudio).not.toHaveBeenCalled();
    act(() => result.current.stop());
    expect(config.sendAudio).toHaveBeenCalledWith(expect.any(ArrayBuffer));
    expect(config.sendAudio.mock.calls[0][0].byteLength).toBe(256);
    expect(stopTrack.mock.invocationCallOrder[0]).toBeLessThan(config.sendAudio.mock.invocationCallOrder[0]);
    expect(config.sendAudio.mock.invocationCallOrder[0]).toBeLessThan(config.sendControl.mock.invocationCallOrder[1]);
  });
});
