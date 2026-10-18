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


async def test_audio_coordinator_final_only_and_cleanup(config):
    from app.schemas.contracts import AudioControl, CreateSession
    from app.services.audio import AudioCoordinator
    from app.services.member_data import MemberData
    from app.services.sessions import SessionService

    config.gemini_api_key = "fake-test-key"
    service = SessionService(config, MemberData(config.data_dir))
    session = service.create(CreateSession(member_id="M1002", consent=True, mode="live"))
    audio = AudioCoordinator(service)
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
    assert session.segments[0].speaker == "member"
    await audio.control(session.session_id, AudioControl(command="pause"), owner)
    assert adapter.closed and session.audio_status == "paused"
    assert not audio.streams
