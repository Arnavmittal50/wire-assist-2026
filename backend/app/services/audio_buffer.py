"""Bounded sample-clock buffering and real PCM16 -> WAV container encoding."""
from dataclasses import dataclass
from io import BytesIO
import wave

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2


@dataclass(frozen=True)
class AudioBatch:
    recording_id: str
    index: int
    start: float
    end: float
    pcm: bytes


def pcm_to_wav(pcm: bytes) -> bytes:
    if not pcm or len(pcm) % 2:
        raise ValueError("Expected complete PCM16 samples")
    output = BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(pcm)
    return output.getvalue()


class AudioBuffer:
    def __init__(self, recording_id, start, duration=12, overlap=2):
        self.recording_id = recording_id
        self.origin = start
        self.size = round(duration * SAMPLE_RATE) * 2
        self.step = self.size - round(overlap * SAMPLE_RATE) * 2
        if not 0 < self.step <= self.size:
            raise ValueError("Invalid audio batch overlap")
        self.data = bytearray()
        self.offset = 0
        self.total = 0
        self.index = 0
        self.last_emitted_end = 0

    @property
    def end(self):
        return self.origin + self.total / BYTES_PER_SECOND

    def append(self, pcm):
        self.data.extend(pcm)
        self.total += len(pcm)
        result = []
        while len(self.data) >= self.size:
            result.append(self._batch(bytes(self.data[:self.size])))
            self.last_emitted_end = self.offset + self.size
            del self.data[:self.step]
            self.offset += self.step
        return result

    def _batch(self, pcm):
        batch = AudioBatch(self.recording_id, self.index,
                           self.origin + self.offset / BYTES_PER_SECOND,
                           self.origin + (self.offset + len(pcm)) / BYTES_PER_SECOND, pcm)
        self.index += 1
        return batch

    def flush(self):
        # Do not resend a tail containing only the previous batch's overlap.
        batch = self._batch(bytes(self.data)) if self.data and self.total > self.last_emitted_end else None
        self.data.clear()
        return batch

    def clear(self):
        self.data.clear()
