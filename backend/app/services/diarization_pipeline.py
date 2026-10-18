"""Nonblocking bounded batch scheduler, isolated from the live audio adapter."""
import asyncio
from dataclasses import dataclass, field

from app.services.audio_buffer import AudioBuffer
from app.services.diarization import GeminiDiarizationService, DiarizationUnavailable
from app.services.speaker_reconciliation import SpeakerReconciler


@dataclass
class Recording:
    session_id: str
    recording_id: str
    buffer: AudioBuffer
    reconciler: SpeakerReconciler
    queue: asyncio.Queue
    task: asyncio.Task | None = None
    pending: int = 0
    closed: bool = False
    errors: list = field(default_factory=list)


class DiarizationPipeline:
    def __init__(self, service):
        self.service = service
        self.settings = service.settings
        self.provider = GeminiDiarizationService(self.settings)
        self.recordings: dict[str, Recording] = {}

    def start(self, session, recording_id):
        state = Recording(session.session_id, recording_id,
                          AudioBuffer(recording_id, session.audio_clock_seconds,
                                      self.settings.batch_duration_seconds, self.settings.batch_overlap_seconds),
                          SpeakerReconciler(session, recording_id, session.audio_clock_seconds),
                          asyncio.Queue(maxsize=self.settings.max_pending_batches))
        self.recordings[recording_id] = state
        return state

    def feed(self, recording_id, pcm):
        state = self.recordings.get(recording_id)
        if state is None or state.closed:
            return
        for batch in state.buffer.append(pcm):
            self._enqueue(state, batch)
        self.service.get(state.session_id).audio_clock_seconds = state.buffer.end

    def _enqueue(self, state, batch):
        if state.pending >= self.settings.max_pending_batches:
            # Discard new audio instead of blocking the microphone or growing a queue.
            state.reconciler.accept(batch, [], error="backlog")
            state.errors[:] = ["Speaker identification is behind; an audio batch was skipped. Live transcription continues."]
        else:
            state.queue.put_nowait(batch)
            state.pending += 1
        if state.task is None or state.task.done():
            state.task = asyncio.create_task(self._work(state), name="batch-speaker-identification")

    def _active(self, state):
        session = self.service.sessions.get(state.session_id)
        return (not state.closed and session is not None and session.status == "active"
                and state.session_id not in self.service.ending)

    async def _work(self, state):
        session = self.service.get(state.session_id)
        try:
            while not state.queue.empty() and self._active(state):
                batch = state.queue.get_nowait()
                session.diarization_pending = state.pending
                session.diarization_notice = "Speaker identification processing"
                await self.service.publish(session, "diarization_status")
                try:
                    words = await self.provider.transcribe(batch)
                    if not self._active(state):
                        return
                    state.reconciler.accept(batch, words)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if not self._active(state):
                        return
                    message = str(error) if isinstance(error, DiarizationUnavailable) else "Speaker identification failed. Live transcription continues."
                    state.reconciler.accept(batch, [], error=message)
                    state.errors[:] = [message]
                finally:
                    state.pending -= 1
                    state.queue.task_done()
                    # Release this PCM batch before publishing results.
                    batch = None
                await self.service.reconcile_speakers(state.reconciler)
                if state.errors:
                    session.diarization_notice = state.errors.pop()
                    await self.service.publish(session, "diarization_error")
                else:
                    session.diarization_notice = ""
                session.diarization_pending = state.pending
                await self.service.publish(session, "diarization_status")
        except asyncio.CancelledError:
            raise
        except Exception:
            if self._active(state):
                session.diarization_pending = 0
                session.diarization_notice = "Speaker identification could not complete. Live transcription continues."
                await self.service.publish(session, "diarization_error")

    async def transcript_arrived(self, recording_id):
        state = self.recordings.get(recording_id)
        if state and self._active(state):
            await self.service.reconcile_speakers(state.reconciler)

    def flush(self, recording_id):
        state = self.recordings.get(recording_id)
        if state and self._active(state):
            batch = state.buffer.flush()
            if batch:
                self._enqueue(state, batch)

    async def stop_session(self, session_id):
        states = [state for state in self.recordings.values() if state.session_id == session_id]
        for state in states:
            state.closed = True
            state.buffer.clear()
            state.reconciler.clear()
            if state.task and not state.task.done():
                state.task.cancel()
            while not state.queue.empty():
                state.queue.get_nowait()
                state.queue.task_done()
        await asyncio.gather(*(s.task for s in states if s.task), return_exceptions=True)
        for state in states:
            state.pending = 0
            self.recordings.pop(state.recording_id, None)
        session = self.service.sessions.get(session_id)
        if session:
            session.diarization_pending = 0
            for segment in session.segments:
                if segment.diarization_status == "PENDING":
                    segment.diarization_status = "FAILED"
                    segment.revision += 1

    async def shutdown(self):
        for sid in {s.session_id for s in self.recordings.values()}:
            await self.stop_session(sid)
