"""Real Gemini smoke test with two locally synthesized 8 kHz WAVs; no phone call."""
import asyncio
import bisect
import json
import struct
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import ROOT, Settings
from app.services.transcription import create_transcription_adapter
from app.services.twilio_media import AudioTrack, MULAW


async def track_test(role, repeats=1):
    with wave.open(str(ROOT / '.tooling' / f'phase2-{role}.wav'), 'rb') as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (8000, 1, 2)
        source = wav.readframes(wav.getnframes())
    levels = sorted(set(MULAW))
    indices = {sample: MULAW.index(sample) for sample in levels}
    encoded = bytearray()
    for sample, in struct.iter_unpack('<h', source):
        i = bisect.bisect_left(levels, sample)
        near = min(levels[max(0, i-1):min(len(levels), i+1)], key=lambda n: abs(n-sample))
        encoded.append(indices[near])
    encoded.extend(b'\xff' * 16000)
    decoder = AudioTrack()
    results, errors = [], []
    began = time.perf_counter()

    async def transcript(text, finalized, **kwargs):
        if finalized:
            results.append({'text': text, 'elapsed_seconds': round(time.perf_counter()-began, 2),
                            'start_offset': str(kwargs.get('start_offset')), 'end_offset': str(kwargs.get('end_offset'))})
    async def error(message): errors.append(message)
    adapter = create_transcription_adapter(Settings(), transcript, error)
    try:
        await adapter.open()
        for turn in range(repeats):
            for i in range(0, len(encoded), 800):
                position = turn * len(encoded) + i
                for _, pcm, _ in decoder.push(position//800+1, position/8, encoded[i:i+800]):
                    await adapter.send(pcm)
                await asyncio.sleep(0.1)
            for _, pcm, _ in decoder.flush():
                await adapter.send(pcm)
            await adapter.end_turn()
            await asyncio.sleep(1)
        await adapter.finish()
    except Exception as failure:
        errors.append(type(failure).__name__)
    finally:
        await adapter.close()
    return {'role': role, 'finalized': results, 'errors': errors}


async def main():
    repeats = 2 if '--repeat' in sys.argv else 1
    rows = await asyncio.gather(track_test('teller', repeats), track_test('member', repeats))
    print(json.dumps(rows, indent=2))
    (ROOT / '.tooling' / 'phase2-transcription-results.json').write_text(json.dumps(rows, indent=2))
    return int(any(len(row['finalized']) < repeats or row['errors'] for row in rows))


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
