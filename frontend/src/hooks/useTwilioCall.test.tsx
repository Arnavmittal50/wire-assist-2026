import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ApiClient } from '../services/api';
import { useTwilioCall } from './useTwilioCall';

const sdk = vi.hoisted(() => {
  class Events {
    listeners = new Map<string, ((...args: any[]) => void)[]>();
    on(name: string, fn: (...args: any[]) => void) { this.listeners.set(name, [...(this.listeners.get(name) || []), fn]); return this; }
    emit(name: string, ...args: any[]) { this.listeners.get(name)?.forEach(fn => fn(...args)); }
  }
  class FakeCall extends Events {
    parameters = { CallSid: 'CA' + 'a'.repeat(32) };
    muted = false;
    disconnect = vi.fn(() => this.emit('disconnect'));
    mute = vi.fn((value: boolean) => { this.muted = value; this.emit('mute', value); });
    isMuted = () => this.muted;
  }
  const devices: FakeDevice[] = [];
  class FakeDevice extends Events {
    call = new FakeCall();
    connect = vi.fn(async () => this.call);
    updateToken = vi.fn();
    destroy = vi.fn();
    constructor(public token: string) { super(); devices.push(this); }
  }
  return { FakeDevice, devices };
});
vi.mock('@twilio/voice-sdk', () => ({ Device: sdk.FakeDevice }));
const stop = vi.fn();
const getUserMedia = vi.fn();
const token = vi.fn();
const status = vi.fn();
const api = { twilioToken: token, twilioCallStatus: status } as unknown as ApiClient;

beforeEach(() => {
  vi.useFakeTimers(); sdk.devices.length = 0; stop.mockReset();
  token.mockReset().mockResolvedValue({ token: 'fake-jwt', identity: 'wire_assist_teller', expires_in: 600 });
  status.mockReset().mockResolvedValue({ status: 'initiated', connected_at: null, ended_at: null });
  getUserMedia.mockReset().mockResolvedValue({ getTracks: () => [{ stop }] });
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } });
});
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

it('starts only on click and waits for the signed phone status, not SDK accept', async () => {
  const { result } = renderHook(() => useTwilioCall(api));
  expect(result.current.phase).toBe('idle');
  expect(token).not.toHaveBeenCalled(); expect(getUserMedia).not.toHaveBeenCalled();
  await act(() => result.current.start());
  expect(getUserMedia).toHaveBeenCalledWith({ audio: true }); expect(stop).toHaveBeenCalledOnce();
  const device = sdk.devices[0];
  expect(device.connect).toHaveBeenCalledWith(); // No phone number from the client.
  await act(async () => device.call.emit('accept'));
  expect(result.current.phase).toBe('connecting'); expect(result.current.seconds).toBe(0);
  act(() => device.call.emit('ringing'));
  expect(result.current.phase).toBe('ringing');
  status.mockResolvedValue({ status: 'in-progress', connected_at: null, ended_at: null });
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(result.current.phase).toBe('connected');
  await act(() => vi.advanceTimersByTimeAsync(3000));
  expect(result.current.seconds).toBe(3);
  act(() => result.current.toggleMute());
  expect(result.current.muted).toBe(true); expect(device.call.mute).toHaveBeenLastCalledWith(true);
  act(() => result.current.toggleMute()); expect(result.current.muted).toBe(false);
  act(() => result.current.end());
  expect(result.current.phase).toBe('ended'); expect(device.destroy).toHaveBeenCalled();
  const duration = result.current.seconds;
  await act(() => vi.advanceTimersByTimeAsync(10000));
  expect(result.current.seconds).toBe(duration);
});

it('handles remote hangup and late busy/no-answer callbacks without reviving the call', async () => {
  const { result } = renderHook(() => useTwilioCall(api));
  await act(() => result.current.start());
  const device = sdk.devices[0];
  act(() => device.call.emit('disconnect'));
  expect(result.current.phase).toBe('ended');
  status.mockResolvedValue({ status: 'no-answer', connected_at: null, ended_at: new Date().toISOString() });
  await act(() => vi.advanceTimersByTimeAsync(1000));
  expect(result.current.message).toBe('No answer');
  act(() => device.call.emit('ringing'));
  expect(result.current.phase).toBe('ended');
});

it('shows configuration and permission failures without simulating a call', async () => {
  const { result } = renderHook(() => useTwilioCall(api));
  token.mockRejectedValueOnce(new Error('Twilio setup incomplete. Set TWILIO_TWIML_APP_SID.'));
  await act(() => result.current.start());
  expect(result.current.message).toContain('TWILIO_TWIML_APP_SID');
  expect(getUserMedia).not.toHaveBeenCalled();
  getUserMedia.mockRejectedValueOnce(new DOMException('denied', 'NotAllowedError'));
  await act(() => result.current.start());
  expect(result.current.phase).toBe('error'); expect(result.current.message).toContain('Allow microphone');
  expect(sdk.devices).toHaveLength(0);
});

it('cancels pending tokens and prevents double-clicks from making duplicate calls', async () => {
  let resolve!: (value: unknown) => void;
  token.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const { result } = renderHook(() => useTwilioCall(api));
  let pending!: Promise<void>;
  act(() => { pending = result.current.start(); });
  await act(() => result.current.start());
  expect(token).toHaveBeenCalledOnce();
  act(() => result.current.end());
  await act(async () => { resolve({ token: 'late' }); await pending; });
  expect(sdk.devices).toHaveLength(0); expect(getUserMedia).not.toHaveBeenCalled();
  expect(result.current.phase).toBe('ended');
});

it('releases permission-check tracks when hangup happens during the permission prompt', async () => {
  let resolve!: (value: unknown) => void;
  getUserMedia.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const { result } = renderHook(() => useTwilioCall(api));
  let pending!: Promise<void>;
  await act(async () => { pending = result.current.start(); await Promise.resolve(); });
  act(() => result.current.end());
  await act(async () => { resolve({ getTracks: () => [{ stop }] }); await pending; });
  expect(stop).toHaveBeenCalledOnce(); expect(sdk.devices).toHaveLength(0);
});

it('refreshes short-lived tokens and cleans up SDK resources on unmount', async () => {
  const { result, unmount } = renderHook(() => useTwilioCall(api));
  await act(() => result.current.start());
  token.mockResolvedValueOnce({ token: 'refreshed', identity: 'wire_assist_teller' });
  const device = sdk.devices[0];
  await act(async () => device.emit('tokenWillExpire'));
  expect(device.updateToken).toHaveBeenCalledWith('refreshed');
  unmount(); expect(device.destroy).toHaveBeenCalled();
  const requests = status.mock.calls.length;
  await vi.advanceTimersByTimeAsync(10000);
  expect(status).toHaveBeenCalledTimes(requests);
});

it('fails visibly if the phone never confirms connection', async () => {
  const { result } = renderHook(() => useTwilioCall(api));
  await act(() => result.current.start());
  await act(() => vi.advanceTimersByTimeAsync(45000));
  expect(result.current.phase).toBe('error');
  expect(result.current.seconds).toBe(0);
  expect(sdk.devices[0].destroy).toHaveBeenCalled();
});

it('sanitizes SDK errors and prevents late SDK success from changing error state', async () => {
  const { result } = renderHook(() => useTwilioCall(api));
  await act(() => result.current.start());
  act(() => sdk.devices[0].emit('error', { code: 31000, message: 'sensitive payload' }));
  expect(result.current.phase).toBe('error');
  expect(result.current.message).toContain('31000');
  expect(result.current.message).not.toContain('sensitive');
  act(() => sdk.devices[0].call.emit('accept'));
  expect(result.current.phase).toBe('error');
});


it('sends only the server-issued processing ticket when AI assistance is enabled', async () => {
  const { result } = renderHook(() => useTwilioCall(api));
  await act(() => result.current.start('opaque-test-ticket'));
  expect(sdk.devices[0].connect).toHaveBeenCalledWith({ params: { ProcessingTicket: 'opaque-test-ticket' } });
});
