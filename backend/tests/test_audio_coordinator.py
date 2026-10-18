"""Concurrency tests use a controllable adapter; no real microphone or provider."""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.schemas.contracts import AudioControl, Payment, Segment, Session
from app.services.audio import AudioCoordinator
from app.services.transcription import TranscriptionUnavailable


class Service:
    def __init__(self):
        self.settings = Settings(_env_file=None, gemini_api_key="fake-test-key")
        self.session = Session(
            member_id="M1002", as_of=datetime(2026, 9, 27, tzinfo=timezone.utc),
            consent=True, retain_transcript=True, mode="live", payment=Payment(), data_source="test",
        )
        self.sessions = {self.session.session_id: self.session}
        self.emit = AsyncMock()
        self.publish = AsyncMock()

    def active(self, session_id):
        session = self.sessions[session_id]
        if session.status != "active":
            raise HTTPException(409, "Ended")
        return session

    async def transcript(self, session_id, request, source="manual"):
        session = self.active(session_id)
        assert request.sequence not in [s.sequence for s in session.segments]
        session.segments.append(Segment(**request.model_dump(), source=source))
        session.next_sequence = max(session.next_sequence, request.sequence + 1)


class Adapter:
    def __init__(self, on_transcript, on_error):
        self.on_transcript = on_transcript
        self.on_error = on_error
        self.open_gate = asyncio.Event()
        self.open_gate.set()
        self.open_entered = asyncio.Event()
        self.open_cancelled = False
        self.finish_gate = asyncio.Event()
        self.finish_gate.set()
        self.finish_entered = asyncio.Event()
        self.final_text = "Final speech before ending."
        self.finish_error = None
        self.finish_calls = 0
        self.close_calls = 0
        self.sent = []

    async def open(self):
        self.open_entered.set()
        try:
            await self.open_gate.wait()
        except asyncio.CancelledError:
            self.open_cancelled = True
            raise

    async def send(self, pcm):
        self.sent.append(pcm)

    async def finish(self):
        self.finish_calls += 1
        self.finish_entered.set()
        await self.finish_gate.wait()
        if self.finish_error:
            raise self.finish_error
        if self.final_text:
            await self.on_transcript(self.final_text, True)

    async def close(self):
        self.close_calls += 1


def setup(*, blocked_open=False, blocked_finish=False):
    service = Service()
    coordinator = AudioCoordinator(service)
    adapters = []
    def factory(settings, on_transcript, on_error):
        adapter = Adapter(on_transcript, on_error)
        if blocked_open:
            adapter.open_gate.clear()
        if blocked_finish:
            adapter.finish_gate.clear()
        adapters.append(adapter)
        return adapter
    coordinator.factory = factory
    return coordinator, service, adapters, service.session.session_id, object()


async def tick_until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 1)


async def start(coordinator, sid, owner):
    return await coordinator.control(sid, AudioControl(command="start", speaker="member"), owner)


async def test_concurrent_stop_and_end_await_same_finalized_text():
    coordinator, service, adapters, sid, owner = setup(blocked_finish=True)
    await start(coordinator, sid, owner)
    adapter = adapters[0]
    stopping = asyncio.create_task(coordinator.control(sid, AudioControl(command="stop"), owner))
    await adapter.finish_entered.wait()
    ending = asyncio.create_task(coordinator.stop(sid, finalize=True))
    await asyncio.sleep(0)
    assert not stopping.done() and not ending.done()
    assert sid in coordinator.streams and sid in coordinator._draining
    adapter.finish_gate.set()
    await asyncio.gather(stopping, ending)
    assert service.session.segments[0].text == adapter.final_text
    assert adapter.finish_calls == 1 and adapter.close_calls == 1
    assert sid not in coordinator.streams and sid not in coordinator._draining


async def test_cancelling_one_stop_caller_does_not_cancel_shared_drain():
    coordinator, service, adapters, sid, owner = setup(blocked_finish=True)
    await start(coordinator, sid, owner)
    stop1 = asyncio.create_task(coordinator.stop(sid, finalize=True, owner=owner))
    await adapters[0].finish_entered.wait()
    stop2 = asyncio.create_task(coordinator.stop(sid, finalize=True))
    stop1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop1
    adapters[0].finish_gate.set()
    await stop2
    assert len(service.session.segments) == 1
    assert adapters[0].close_calls == 1


async def test_wrong_owner_cannot_stop_pause_send_or_mutate_status():
    coordinator, service, adapters, sid, owner = setup()
    await start(coordinator, sid, owner)
    stranger = object()
    assert await coordinator.stop(sid, owner=stranger) is False
    for command in ["pause", "stop"]:
        with pytest.raises(HTTPException):
            await coordinator.control(sid, AudioControl(command=command), stranger)
    with pytest.raises(HTTPException):
        await coordinator.send(sid, stranger, b"\x00\x00")
    assert service.session.audio_status == "recording"
    assert adapters[0].finish_calls == 0 and adapters[0].close_calls == 0
    await coordinator.shutdown()


async def test_duplicate_start_is_rejected_while_opening_and_stop_cancels_open():
    coordinator, service, adapters, sid, owner = setup(blocked_open=True)
    opening = asyncio.create_task(start(coordinator, sid, owner))
    await tick_until(lambda: bool(adapters))
    await adapters[0].open_entered.wait()
    with pytest.raises(HTTPException, match="409"):
        await start(coordinator, sid, owner)
    await coordinator.stop(sid, owner=owner)
    with pytest.raises(HTTPException) as caught:
        await opening
    assert caught.value.detail == "Audio start was cancelled."
    assert adapters[0].open_cancelled and adapters[0].close_calls == 1
    assert not coordinator.streams and service.session.audio_status == "stopped"


async def test_cancelled_start_cleans_reserved_provider_connection():
    coordinator, _, adapters, sid, owner = setup(blocked_open=True)
    opening = asyncio.create_task(start(coordinator, sid, owner))
    await tick_until(lambda: bool(adapters))
    await adapters[0].open_entered.wait()
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await opening
    assert adapters[0].open_cancelled and adapters[0].close_calls == 1
    assert not coordinator.streams and not coordinator._draining


async def test_final_chunks_have_unique_ids_and_do_not_deduplicate_repeated_speech():
    coordinator, service, adapters, sid, owner = setup()
    await start(coordinator, sid, owner)
    adapter = adapters[0]
    await adapter.on_transcript("Yes.", False)
    assert not service.session.segments
    await adapter.on_transcript("Yes.", True)
    await adapter.on_transcript("Yes.", True)
    await adapter.on_transcript("x" * 4000 + " " * 4000 + "last words", True)
    assert [s.text for s in service.session.segments] == ["Yes.", "Yes.", "x" * 4000, "last words"]
    assert [s.sequence for s in service.session.segments] == [0, 1, 2, 3]
    assert len({s.segment_id for s in service.session.segments}) == 4
    assert all(s.speaker == "member" and s.source == "gemini" for s in service.session.segments)
    await coordinator.shutdown()


async def test_new_audio_respects_persisted_sequence_when_transcript_was_not_retained():
    coordinator, service, adapters, sid, owner = setup()
    service.session.next_sequence = 7
    await start(coordinator, sid, owner)
    await adapters[0].on_transcript("New speech after reconnect.", True)
    assert service.session.segments[0].sequence == 7
    assert service.session.next_sequence == 8
    await coordinator.shutdown()


async def test_late_callback_from_old_stream_cannot_affect_new_stream():
    coordinator, service, adapters, sid, owner = setup()
    await start(coordinator, sid, owner)
    old = adapters[0]
    await coordinator.stop(sid, owner=owner)
    await start(coordinator, sid, owner)
    await old.on_transcript("Stale words", True)
    await old.on_error("Stale error")
    await asyncio.sleep(0)
    assert not service.session.segments
    assert service.session.audio_status == "recording"
    assert adapters[1].close_calls == 0
    await coordinator.shutdown()


async def test_receive_error_schedules_cleanup_without_self_await_deadlock():
    coordinator, service, adapters, sid, owner = setup()
    await start(coordinator, sid, owner)
    await asyncio.wait_for(adapters[0].on_error("Safe provider failure"), 1)
    await tick_until(lambda: sid not in coordinator.streams)
    assert service.session.audio_status == "stopped" and adapters[0].close_calls == 1
    await coordinator.shutdown()
    assert not coordinator._background


async def test_receive_error_during_finalization_does_not_duplicate_close_or_deadlock():
    coordinator, _, adapters, sid, owner = setup(blocked_finish=True)
    await start(coordinator, sid, owner)
    adapter = adapters[0]
    stopping = asyncio.create_task(coordinator.stop(sid, finalize=True, owner=owner))
    await adapter.finish_entered.wait()
    await adapter.on_error("Safe failure")
    adapter.finish_error = TranscriptionUnavailable("Final text unavailable")
    adapter.finish_gate.set()
    await asyncio.wait_for(stopping, 1)
    await coordinator.shutdown()
    assert adapter.close_calls == 1 and adapter.finish_calls == 1
    assert not coordinator._draining and not coordinator._background


@pytest.mark.parametrize("pcm", [b"", b"\x01", "invalid", b"\x00" * 64002], ids=["empty", "odd", "nonbytes", "large"])
async def test_malformed_audio_never_reaches_provider(pcm):
    coordinator, _, adapters, sid, owner = setup()
    await start(coordinator, sid, owner)
    with pytest.raises(ValueError):
        await coordinator.send(sid, owner, pcm)
    assert adapters[0].sent == []
    await coordinator.shutdown()


async def test_shutdown_waits_for_existing_drain_and_prevents_new_starts():
    coordinator, service, adapters, sid, owner = setup(blocked_finish=True)
    await start(coordinator, sid, owner)
    stopping = asyncio.create_task(coordinator.stop(sid, finalize=True))
    await adapters[0].finish_entered.wait()
    shutdown = asyncio.create_task(coordinator.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    adapters[0].finish_gate.set()
    await asyncio.gather(stopping, shutdown)
    assert service.session.segments and not coordinator.streams and not coordinator._draining
    with pytest.raises(HTTPException):
        await start(coordinator, sid, owner)
