import { describe, expect, it } from 'vitest';
import { PcmPackets } from './pcmPackets';
describe('PCM transport buffering', () => {
  it('sends 100 ms packets and flushes the last partial packet once', () => {
    const transport = new PcmPackets();
    expect(transport.push(new Uint8Array(3000).fill(1).buffer)).toEqual([]);
    const packets = transport.push(new Uint8Array(512).fill(2).buffer);
    expect(packets).toHaveLength(1); expect(packets[0].byteLength).toBe(3200);
    expect(new Uint8Array(packets[0])[2999]).toBe(1); expect(new Uint8Array(packets[0])[3000]).toBe(2);
    const final = transport.flush(); expect(final?.byteLength).toBe(312);
    expect(new Uint8Array(final!)[0]).toBe(2); expect(transport.flush()).toBeNull();
  });
});
