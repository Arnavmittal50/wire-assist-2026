import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.models.repository import REDACTED, RepositoryError, SessionRepository, redact_session
from app.schemas.contracts import (
    ActionType, Activity, Attention, Evidence, Insight, Payment,
    Segment, Session, Signal, TellerAction,
)


PRIVATE = "UNIQUE_PRIVATE_TEXT_9471"
FINGERPRINT = hashlib.sha256(PRIVATE.encode()).hexdigest()


def session(retain=False):
    evidence = Evidence(source_type="segment", source_id="turn-1", quote=PRIVATE)
    historical = Evidence(source_type="event", source_id="E00104", quote='"amount": 1500.0')
    return Session(
        member_id="M1002", as_of=datetime(2026, 9, 27, 2, 25, tzinfo=timezone.utc),
        consent=True, retain_transcript=retain, mode="demo",
        payment=Payment(purpose=PRIVATE), data_source="huskyhack_csv",
        segments=[Segment(segment_id="turn-1", sequence=0, text=PRIVATE, speaker="member")],
        segment_count=1, next_sequence=1,
        insights=[Insight(
            title=PRIVATE, observation=PRIVATE, suggested_question=PRIVATE,
            evidence=[evidence, historical], source_segment_ids=["turn-1"], source_event_ids=["E00104"],
            recommended_action=ActionType.ASK_FOLLOW_UP, priority="medium",
            confidence_in_observation=0.9, signal_codes=["SECRECY"],
        )],
        attention=Attention(score=15, level="routine", signals=[Signal(
            signal_id="conversation:SECRECY", code="SECRECY", title=PRIVATE, points=15,
            evidence=[evidence], origin="conversation",
        )]),
        actions=[TellerAction(action_type=ActionType.OFFER_VERIFICATION, note=PRIVATE)],
        activity=[Activity(description=PRIVATE)], provider_notice=PRIVATE,
    )


@pytest.fixture
def repository(tmp_path):
    repo = SessionRepository(tmp_path / "sessions.sqlite3")
    yield repo
    repo.close()


def receipt():
    return {"segment_id": "turn-1", "sequence": 0, "fingerprint": FINGERPRINT}


def test_no_retention_redacts_all_conversation_derivatives_without_mutating_memory(repository):
    current = session()
    before = current.model_dump_json()
    assert PRIVATE not in redact_session(current).model_dump_json()
    repository.save(current, receipt())
    persisted = repository.load_all()[0]
    assert PRIVATE not in persisted.model_dump_json()
    assert persisted.segments == []
    assert persisted.insights[0].evidence[0].quote == REDACTED
    assert persisted.insights[0].evidence[1].quote == '"amount": 1500.0'
    assert persisted.insights[0].source_segment_ids == ["turn-1"]
    assert persisted.attention.signals[0].code == "SECRECY"
    assert persisted.actions[0].action_type == ActionType.OFFER_VERIFICATION
    assert persisted.actions[0].note == ""
    assert persisted.payment.purpose == ""
    assert persisted.segment_count == 1
    assert persisted.next_sequence == 1
    assert current.model_dump_json() == before
    assert PRIVATE.encode() not in Path(repository.path).read_bytes()


def test_opt_in_retention_preserves_transcript_and_review_details(repository):
    current = session(retain=True)
    repository.save(current)
    saved = repository.load_all()[0]
    assert saved.segments[0].text == PRIVATE
    assert saved.insights[0].observation == PRIVATE
    assert saved.actions[0].note == PRIVATE
    assert saved.model_dump() == current.model_dump()


def test_atomic_session_and_receipt_reopen_without_plaintext(tmp_path):
    path = tmp_path / "reopen.sqlite3"
    current = session()
    first = SessionRepository(path)
    first.save(current, receipt())
    first.close()
    second = SessionRepository(path)
    try:
        assert second.load_all()[0].session_id == current.session_id
        assert second.segment_receipt(current.session_id, "turn-1") == {"fingerprint": FINGERPRINT, "sequence": 0}
        assert second.sequence_exists(current.session_id, 0)
        assert not second.sequence_exists(current.session_id, 1)
        assert second.receipt_count(current.session_id) == 1
        assert PRIVATE.encode() not in path.read_bytes()
    finally:
        second.close()


def test_matching_receipt_retry_is_idempotent(repository):
    current = session()
    repository.save(current, receipt())
    repository.save(current, receipt())
    repository.record_receipt(current.session_id, "turn-1", 0, FINGERPRINT)
    assert repository.receipt_count(current.session_id) == 1


def test_conflicting_receipt_rolls_back_entire_snapshot(repository):
    current = session()
    repository.save(current, receipt())
    current.review_outcome = "legitimate"
    conflict = {**receipt(), "fingerprint": "a" * 64}
    with pytest.raises(RepositoryError, match="conflicts"):
        repository.save(current, conflict)
    assert repository.load_all()[0].review_outcome == "in_review"
    assert repository.segment_receipt(current.session_id, "turn-1")["fingerprint"] == FINGERPRINT


def test_duplicate_sequence_rolls_back_session_update(repository):
    current = session()
    repository.save(current, receipt())
    current.review_outcome = "legitimate"
    with pytest.raises(RepositoryError, match="could not be saved"):
        repository.save(current, {**receipt(), "segment_id": "turn-2"})
    assert repository.load_all()[0].review_outcome == "in_review"
    assert repository.receipt_count(current.session_id) == 1


@pytest.mark.parametrize("bad_receipt", [
    {"segment_id": "turn", "sequence": 0, "fingerprint": PRIVATE},
    {"segment_id": "turn", "sequence": -1, "fingerprint": FINGERPRINT},
    {"segment_id": "", "sequence": 0, "fingerprint": FINGERPRINT},
    {"segment_id": "turn", "sequence": True, "fingerprint": FINGERPRINT},
])
def test_invalid_receipt_fails_with_safe_error_and_no_partial_session(repository, bad_receipt):
    with pytest.raises(RepositoryError) as failure:
        repository.save(session(), bad_receipt)
    assert PRIVATE not in str(failure.value)
    assert repository.load_all() == []


def test_receipt_cannot_exist_without_parent_session(repository):
    with pytest.raises(RepositoryError):
        repository.record_receipt("missing", "turn-1", 0, FINGERPRINT)
    assert repository.receipt_count("missing") == 0


def test_delete_removes_snapshot_receipts_and_plaintext_pages(repository):
    current = session(retain=True)
    repository.save(current, receipt())
    repository.delete(current.session_id)
    assert repository.load_all() == []
    assert repository.segment_receipt(current.session_id, "turn-1") is None
    assert repository.receipt_count(current.session_id) == 0
    assert PRIVATE.encode() not in Path(repository.path).read_bytes()
    repository.delete(current.session_id)  # Deletion remains idempotent.


def test_turning_retention_off_removes_old_plaintext_pages(repository):
    current = session(retain=True)
    repository.save(current)
    current.retain_transcript = False
    repository.save(current)
    assert PRIVATE.encode() not in Path(repository.path).read_bytes()


def test_loading_stops_interrupted_audio_and_pending_analysis(repository):
    current = session()
    current.audio_status = "recording"
    current.analysis_pending = True
    repository.save(current)
    restored = repository.load_all()[0]
    assert restored.audio_status == "stopped"
    assert restored.analysis_pending is False
    assert "restored after interruption" in restored.provider_notice
    assert current.audio_status == "recording"
    assert current.analysis_pending is True


def test_corrupt_snapshot_reports_safe_error_without_stored_content(repository):
    with sqlite3.connect(repository.path) as raw:
        raw.execute("INSERT INTO sessions VALUES (?, ?, ?)", ("corrupt", PRIVATE, "2026"))
    with pytest.raises(RepositoryError) as failure:
        repository.load_all()
    assert PRIVATE not in str(failure.value)


def test_storage_uses_foreign_keys_secure_delete_and_no_wal(repository):
    assert repository._connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert repository._connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
    assert repository._connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert repository._connection.execute("PRAGMA synchronous").fetchone()[0] == 2
