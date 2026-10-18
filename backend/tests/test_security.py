from app.services.analysis import AnalysisService
"""Boundary and lifecycle integration checks not covered by provider unit tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from app.schemas.contracts import AnalysisResult


def authenticate(socket, config):
    socket.send_json({"type": "authenticate", "token": config.dev_auth_token})
    assert socket.receive_json()["type"] == "connection_status"
    assert socket.receive_json()["type"] == "session_status"


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/sessions", None),
    ("GET", "/sessions/unknown", None),
    ("GET", "/sessions/unknown/insights", None),
    ("GET", "/sessions/unknown/summary", None),
    ("GET", "/members/M1002/events", None),
    ("POST", "/sessions", {"member_id": "M1002", "consent": True}),
    ("POST", "/sessions/unknown/transcript", {"sequence": 0, "text": "unauthorized input"}),
    ("POST", "/sessions/unknown/actions", {"action_type": "NO_ACTION"}),
    ("POST", "/sessions/unknown/audio", {"command": "start"}),
    ("POST", "/sessions/unknown/end", None),
    ("DELETE", "/sessions/unknown", None),
])
def test_protected_routes_deny_bad_auth_before_session_lookup(client, method, path, body):
    response = client.request(method, path, json=body, headers={"Authorization": "Bearer invalid"})
    assert response.status_code == 401
    assert "unknown" not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert client.app.state.service.sessions == {}


def test_non_ascii_authorization_fails_closed_without_server_error(client):
    response = client.get("/members", headers=[(b"authorization", b"Bearer \xff")])
    assert response.status_code == 401


def test_cors_preflight_only_allows_configured_local_origin(client):
    headers = {"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "authorization,content-type"}
    approved = client.options("/sessions", headers=headers)
    assert approved.status_code == 200
    assert approved.headers["Access-Control-Allow-Origin"] == headers["Origin"]
    rejected = client.options("/sessions", headers={**headers, "Origin": "https://unrelated.example"})
    assert rejected.status_code == 400
    assert "Access-Control-Allow-Origin" not in rejected.headers


@pytest.mark.parametrize("payload", ["not JSON", "[]", "null", '{"type":"authenticate","token":3}',
                                    '{"type":"authenticate","token":"bad"}',
                                    '{"type":"authenticate","token":"é"}', "x" * 1025])
def test_malformed_websocket_auth_closes_without_session_disclosure(client, session_body, payload):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as socket:
        socket.send_text(payload)
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 4401
    assert not client.app.state.service.connections.get(sid)


def test_binary_auth_handshake_is_rejected(client, session_body):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as socket:
        socket.send_bytes(b"\x00\x00")
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 4401


def test_websocket_origin_and_missing_session_fail_before_snapshot(client, session_body, config):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    with pytest.raises(WebSocketDisconnect) as failure:
        with client.websocket_connect(f"/ws/sessions/{sid}", headers={"origin": "https://unrelated.example"}):
            pass
    assert failure.value.code == 4403
    with client.websocket_connect("/ws/sessions/missing") as socket:
        socket.send_json({"type": "authenticate", "token": config.dev_auth_token})
        with pytest.raises(WebSocketDisconnect) as missing:
            socket.receive_json()
        assert missing.value.code == 4401


def test_websocket_invalid_message_and_oversize_leave_transcript_unchanged(client, session_body, config):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as socket:
        authenticate(socket, config)
        socket.send_json(["transcript_submit", "invalid structure"])
        assert socket.receive_json()["type"] == "error"
        assert socket.receive_json()["type"] == "session_status"
        socket.send_text("x" * 8193)
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1009
    assert client.get(f"/sessions/{sid}").json()["segment_count"] == 0


def test_websocket_text_rate_limit_is_enforced(client, session_body, config):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as socket:
        authenticate(socket, config)
        for _ in range(30):
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "connection_status"
        socket.send_json({"type": "ping"})
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 1008


def test_http_payload_limit_prevents_session_mutation(client):
    response = client.post("/sessions", content=b"x" * 32769, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert client.app.state.service.sessions == {}


def test_http_request_rate_limit_bounds_repeated_requests(client):
    responses = [client.get("/health").status_code for _ in range(241)]
    assert responses[:240] == [200] * 240
    assert responses[-1] == 429


def test_delete_with_open_websocket_cannot_resurrect_session(client, session_body, config):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    with client.websocket_connect(f"/ws/sessions/{sid}") as socket:
        authenticate(socket, config)
        assert client.delete(f"/sessions/{sid}").status_code == 204
        with pytest.raises(WebSocketDisconnect) as failure:
            socket.receive_json()
        assert failure.value.code == 4404
    service = client.app.state.service
    assert client.get(f"/sessions/{sid}").status_code == 404
    assert sid not in service.sessions
    assert not service.connections.get(sid)
    assert all(saved.session_id != sid for saved in service.repository.load_all())


def test_nonretained_end_purges_live_memory_and_all_browser_views(client, session_body, config):
    marker = "privacy-boundary-7741"
    sid = client.post("/sessions", json={**session_body, "retain_transcript": False}).json()["session_id"]
    route = f"/sessions/{sid}"
    response = client.post(route + "/transcript", json={"segment_id": "secret", "sequence": 0,
                "speaker": "member", "text": f"Please send today. {marker}"})
    assert response.status_code == 200
    assert marker in response.text  # Available during the consented active review.
    client.post(route + "/actions", json={"action_type": "OFFER_VERIFICATION", "note": marker})
    ended = client.post(route + "/end")
    assert ended.status_code == 200
    assert marker not in ended.text
    assert ended.json()["segments"] == []
    assert ended.json()["segment_count"] == 1
    for endpoint in [route, route + "/summary", route + "/insights", "/sessions"]:
        assert marker not in client.get(endpoint).text
    assert marker not in client.app.state.service.sessions[sid].model_dump_json()
    assert marker.encode() not in config.database_path.read_bytes()


def test_human_action_wins_when_pending_provider_ignores_cancellation(client, session_body):
    began = threading.Event()
    finished = threading.Event()

    class SlowAnalysis(AnalysisService):
        async def analyze(self, snapshot, events):
            began.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                finished.set()
                return AnalysisResult(), "gemini", "stale response must never overwrite the decision"

    service = client.app.state.service
    service.analysis = SlowAnalysis(service.settings)
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    client.post(f"/sessions/{sid}/transcript", json={"sequence": 0, "text": "A finalized turn."})
    assert began.wait(2)
    result = client.post(f"/sessions/{sid}/actions", json={"action_type": "MARK_LEGITIMATE",
                         "note": "Recipient independently verified and member wishes to proceed."})
    assert result.status_code == 200
    assert finished.wait(2)
    state = client.get(f"/sessions/{sid}").json()
    assert state["review_outcome"] == "legitimate"
    assert state["attention"]["score"] == 0
    assert state["analysis_pending"] is False
    assert "stale response" not in state["provider_notice"]


def test_end_in_progress_rejects_new_user_evidence_and_actions(client, session_body):
    began, ending = threading.Event(), threading.Event()
    release = asyncio.Event()

    class SlowAnalysis(AnalysisService):
        async def analyze(self, snapshot, events):
            began.set()
            await release.wait()
            return AnalysisResult(), "demo", "completed pending turn"

    service, audio = client.app.state.service, client.app.state.audio
    service.analysis = SlowAnalysis(service.settings)
    original_stop = audio.stop

    async def observed_stop(*args, **kwargs):
        if kwargs.get("finalize"):
            ending.set()
        return await original_stop(*args, **kwargs)

    audio.stop = observed_stop
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    route = f"/sessions/{sid}"
    client.post(route + "/transcript", json={"sequence": 0, "text": "Original finalized turn."})
    assert began.wait(2)
    with ThreadPoolExecutor(max_workers=1) as workers:
        pending = workers.submit(client.post, route + "/end")
        try:
            assert ending.wait(2)
            assert client.post(route + "/transcript", json={"sequence": 1, "text": "Too late to add."}).status_code == 409
            assert client.post(route + "/actions", json={"action_type": "NO_ACTION"}).status_code == 409
        finally:
            client.portal.call(release.set)
        ended = pending.result(timeout=3)
    assert ended.status_code == 200
    assert ended.json()["status"] == "ended"
    assert ended.json()["segment_count"] == 1


def test_authorized_fake_live_flow_drains_final_audio_before_end(config, session_body):
    config.gemini_api_key = "fake-backend-secret-never-for-client"
    app = create_app(config)
    adapters = []

    class FakeAnalysis(AnalysisService):
        async def initialize(self, snapshot, events):
            return await super().initialize(snapshot.model_copy(update={"mode": "demo"}), events)

        async def analyze(self, snapshot, events):
            return AnalysisResult(), "gemini", "Fake provider transport in integration test."

    class FakeAudio:
        def __init__(self, settings, on_transcript, on_error):
            self.on_transcript, self.packets, self.closed = on_transcript, [], False
            adapters.append(self)

        async def open(self):
            pass

        async def send(self, pcm):
            self.packets.append(pcm)

        async def finish(self):
            await self.on_transcript("This is the finalized fake provider turn.", True)

        async def close(self):
            self.closed = True

    app.state.audio.factory = FakeAudio
    app.state.service.analysis = FakeAnalysis(config)
    with TestClient(app, headers={"Authorization": "Bearer " + config.dev_auth_token}) as client:
        assert config.gemini_api_key not in client.get("/health").text
        sid = client.post("/sessions", json={**session_body, "mode": "live"}).json()["session_id"]
        route = f"/sessions/{sid}"
        assert client.post(route + "/audio", json={"command": "start"}).status_code == 409
        assert adapters == []
        with client.websocket_connect(f"/ws/sessions/{sid}") as socket:
            authenticate(socket, config)
            socket.send_json({"type": "audio_control", "command": "start", "speaker": "member"})
            recording = socket.receive_json()
            assert recording["payload"]["session"]["audio_status"] == "recording"
            socket.send_bytes(b"\x01\x00" * 80)
            # Ping is a barrier: the preceding binary packet has reached the adapter.
            socket.send_json({"type": "ping"})
            assert socket.receive_json()["type"] == "connection_status"
            ended = client.post(route + "/end")
            assert ended.status_code == 200
            state = ended.json()
            assert state["status"] == "ended"
            assert state["segment_count"] == 1
            assert state["segments"][0]["source"] == "gemini"
            assert state["segments"][0]["speaker"] == "member"
            assert state["segments"][0]["text"] == "This is the finalized fake provider turn."
            assert adapters[0].closed and len(adapters[0].packets) == 1
            assert not app.state.audio.streams
            assert config.gemini_api_key not in ended.text
