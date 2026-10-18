"""Own one provider stream per session; raw audio is never written to disk."""
import asyncio
import time
from contextlib import suppress
from fastapi import HTTPException

from app.schemas.contracts import AudioControl, TranscriptInput, uid
from app.services.transcription import create_transcription_adapter, TranscriptionUnavailable


class AudioCoordinator:
    def __init__(self, service):
        self.service = service
        self.streams: dict[str, dict] = {}
        self._draining: dict[str, asyncio.Task] = {}
        self._background: set[asyncio.Task] = set()
        self._shutdown = False
        self.factory = create_transcription_adapter

    async def control(self, session_id, request: AudioControl, owner=None):
        session = self.service.active(session_id)
        if request.command in {"pause", "stop"}:
            if not await self.stop(session_id, finalize=True, owner=owner):
                raise HTTPException(409, "Only the connection recording audio may control its microphone stream.")
            if session.status == "active":
                session.audio_status = "paused" if request.command == "pause" else "stopped"
                await self.service.publish(session, "session_status")
            return session
        if self._shutdown:
            raise HTTPException(409, "Audio processing is shutting down.")
        if session_id in self.streams or session_id in self._draining:
            raise HTTPException(409, "Microphone processing is already active.")
        if session.mode != "live" or not self.service.settings.gemini_api_key:
            raise HTTPException(409, "Live transcription requires configured Gemini access. Use manual text or a local microphone preview.")
        if owner is None:
            raise HTTPException(409, "Start audio through the authorized WebSocket connection.")

        # Reserve before the first await. Stream identity, not only session ID,
        # distinguishes a retiring recording from a later recording for this session.
        clock = time.monotonic()
        stream = {"owner": owner, "started": clock, "window": clock, "bytes": 0,
                  "stopping": False, "finalize": False, "open_task": None}

        async def on_transcript(text, finalized):
            if (self.streams.get(session_id) is not stream or session.status != "active"
                    or not isinstance(text, str) or not text.strip()):
                return
            if finalized:
                # Long utterances are explicitly split, with monotonically assigned segment IDs.
                for offset in range(0, len(text), 4000):
                    chunk = text[offset:offset + 4000].strip()
                    if not chunk:
                        continue
                    sequence = session.next_sequence
                    await self.service.transcript(session_id, TranscriptInput(
                        segment_id=uid(), sequence=sequence, text=chunk,
                        speaker=request.speaker), source="gemini")
            else:
                await self.service.emit(session_id, "transcript_partial", {"text": text[:4000], "speaker": request.speaker})

        async def on_error(message):
            if self.streams.get(session_id) is not stream:
                return
            session.audio_status = "stopped"
            await self.service.emit(session_id, "error", {"message": message, "code": "transcription_unavailable"})
            await self.service.publish(session, "session_status")
            # The adapter callback may itself run on the receive task.
            async def retire():
                if self.streams.get(session_id) is stream:
                    await self.stop(session_id, owner=owner)
            task = asyncio.create_task(retire(), name="audio-error-cleanup")
            self._background.add(task)
            task.add_done_callback(self._background.discard)

        adapter = self.factory(self.service.settings, on_transcript=on_transcript, on_error=on_error)
        stream["adapter"] = adapter
        self.streams[session_id] = stream
        stream["open_task"] = asyncio.create_task(adapter.open(), name="audio-provider-connect")
        try:
            await asyncio.shield(stream["open_task"])
            if session.status != "active" or self.streams.get(session_id) is not stream or stream["stopping"]:
                raise HTTPException(409, "Audio start was cancelled.")
        except asyncio.CancelledError:
            was_stopping = stream["stopping"]
            if self.streams.get(session_id) is stream:
                await self.stop(session_id, owner=owner)
            if was_stopping:
                raise HTTPException(409, "Audio start was cancelled.") from None
            raise
        except Exception:
            if self.streams.get(session_id) is stream:
                await self.stop(session_id, owner=owner)
            raise
        session.audio_status = "recording"
        await self.service.publish(session, "session_status")
        return session

    async def send(self, session_id, owner, pcm: bytes):
        session = self.service.active(session_id)
        stream = self.streams.get(session_id)
        if not stream or stream["owner"] is not owner or stream["stopping"] or session.audio_status != "recording":
            raise HTTPException(409, "Start transcription before sending audio.")
        if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2 or len(pcm) > self.service.settings.max_audio_bytes:
            raise ValueError("Audio must be bounded, even-length PCM16 mono chunks at 16 kHz.")
        clock = time.monotonic()
        if clock - stream["started"] > 590:
            raise TranscriptionUnavailable("The live demonstration reached its time limit. Stop and restart recording or use manual text.")
        if clock - stream["window"] >= 1:
            stream["window"] = clock
            stream["bytes"] = 0
        stream["bytes"] += len(pcm)
        if stream["bytes"] > 128000:
            raise ValueError("Audio arrived too quickly. Restart recording with the supported format.")
        await stream["adapter"].send(pcm)

    async def stop(self, session_id, finalize=False, owner=None) -> bool:
        """Coalesce concurrent stop/end calls and finish callbacks before return.

        False means the caller does not own this active stream. A cancelled
        caller cannot cancel the shared drain that another end request awaits.
        """
        stream = self.streams.get(session_id)
        if stream is not None and owner is not None and stream["owner"] is not owner:
            return False
        draining = self._draining.get(session_id)
        if stream is not None:
            stream["finalize"] = stream["finalize"] or finalize
            stream["stopping"] = True
            if draining is None:
                draining = asyncio.create_task(self._drain(session_id, stream), name="audio-provider-drain")
                self._draining[session_id] = draining
        if draining is not None:
            await asyncio.shield(draining)
        elif stream is None:
            session = self.service.sessions.get(session_id)
            if session:
                session.audio_status = "stopped"
        return True

    async def _drain(self, session_id, stream):
        adapter = stream["adapter"]
        opening = stream["open_task"]
        connected = False
        try:
            if opening is not None:
                if not opening.done():
                    opening.cancel()
                try:
                    await opening
                    connected = True
                except (asyncio.CancelledError, Exception):
                    pass
            try:
                if connected and stream["finalize"]:
                    await adapter.finish()
            except TranscriptionUnavailable as error:
                await self.service.emit(session_id, "error", {"message": str(error), "code": "transcription_unavailable"})
            except Exception:
                await self.service.emit(session_id, "error", {
                    "message": "Audio finalization failed. Review the transcript and enter any missing words manually.",
                    "code": "transcription_unavailable",
                })
        finally:
            with suppress(Exception):
                await adapter.close()
            if self.streams.get(session_id) is stream:
                self.streams.pop(session_id, None)
                session = self.service.sessions.get(session_id)
                if session:
                    session.audio_status = "stopped"
            if self._draining.get(session_id) is asyncio.current_task():
                self._draining.pop(session_id, None)

    async def shutdown(self):
        self._shutdown = True
        await asyncio.gather(*(self.stop(sid) for sid in set(self.streams) | set(self._draining)))
        while self._background:
            pending = tuple(self._background)
            await asyncio.gather(*pending, return_exceptions=True)
            self._background.difference_update(pending)
