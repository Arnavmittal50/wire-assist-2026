"""Gemini Interactions transcription adapter. No temporary/provider files.

Wire format verified against google-genai 2.31.0 and:
https://ai.google.dev/gemini-api/docs/transcribe
Short WAV batches are inline and interactions use store=False.
"""
import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass
import math

from app.services.audio_buffer import pcm_to_wav


@dataclass(frozen=True)
class SpokenWord:
    text: str
    speaker: str | None
    start: float | None
    end: float | None


class DiarizationUnavailable(RuntimeError):
    pass


def offset_seconds(value):
    if value is None:
        return None
    try:
        number = float(str(value).removesuffix("s"))
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def parse_annotations(interaction, batch):
    words = []
    for step in field(interaction, "steps", []) or []:
        for content in field(step, "content", []) or []:
            for annotation in field(content, "annotations", []) or []:
                if field(annotation, "type") != "word_info":
                    continue
                text = field(annotation, "text", "")
                speaker = field(annotation, "speaker")
                start = offset_seconds(field(annotation, "start_offset"))
                end = offset_seconds(field(annotation, "end_offset"))
                if not isinstance(text, str) or not text.strip():
                    continue
                if not isinstance(speaker, str) or not speaker or len(speaker) > 100:
                    speaker = None
                if start is not None and end is not None:
                    if end < start or end > batch.end - batch.start + .5:
                        continue
                    start, end = batch.start + start, batch.start + end
                else:
                    start = end = None
                words.append(SpokenWord(text, speaker, start, end))
                if len(words) > 3000:
                    raise DiarizationUnavailable("Speaker identification returned too many annotations.")
    return words


def make_client(api_key):
    from google import genai
    from google.genai import types
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=30000, retry_options=types.HttpRetryOptions(attempts=1)))


class GeminiDiarizationService:
    def __init__(self, settings, client_factory=make_client):
        self.settings = settings
        self.client_factory = client_factory

    async def transcribe(self, batch):
        if not self.settings.gemini_api_key.strip():
            raise DiarizationUnavailable("Speaker identification needs a backend Gemini API key. Live text remains available.")
        client = None
        try:
            client = self.client_factory(self.settings.gemini_api_key)
            mode = {"type": "verbatim", "diarization_mode": "speaker"}
            if self.settings.diarization_word_timestamps:
                mode["timestamp_granularities"] = ["word"]
            result = await asyncio.wait_for(client.aio.interactions.create(
                model=self.settings.gemini_diarization_model,
                input=[{"type": "audio", "mime_type": "audio/wav",
                        "data": base64.b64encode(pcm_to_wav(batch.pcm)).decode("ascii")}],
                generation_config={"transcription_config": {"mode": mode}},
                store=False,
            ), timeout=self.settings.provider_timeout_seconds)
            if field(result, "status") not in {None, "completed"}:
                raise DiarizationUnavailable("Speaker identification did not complete. Live transcription continues.")
            return parse_annotations(result, batch)
        except asyncio.CancelledError:
            raise
        except DiarizationUnavailable:
            raise
        except Exception as error:
            code = getattr(error, "code", None) or getattr(error, "status_code", None)
            reason = {400: "configuration rejected", 401: "authentication failed", 403: "account lacks model access",
                      404: "model unavailable for this account", 429: "quota or request limit reached",
                      503: "provider temporarily unavailable"}.get(code, "request timed out or connection failed")
            suffix = f" (HTTP {code})" if isinstance(code, int) else ""
            raise DiarizationUnavailable(
                f"Speaker identification: {self.settings.gemini_diarization_model}: {reason}{suffix}. "
                "Live transcription continues; labels can be corrected manually.") from None
        finally:
            if client is not None:
                with suppress(Exception):
                    await asyncio.wait_for(client.aio.aclose(), 5)
                with suppress(Exception):
                    client.close()
