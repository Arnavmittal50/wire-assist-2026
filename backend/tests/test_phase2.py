def create(client, body):
    return client.post("/sessions", json=body).json()["session_id"]


def test_manual_final_idempotent_and_reordering(client, session_body):
    sid = create(client, session_body)
    route = f"/sessions/{sid}/transcript"
    latest = {"segment_id": "last", "sequence": 2, "text": "My investment transfer.", "speaker": "member"}
    assert client.post(route, json=latest).status_code == 200
    assert len(client.post(route, json=latest).json()["segments"]) == 1
    earlier = {**latest, "segment_id": "first", "sequence": 1, "text": "Hello."}
    assert [s["segment_id"] for s in client.post(route, json=earlier).json()["segments"]] == ["first", "last"]
    assert client.post(route, json={**latest, "text": "Changed"}).status_code == 409
    assert client.post(route, json={**latest, "segment_id": "collision"}).status_code == 409
    assert client.post(route, json={**latest, "sequence": 4, "segment_id": "blank", "text": " "}).status_code == 422


def test_socket_transcript_and_missing_audio_provider(client, session_body, config):
    sid = create(client, session_body)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "authenticate", "token": config.dev_auth_token})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "audio_control", "command": "start", "speaker": "member"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert "manual" in error["payload"]["message"].lower()
        assert ws.receive_json()["payload"]["session"]["audio_status"] == "stopped"
        ws.send_json({"type": "transcript_submit", "segment_id": "manual", "sequence": 1, "text": "Hello", "speaker": "member"})
        event = ws.receive_json()
        assert event["type"] == "transcript_final"
        assert event["payload"]["segment"]["finalized"] is True


def test_invalid_control_is_not_reported_as_a_pcm_format_problem(client, session_body, config):
    sid = create(client, session_body)
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "authenticate", "token": config.dev_auth_token})
        ws.receive_json(); ws.receive_json()
        ws.send_json({"type": "audio_control", "command": "start", "speaker": "invalid-speaker"})
        error = ws.receive_json()
        assert error["payload"]["code"] == "invalid_audio_control"
        assert "PCM" not in error["payload"]["message"]
        assert "auto" in error["payload"]["message"]
        assert ws.receive_json()["payload"]["session"]["audio_status"] == "stopped"


def test_binary_packet_error_is_distinct_and_auto_mode_can_restart(client, session_body, config):
    config.gemini_api_key = "fake-test-key"
    # Keep this transport test offline; classification is tested separately.
    client.app.state.service.analysis._settings = config.model_copy(update={"gemini_api_key": ""})
    received = []
    class Adapter:
        async def open(self): pass
        async def send(self, pcm): received.append(pcm)
        async def finish(self): pass
        async def close(self): pass
    client.app.state.audio.factory = lambda *args, **kwargs: Adapter()
    sid = create(client, {**session_body, "mode": "live"})
    with client.websocket_connect(f"/ws/sessions/{sid}") as ws:
        ws.send_json({"type": "authenticate", "token": config.dev_auth_token})
        ws.receive_json(); ws.receive_json()
        def next_matching(predicate):
            while True:
                message = ws.receive_json()
                if predicate(message):
                    return message

        ws.send_json({"type": "audio_control", "command": "start", "speaker": "auto"})
        assert next_matching(lambda m: m["payload"].get("session", {}).get("audio_status") == "recording")
        ws.send_bytes(b'\x00')  # Incomplete 16-bit sample.
        error = next_matching(lambda m: m["type"] == "error")
        assert error["payload"]["code"] == "invalid_audio_format"
        assert "PCM16" in error["payload"]["message"]
        assert ws.receive_json()["payload"]["session"]["audio_status"] == "stopped"
        assert received == []
        ws.send_json({"type": "audio_control", "command": "start", "speaker": "auto"})
        assert next_matching(lambda m: m["payload"].get("session", {}).get("audio_status") == "recording")
        packet = b'\x00\x00' * 1600
        ws.send_bytes(packet)
        ws.send_json({"type": "ping"})
        assert next_matching(lambda m: m["type"] == "connection_status")
        assert received == [packet]


async def test_audio_coordinator_final_only_and_cleanup(config):
    from app.schemas.contracts import AudioControl, CreateSession
    from app.services.audio import AudioCoordinator
    from app.services.member_data import MemberData
    from app.services.sessions import SessionService

    config.gemini_api_key = "fake-test-key"
    service = SessionService(config, MemberData(config.data_dir))
    session = service.create(CreateSession(member_id="M1002", consent=True, mode="live"))
    audio = AudioCoordinator(service)
    from unittest.mock import AsyncMock
    audio.diarization.provider.transcribe = AsyncMock(return_value=[])
    class FakeAdapter:
        closed = False
        async def open(self):
            pass
        async def send(self, pcm):
            await self.on_transcript("unfinished", False)
            assert not session.segments
            await self.on_transcript("This is the final speech.", True)
        async def finish(self):
            pass
        async def close(self):
            self.closed = True
    adapter = FakeAdapter()
    def factory(settings, *, on_transcript, on_error):
        adapter.on_transcript = on_transcript
        return adapter
    audio.factory = factory
    owner = object()
    await audio.control(session.session_id, AudioControl(command="start", speaker="member"), owner)
    await audio.send(session.session_id, owner, b"\x00\x00" * 1600)
    assert session.segments[0].text == "This is the final speech."
    assert session.segments[0].source == "gemini"
    assert session.segments[0].speaker == "unknown"
    assert session.segments[0].diarization_status == "PENDING"
    await audio.control(session.session_id, AudioControl(command="pause"), owner)
    assert adapter.closed and session.audio_status == "paused"
    assert not audio.streams
