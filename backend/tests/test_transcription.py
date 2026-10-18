"""Contract tests use a fake SDK transport; no network or API credentials."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.config import Settings
from app.services.transcription import (
    GeminiLiveTranscriptionAdapter,
    ManualTranscriptionAdapter,
    PCM_MIME_TYPE,
    TranscriptionUnavailable,
    create_transcription_adapter,
)


def response(*, interim=None, final=None, complete=False):
    return SimpleNamespace(server_content=SimpleNamespace(
        interim_input_transcription=SimpleNamespace(text=interim) if interim is not None else None,
        input_transcription=SimpleNamespace(text=final) if final is not None else None,
        turn_complete=complete,
    ))


class FakeSession:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.sent = []
        self.receive_calls = 0
        self.send_error = None
        self.flush_response = response(final="Final recorded words.", complete=True)

    async def send_realtime_input(self, **kwargs):
        if self.send_error:
            raise self.send_error
        self.sent.append(kwargs)
        if kwargs.get("audio_stream_end") and self.flush_response:
            await self.messages.put(self.flush_response)

    async def receive(self):
        self.receive_calls += 1
        while True:
            message = await self.messages.get()
            if isinstance(message, Exception):
                raise message
            yield message
            if message.server_content and message.server_content.turn_complete:
                return


class FakeClient:
    def __init__(self):
        self.session = FakeSession()
        self.connect_args = None
        self.exited = False
        self.connect_error = None
        self.close = Mock()
        self.aio = SimpleNamespace(
            live=SimpleNamespace(connect=self.connect), aclose=AsyncMock()
        )

    @asynccontextmanager
    async def connect(self, **kwargs):
        self.connect_args = kwargs
        if self.connect_error:
            raise self.connect_error
        try:
            yield self.session
        finally:
            self.exited = True


def build_adapter(**kwargs):
    settings = Settings(_env_file=None, gemini_api_key="test-secret-never-print")
    client = FakeClient()
    factory = Mock(return_value=client)
    callback = AsyncMock()
    error_callback = AsyncMock()
    adapter = GeminiLiveTranscriptionAdapter(
        settings, callback, error_callback, client_factory=factory, **kwargs
    )
    return adapter, client, factory, callback, error_callback


async def until(predicate):
    async def check():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(check(), 1)


async def test_missing_key_is_manual_and_never_fabricates_text():
    callback = AsyncMock()
    adapter = create_transcription_adapter(
        Settings(_env_file=None, gemini_api_key=""), callback, AsyncMock()
    )
    assert isinstance(adapter, ManualTranscriptionAdapter)
    assert adapter.provider == "manual"
    with pytest.raises(TranscriptionUnavailable, match="manually"):
        await adapter.open()
    with pytest.raises(TranscriptionUnavailable):
        await adapter.send(b"\x00\x00")
    await adapter.finish()
    await adapter.close()
    callback.assert_not_called()


async def test_client_is_lazy_and_sdk_config_separates_transcription_model():
    adapter, client, factory, _, _ = build_adapter()
    factory.assert_not_called()
    async with adapter:
        factory.assert_called_once()
        assert client.connect_args["model"] == "gemini-3.5-transcribe-live"
        config = client.connect_args["config"]
        assert config.response_modalities == ["TEXT"]
        assert config.input_audio_transcription is not None
        await adapter.send(b"\x01\x00" * 1600)
        blob = client.session.sent[0]["audio"]
        assert blob.mime_type == PCM_MIME_TYPE
        assert len(blob.data) == 3200
    assert client.exited
    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()
    await adapter.close()
    client.close.assert_called_once()


async def test_interim_final_and_multiple_turns_keep_separate_semantics():
    adapter, client, _, callback, _ = build_adapter()
    async with adapter:
        await client.session.messages.put(response(interim=" They said "))
        await client.session.messages.put(response(final="They said send money.", complete=True))
        await client.session.messages.put(response(final="I have another question.", complete=True))
        await until(lambda: callback.await_count == 3)
        assert callback.await_args_list[0].args == ("They said", False)
        assert callback.await_args_list[1].args == ("They said send money.", True)
        assert callback.await_args_list[2].args == ("I have another question.", True)
        assert client.session.receive_calls >= 2


async def test_finish_flushes_final_text_before_cleanup():
    adapter, client, _, callback, _ = build_adapter()
    async with adapter:
        await adapter.send(b"\x00\x00" * 1600)
        await adapter.finish()
        assert client.session.sent[-1] == {"audio_stream_end": True}
        callback.assert_awaited_with("Final recorded words.", True)
        await adapter.finish()  # Idempotent EOF.
        assert len(client.session.sent) == 2
        with pytest.raises(TranscriptionUnavailable, match="finishing"):
            await adapter.send(b"\x00\x00")


@pytest.mark.parametrize(
    "invalid", [b"", b"\x00", "not bytes", b"\x00\x00" * 32001],
    ids=["empty", "partial-sample", "wrong-type", "oversized"],
)
async def test_invalid_pcm_is_rejected_before_transport(invalid):
    adapter, client, _, _, _ = build_adapter()
    async with adapter:
        with pytest.raises(ValueError):
            await adapter.send(invalid)
        assert client.session.sent == []


async def test_receive_failure_is_safe_and_requires_manual_fallback():
    adapter, client, _, callback, error_callback = build_adapter()
    async with adapter:
        await client.session.messages.put(RuntimeError("secret-key-private-provider-body"))
        await until(lambda: error_callback.await_count == 1)
        message = error_callback.await_args.args[0]
        assert "manual" in message
        assert "secret" not in message
        callback.assert_not_called()
        with pytest.raises(TranscriptionUnavailable) as caught:
            await adapter.send(b"\x00\x00")
        assert "secret" not in str(caught.value)


async def test_connection_failure_closes_client_and_redacts_provider_error():
    adapter, client, _, _, _ = build_adapter()
    client.connect_error = RuntimeError("test-secret-never-print")
    with pytest.raises(TranscriptionUnavailable, match="could not connect") as caught:
        await adapter.open()
    assert "test-secret" not in str(caught.value)
    client.aio.aclose.assert_awaited_once()
    client.close.assert_called_once()


async def test_send_failure_does_not_expose_credentials():
    adapter, client, _, _, errors = build_adapter()
    async with adapter:
        client.session.send_error = RuntimeError("test-secret-never-print")
        with pytest.raises(TranscriptionUnavailable) as caught:
            await adapter.send(b"\x00\x00")
        assert "test-secret" not in str(caught.value)
        errors.assert_awaited_once()


async def test_finish_timeout_does_not_claim_finalized_transcript():
    adapter, client, _, callback, errors = build_adapter(drain_timeout_seconds=0.01)
    client.session.flush_response = None
    async with adapter:
        await adapter.send(b"\x00\x00")
        with pytest.raises(TranscriptionUnavailable, match="could not be confirmed"):
            await adapter.finish()
        callback.assert_not_called()
        errors.assert_awaited_once()


async def test_empty_recording_finishes_without_sending_or_fabricating_text():
    adapter, client, _, callback, _ = build_adapter()
    async with adapter:
        await adapter.finish()
        assert client.session.sent == []
        callback.assert_not_called()


async def test_close_cancels_pending_receiver_without_error_notification():
    adapter, _, _, _, errors = build_adapter()
    await adapter.open()
    receiver = adapter._receiver
    await adapter.close()
    assert receiver.done()
    errors.assert_not_called()


async def test_time_limit_requests_a_new_recording():
    adapter, client, _, _, _ = build_adapter()
    async with adapter:
        adapter._started_at -= 600
        with pytest.raises(TranscriptionUnavailable, match="time limit"):
            await adapter.send(b"\x00\x00")
        assert client.session.sent == []
