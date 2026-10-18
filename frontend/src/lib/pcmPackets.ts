// Keep the partial transport packet on the main thread so Stop can flush it
// synchronously after releasing microphone tracks, before the provider is stopped.
export class PcmPackets {
  private pending = new Uint8Array(0);
  constructor(private readonly packetBytes = 3200) {}
  push(buffer: ArrayBuffer): ArrayBuffer[] {
    const combined = new Uint8Array(this.pending.length + buffer.byteLength);
    combined.set(this.pending); combined.set(new Uint8Array(buffer), this.pending.length);
    const packets: ArrayBuffer[] = [];
    let offset = 0;
    while (combined.length - offset >= this.packetBytes) {
      packets.push(combined.slice(offset, offset + this.packetBytes).buffer); offset += this.packetBytes;
    }
    this.pending = combined.slice(offset);
    return packets;
  }
  flush(): ArrayBuffer | null {
    if (!this.pending.length) return null;
    const packet = this.pending.slice().buffer; this.pending = new Uint8Array(0); return packet;
  }
  reset() { this.pending = new Uint8Array(0); }
}
