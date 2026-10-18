def test_health_auth_and_members(client):
    assert client.get("/health").json()["analysis_provider"] == "unavailable"
    assert client.get("/members", headers={"Authorization": "Bearer wrong"}).status_code == 401
    members = client.get("/members").json()
    assert any(m["member_id"] == "M1002" for m in members)
    assert all("scenario" not in m and "outcome" not in m for m in members)


def test_create_session_requires_consent(client, session_body):
    assert client.post("/sessions", json={**session_body, "consent": False}).status_code == 422
    session = client.post("/sessions", json=session_body).json()
    assert session["status"] == "active"
    assert client.get('/sessions/' + session["session_id"]).json()["member_id"] == "M1002"


def test_websocket_authenticated_snapshot(client, session_body, config):
    session = client.post("/sessions", json=session_body).json()
    with client.websocket_connect('/ws/sessions/' + session["session_id"]) as ws:
        ws.send_json({"type": "authenticate", "token": config.dev_auth_token})
        assert ws.receive_json()["type"] == "connection_status"
        event = ws.receive_json()
        assert event["type"] == "session_status"
        assert event["payload"]["session"]["session_id"] == session["session_id"]
