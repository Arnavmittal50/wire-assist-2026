import time
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.main import create_app


def settled(client, sid):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = client.get(f"/sessions/{sid}").json()
        if not state["analysis_pending"]:
            return state
        time.sleep(0.01)
    raise AssertionError("Local analysis did not settle")


def test_actions_legitimate_review_summary_and_delete(client, session_body):
    state = client.post("/sessions", json=session_body).json()
    sid = state["session_id"]
    route = f"/sessions/{sid}"
    client.post(route + "/transcript", json={"segment_id": "review", "sequence": 1,
                "text": "I met him online. He says send today and keep it secret.", "speaker": "member"})
    state = settled(client, sid)
    insight_id = state["insights"][0]["insight_id"]
    assert state["attention"]["score"] > 45
    assert client.post(route + "/actions", json={"action_type": "ACKNOWLEDGE", "insight_id": insight_id}).status_code == 200
    assert client.post(route + "/actions", json={"action_type": "DISMISS", "insight_id": insight_id}).status_code == 422
    state = client.post(route + "/actions", json={"action_type": "DISMISS", "insight_id": insight_id,
                                                   "note": "Clarified this wording independently."}).json()
    assert state["insights"][0]["status"] == "dismissed"
    state = client.post(route + "/actions", json={"action_type": "MARK_LEGITIMATE",
                "note": "Verified invoice and recipient with an independently sourced known contact; member wishes to proceed."}).json()
    assert state["attention"]["score"] == 0
    assert state["review_outcome"] == "legitimate"
    assert all(a["simulated"] for a in state["actions"])
    ended = client.post(route + "/end").json()
    assert ended["status"] == "ended" and ended["ended_at"]
    summary = client.get(route + "/summary").json()
    assert summary["review_outcome"] == "legitimate" and summary["transcript_segment_count"] == 1
    assert not summary["unresolved_signals"]
    assert client.post(route + "/transcript", json={"sequence": 2, "text": "Too late"}).status_code == 409
    assert client.post(route + "/actions", json={"action_type": "NO_ACTION"}).status_code == 409
    assert client.delete(route).status_code == 204
    assert client.get(route).status_code == 404
    assert all(s["session_id"] != sid for s in client.get("/sessions").json())


def test_simulated_hold_restrictions_and_voluntary_contact(client, session_body):
    sid = client.post("/sessions", json=session_body).json()["session_id"]
    route = f"/sessions/{sid}/actions"
    hold = client.post(route, json={"action_type": "CONSIDER_HOLD", "note": "Review requested"}).json()["actions"][-1]
    assert "up to 4 hours" in hold["note"] and "No funds held" in hold["note"] and hold["simulated"]
    contact = client.post(route, json={"action_type": "OFFER_TRUSTED_CONTACT"}).json()["actions"][-1]
    assert "voluntary" in contact["note"] and "no control over funds" in contact["note"]
    cash_sid = client.post("/sessions", json={**session_body, "payment": {"amount": 7000, "rail": "Cash", "recipient_id": ""}}).json()["session_id"]
    assert client.post(f"/sessions/{cash_sid}/actions", json={"action_type": "CONSIDER_HOLD"}).status_code == 422
    assert client.post(route, json={"action_type": "ACKNOWLEDGE", "insight_id": "other-session-insight"}).status_code == 404


def test_legitimate_dataset_member_does_not_auto_clear_from_answer_key(client, session_body):
    cutoff = "2026-10-19T16:41:00Z"
    events = client.get(f"/members/M1057/events?as_of={cutoff}").json()
    observed = next(e for e in events if e["event_id"] == "E03702")
    body = {**session_body, "member_id": "M1057", "as_of": cutoff,
            "payment": {"amount": observed["amount"], "recipient_id": observed["recipient_id"], "rail": "Wire"}}
    state = client.post("/sessions", json=body).json()
    assert state["attention"]["score"] > 0 and state["review_outcome"] == "in_review"
    sid = state["session_id"]
    state = client.post(f"/sessions/{sid}/actions", json={"action_type": "MARK_LEGITIMATE",
                          "note": "Teller independently verified the closing instructions and member intent."}).json()
    assert state["review_outcome"] == "legitimate" and state["attention"]["score"] == 0


def test_restart_preserves_retained_text_and_durable_idempotency(config, session_body):
    request = {"segment_id": "restart-segment", "sequence": 4, "text": "I need to pay today.", "speaker": "member"}
    auth = {"Authorization": "Bearer " + config.dev_auth_token}
    with TestClient(create_app(config), headers=auth) as first:
        sid = first.post("/sessions", json=session_body).json()["session_id"]
        first.post(f"/sessions/{sid}/transcript", json=request)
        settled(first, sid)
    with TestClient(create_app(config), headers=auth) as second:
        state = second.get(f"/sessions/{sid}").json()
        assert state["segments"][0]["text"] == request["text"]
        assert state["audio_status"] == "stopped"
        state = second.post(f"/sessions/{sid}/transcript", json=request).json()
        assert state["segment_count"] == 1 and state["next_sequence"] == 5
        second.delete(f"/sessions/{sid}")
    with TestClient(create_app(config), headers=auth) as third:
        assert third.get(f"/sessions/{sid}").status_code == 404


def test_nonretained_text_never_reappears_after_restart(config, session_body):
    text = "private-marker-cobalt-821 Please send today."
    request = {"segment_id": "transient", "sequence": 1, "text": text, "speaker": "member"}
    auth = {"Authorization": "Bearer " + config.dev_auth_token}
    with TestClient(create_app(config), headers=auth) as first:
        sid = first.post("/sessions", json={**session_body, "retain_transcript": False}).json()["session_id"]
        first.post(f"/sessions/{sid}/transcript", json=request)
        settled(first, sid)
    assert text.encode() not in config.database_path.read_bytes()
    with TestClient(create_app(config), headers=auth) as second:
        state = second.get(f"/sessions/{sid}").json()
        assert not state["segments"] and state["segment_count"] == 1
        assert "private-marker-cobalt-821" not in str(state)
        assert second.post(f"/sessions/{sid}/transcript", json=request).json()["segment_count"] == 1
        assert second.post(f"/sessions/{sid}/transcript", json={**request, "text": "changed"}).status_code == 409
        response = second.post(f"/sessions/{sid}/transcript", json={"segment_id": "next", "sequence": state["next_sequence"], "text": "One more turn"})
        assert response.status_code == 200 and response.json()["segment_count"] == 2


def test_signal_resolution_changes_score_with_auditable_reason(client, session_body):
    state = client.post("/sessions", json=session_body).json()
    sid = state["session_id"]
    signal = state["attention"]["signals"][0]
    state = client.post(f"/sessions/{sid}/actions", json={"action_type": "RESOLVE_SIGNAL", "signal_id": signal["signal_id"],
                                                         "note": "Context independently checked."}).json()
    assert state["attention"]["score"] == 45 - signal["points"]
    assert state["actions"][-1]["signal_id"] == signal["signal_id"]


def test_summary_records_initial_demo_fallback_before_any_speech(client, session_body):
    state = client.post("/sessions", json={**session_body, "mode": "live"}).json()
    sid = state["session_id"]
    client.post(f"/sessions/{sid}/end")
    assert client.get(f"/sessions/{sid}/summary").json()["analysis_providers"] == ["unavailable"]
    baseline = client.get(f"/sessions/{sid}").json()["initial_analysis"]
    assert baseline["provider"] == "unavailable" and baseline["model"] is None
