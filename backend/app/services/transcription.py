"""Replaceable speech-to-text adapters; audio exists in memory only.

Google protocol reference (checked 2026-10-17):
https://ai.google.dev/gemini-api/docs/live-api/live-transcribe

The Live model accepts raw PCM, not a WAV header or MediaRecorder WebM chunks.
Interim hypotheses are previews; only provider-finalized text is committed.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from time import monotonic
from typing import Any, Awaitable, Callable, Protocol

from app.core.config import Settings


PCM_MIME_TYPE = "audio/pcm;rate=16000"
PCM_SAMPLE_RATE = 16000
MAX_LIVE_SECONDS = 590  # Leave room before Google's documented 10-minute limit.
TranscriptCallback = Callable[[str, bool], Awaitable[None]]
ErrorCallback = Callable[[str], Awaitable[None]]


class TranscriptionUnavailable(RuntimeError):
    """A safe user-facing failure; never includes raw provider error details."""


class TranscriptionAdapter(Protocol):
    provider: str

    async def open(self) -> None: ...
    async def send(self, pcm_bytes: bytes) -> None: ...
    async def finish(self) -> None: ...
    async def close(self) -> None: ...


class ManualTranscriptionAdapter:
    """Manual entry is handled by the normal finalized-transcript endpoint."""

    provider = "manual"
    notice = "Gemini transcription is not configured. Enter finalized text manually."

    async def open(self) -> None:
        raise TranscriptionUnavailable(self.notice)

    async def send(self, pcm_bytes: bytes) -> None:
        raise TranscriptionUnavailable(self.notice)

    async def finish(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def __aenter__(self) -> ManualTranscriptionAdapter:
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


def _make_client(*, api_key: str) -> Any:
    # Lazy construction keeps manual mode entirely independent of credentials.
    from google import genai

    return genai.Client(api_key=api_key)


class GeminiLiveTranscriptionAdapter:
    """One microphone recording backed by a server-owned Gemini Live session.

    Callbacks must finish quickly: enqueue downstream analysis instead of
    awaiting model reasoning here. The owner checks consent and assigns speaker
    labels; this transcription model does not support live diarization.
    """

    provider = "gemini"

    def __init__(
        self,
        settings: Settings,
        on_transcript: TranscriptCallback,
        on_error: ErrorCallback,
        *,
        client_factory: Callable[..., Any] = _make_client,
        drain_timeout_seconds: float = 5.0,
    ) -> None:
        self._settings = settings
        self._on_transcript = on_transcript
        self._on_error = on_error
        self._client_factory = client_factory
        self._timeout = max(0.1, settings.provider_timeout_seconds)
        self._drain_timeout = max(0.01, min(drain_timeout_seconds, self._timeout))
        self._client: Any = None
        self._context: Any = None
        self._session: Any = None
        self._receiver: asyncio.Task[None] | None = None
        self._drained = asyncio.Event()
        self._closing = False
        self._finishing = False
        self._opened = False
        self._started_at = 0.0
        self._sent_bytes = 0
        self._failure: str | None = None

    async def open(self) -> None:
        if self._opened or self._closing:
            raise TranscriptionUnavailable("Start a new microphone recording to reconnect transcription.")
        if not self._settings.gemini_api_key.strip():
            raise TranscriptionUnavailable(ManualTranscriptionAdapter.notice)
        try:
            from google.genai import types

            self._client = self._client_factory(api_key=self._settings.gemini_api_key)
            config = types.LiveConnectConfig(
                response_modalities=["TEXT"],
                input_audio_transcription=types.AudioTranscriptionConfig(),
            )
            self._context = self._client.aio.live.connect(
                model=self._settings.gemini_transcription_model,
                config=config,
            )
            self._session = await asyncio.wait_for(self._context.__aenter__(), self._timeout)
            self._opened = True
            self._started_at = monotonic()
            self._receiver = asyncio.create_task(self._receive_loop(), name="gemini-transcription")
        except asyncio.CancelledError:
            await self.close()
            raise
        except Exception:
            await self.close()
            raise TranscriptionUnavailable(
                "Gemini transcription could not connect. Check the backend key, model access, and quota; use manual text meanwhile."
            ) from None

    def _check_open(self) -> None:
        if self._failure:
            raise TranscriptionUnavailable(self._failure)
        if not self._opened or self._closing or self._session is None:
            raise TranscriptionUnavailable("Microphone transcription is not connected. Start recording again or use manual text.")

    async def send(self, pcm_bytes: bytes) -> None:
        self._check_open()
        if self._finishing:
            raise TranscriptionUnavailable("This audio recording is finishing. Start a new recording for more speech.")
        if not isinstance(pcm_bytes, bytes) or not pcm_bytes or len(pcm_bytes) % 2:
            raise ValueError("Audio must contain complete signed 16-bit PCM samples.")
        if len(pcm_bytes) > self._settings.max_audio_bytes:
            raise ValueError("Audio chunk exceeds the configured size limit.")
        if (monotonic() - self._started_at >= MAX_LIVE_SECONDS
                or self._sent_bytes + len(pcm_bytes) > MAX_LIVE_SECONDS * PCM_SAMPLE_RATE * 2):
            raise TranscriptionUnavailable("The live audio time limit was reached. Stop and start a new recording, or use manual text.")
        try:
            from google.genai import types

            await asyncio.wait_for(
                self._session.send_realtime_input(
                    audio=types.Blob(data=pcm_bytes, mime_type=PCM_MIME_TYPE)
                ),
                timeout=self._timeout,
            )
            self._sent_bytes += len(pcm_bytes)
        except asyncio.CancelledError:
            raise
        except Exception:
            message = "Gemini could not receive audio. Stop recording and enter missing speech manually."
            await self._notify_failure(message)
            raise TranscriptionUnavailable(message) from None

    async def finish(self) -> None:
        """Flush pending speech before close; never invent a final transcript."""
        self._check_open()
        if self._finishing:
            return
        self._finishing = True
        if not self._sent_bytes:
            return
        self._drained.clear()
        try:
            await asyncio.wait_for(
                self._session.send_realtime_input(audio_stream_end=True), self._timeout
            )
            await asyncio.wait_for(self._drained.wait(), self._drain_timeout)
        except asyncio.CancelledError:
            raise
        except Exception:
            message = "Final audio text could not be confirmed. Review the transcript and enter any missing speech manually."
            await self._notify_failure(message)
            raise TranscriptionUnavailable(message) from None
        if self._failure:
            raise TranscriptionUnavailable(self._failure)

    async def _receive_loop(self) -> None:
        try:
            while not self._closing:
                received = False
                # The SDK iterator stops after a complete turn, not a session.
                async for response in self._session.receive():
                    received = True
                    content = response.server_content
                    if content is None:
                        continue
                    interim = getattr(content, "interim_input_transcription", None)
                    final = getattr(content, "input_transcription", None)
                    if interim and isinstance(interim.text, str) and interim.text.strip():
                        await self._on_transcript(interim.text.strip(), False)
                    if final and isinstance(final.text, str) and final.text.strip():
                        await self._on_transcript(final.text.strip(), True)
                        if self._finishing:
                            self._drained.set()
                    if self._finishing and getattr(content, "turn_complete", False):
                        self._drained.set()
                if not received:
                    raise TranscriptionUnavailable("Transcription connection ended.")
        except asyncio.CancelledError:
            raise
        except Exception:
            if not self._closing:
                await self._notify_failure(
                    "Live transcription stopped. Review captured text and use manual entry or start recording again."
                )

    async def _notify_failure(self, message: str) -> None:
        first_failure = self._failure is None
        self._failure = self._failure or message
        self._drained.set()
        if first_failure:
            # A failed UI notification must not leave an unhandled task error.
            with suppress(Exception):
                await self._on_error(self._failure)

    async def close(self) -> None:
        """Idempotent cleanup, also safe after partial connection failure."""
        if self._closing:
            return
        self._closing = True
        self._drained.set()
        if self._receiver and self._receiver is not asyncio.current_task():
            self._receiver.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._receiver
        if self._context is not None and self._session is not None:
            with suppress(Exception):
                await asyncio.wait_for(self._context.__aexit__(None, None, None), self._timeout)
        if self._client is not None:
            with suppress(Exception):
                await asyncio.wait_for(self._client.aio.aclose(), self._timeout)
            with suppress(Exception):
                self._client.close()
        self._session = None
        self._context = None
        self._client = None

    async def __aenter__(self) -> GeminiLiveTranscriptionAdapter:
        await self.open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


def create_transcription_adapter(
    settings: Settings,
    on_transcript: TranscriptCallback,
    on_error: ErrorCallback,
) -> TranscriptionAdapter:
    if not settings.gemini_api_key.strip():
        return ManualTranscriptionAdapter()
    return GeminiLiveTranscriptionAdapter(settings, on_transcript, on_error)
