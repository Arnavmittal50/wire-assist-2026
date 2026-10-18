"""Opt-in Gemini check: consented PCM16 mono 16 kHz WAV, or synthetic silence.

Prints only timing/label counts, never audio or transcript contents.
Silence verifies model access, not speaker-identification accuracy.
"""
import argparse
import asyncio
from pathlib import Path
import sys
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings
from app.services.audio_buffer import AudioBatch
from app.services.diarization import GeminiDiarizationService


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audio', type=Path)
    parser.add_argument('--consented', action='store_true')
    parser.add_argument('--probe-silence', action='store_true')
    args = parser.parse_args()
    if args.audio and args.consented and not args.probe_silence:
        with wave.open(str(args.audio)) as recording:
            if (recording.getnchannels(), recording.getsampwidth(), recording.getframerate()) != (1, 2, 16000):
                parser.error('Use PCM16 mono at 16 kHz.')
            if recording.getnframes() > 30 * 16000:
                parser.error('Use a recording of at most 30 seconds.')
            pcm = recording.readframes(recording.getnframes())
    elif args.probe_silence and not args.audio:
        pcm = b'\x00\x00' * 16000
    else:
        parser.error('Choose --probe-silence or --audio PATH --consented.')
    started = time.monotonic()
    words = await GeminiDiarizationService(Settings()).transcribe(AudioBatch('integration', 0, 0, len(pcm) / 32000, pcm))
    speakers = {word.speaker for word in words if word.speaker}
    print(f'Gemini responded in {time.monotonic() - started:.1f}s; {len(words)} word annotations; {len(speakers)} distinct labels.')
    if args.probe_silence:
        print('Model access verified only. No speech accuracy claim.')
    else:
        assert len(speakers) >= 2, 'Recording did not produce two distinct speaker labels.'
        assert all(word.start is not None and word.end is not None for word in words), 'Missing alignment timestamps.'
        print('Two labels and timestamps returned. Manually compare attribution with the consented recording.')


if __name__ == '__main__':
    asyncio.run(main())
