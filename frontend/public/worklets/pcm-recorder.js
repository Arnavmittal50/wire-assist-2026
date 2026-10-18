// Browser-only audio transport. No audio is retained here or written to storage.
class PcmRecorder extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.phase = 0;
    this.sum = 0;
    this.count = 0;
  }
  process(inputs) {
    const channels = inputs[0];
    if (!channels || !channels.length) return true;
    const length = channels[0].length;
    const samples = new Int16Array(Math.ceil(length / this.ratio) + 1);
    let index = 0;
    for (let i = 0; i < length; i++) {
      let mono = 0;
      for (const channel of channels) mono += channel[i] || 0;
      this.sum += mono / channels.length;
      this.count++;
      this.phase++;
      if (this.phase >= this.ratio) {
        const sample = Math.max(-1, Math.min(1, this.sum / this.count));
        samples[index++] = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
        this.phase -= this.ratio;
        this.sum = 0;
        this.count = 0;
      }
    }
    if (index) {
      // Main-thread transport batches these render quanta into 100 ms packets.
      // Keeping the tail there permits immediate Stop without dropping its buffer.
      const packet = new ArrayBuffer(index * 2);
      const view = new DataView(packet);
      for (let j = 0; j < index; j++) view.setInt16(j * 2, samples[j], true);
      this.port.postMessage(packet, [packet]);
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PcmRecorder);
