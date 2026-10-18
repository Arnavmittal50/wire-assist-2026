import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { runInNewContext } from 'node:vm';
import { describe, expect, it } from 'vitest';

describe('audio transport encoding', () => {
  it('downsamples 48 kHz to 16 kHz signed little-endian PCM in 100 ms packets', () => {
    let Recorder!: new () => { process: (input: Float32Array[][]) => boolean };
    const packets: ArrayBuffer[] = [];
    class Processor { port = { postMessage: (packet: ArrayBuffer) => packets.push(packet) }; }
    const source = readFileSync(resolve(process.cwd(), 'public/worklets/pcm-recorder.js'), 'utf8');
    runInNewContext(source, { AudioWorkletProcessor: Processor, sampleRate: 48000, registerProcessor: (_name: string, cls: typeof Recorder) => { Recorder = cls; } });
    const processor = new Recorder();
    processor.process([[new Float32Array(4800).fill(0.5)]]);
    expect(packets).toHaveLength(1);
    expect(packets[0].byteLength).toBe(3200);
    const view = new DataView(packets[0]);
    expect(view.getInt16(0, true)).toBe(16384);
    expect(view.getInt16(3198, true)).toBe(16384);
  });
  it('mixes multiple microphone channels to mono without duplicating packets', () => {
    let Recorder!: new () => { process: (input: Float32Array[][]) => boolean };
    const packets: ArrayBuffer[] = [];
    class Processor { port = { postMessage: (packet: ArrayBuffer) => packets.push(packet) }; }
    const source = readFileSync(resolve(process.cwd(), 'public/worklets/pcm-recorder.js'), 'utf8');
    runInNewContext(source, { AudioWorkletProcessor: Processor, sampleRate: 16000, registerProcessor: (_name: string, cls: typeof Recorder) => { Recorder = cls; } });
    const processor = new Recorder();
    processor.process([[new Float32Array(1600).fill(0.5), new Float32Array(1600).fill(-0.5)]]);
    processor.process([[]]);
    expect(packets).toHaveLength(1);
    expect(new DataView(packets[0]).getInt16(0, true)).toBe(0);
  });
});
