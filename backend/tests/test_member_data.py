import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.member_data import DEFAULT_AS_OF, MemberData


@pytest.fixture(scope="module")
def data():
    return MemberData(Path(__file__).resolve().parents[2] / "data")


def test_loads_all_synthetic_members_without_answer_key(data):
    assert len(data.members()) == 118
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert sum(len(data.events(member["member_id"], end)) for member in data.members()) == 7471
    assert data.member("M1002") == {
        "member_id": "M1002", "label": "Synthetic member M1002", "data_source": "huskyhack_csv",
    }
    assert data.member("not-a-member") is None
    assert data.events("not-a-member") == []


def test_cutoff_uses_pacific_timezone_and_excludes_future_outcomes(data):
    events = data.events("M1002", DEFAULT_AS_OF)
    assert events[-1]["event_id"] == "E00104"
    assert events[-1]["timestamp"] == "2026-09-27T02:25:00Z"
    assert events[-1]["amount"] == 1500.0
    assert events[-1]["status"] == "Pending"
    assert not any(e["event_type"] in {"Scam Reported", "Recovery Unsuccessful"} for e in events)
    later = data.events("M1002", datetime(2026, 10, 31, tzinfo=timezone.utc))
    assert any(e["event_type"] == "Scam Reported" for e in later)


def test_observed_amounts_are_preserved_and_lifecycle_events_distinct(data):
    events = data.events("M1002", DEFAULT_AS_OF)
    growth = [e["amount"] for e in events if e["event_type"] == "Payment Initiated" and e["recipient_id"] == "R1002-08"]
    assert growth == [87.95, 132.93, 220.12, 306.77]
    wire = events[-1]
    assert wire["days_since_last_same_rail"] is None
    assert wire["new_recipient"] is True
    assert wire["password_reset_recent"] is False


def test_labels_notes_and_demographics_never_enter_public_payload(data):
    payload = json.dumps(data.members() + data.events("M1002"))
    for forbidden in ["scenario_type", "scenario_subtype", "journey_outcome", "is_legitimate_lookalike", "event_note", "anchor_persona", "Michael"]:
        assert forbidden not in payload


def test_callers_cannot_mutate_cached_history(data):
    events = data.events("M1002")
    events[0]["amount"] = -99
    assert data.events("M1002")[0]["amount"] != -99


def test_missing_events_uses_explicit_fixture_and_ignores_key(tmp_path):
    (tmp_path / "member_key.csv").write_text("invalid answer key that must not be read")
    fallback = MemberData(tmp_path)
    assert fallback.data_source == "sample_fixture"
    assert fallback.member("M1002")["data_source"] == "sample_fixture"
    assert fallback.events("M1002")[-1]["event_id"].startswith("SAMPLE-")
    assert {"M1001", "M1002", "M1003", "M1004"}.issubset({m["member_id"] for m in fallback.members()})
    assert all(fallback.events(member_id) for member_id in ["M1001", "M1002", "M1003", "M1004"])


def test_naive_cutoff_rejected(data):
    with pytest.raises(ValueError, match="timezone"):
        data.events("M1002", datetime(2026, 9, 27))


def test_malformed_present_csv_fails_instead_of_masking_error(tmp_path):
    (tmp_path / "synthetic_events.csv").write_text("event_id,amount\nE1,4\n")
    with pytest.raises(ValueError, match="missing columns"):
        MemberData(tmp_path)


def test_duplicate_event_ids_rejected(tmp_path):
    rows = [
        {"event_id": "E1", "synthetic_member_id": "M1", "event_timestamp": "9/1/2026 12:00", "event_type": "Login"},
        {"event_id": "E1", "synthetic_member_id": "M1", "event_timestamp": "9/1/2026 13:00", "event_type": "Login"},
    ]
    with (tmp_path / "synthetic_events.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="Duplicate event"):
        MemberData(tmp_path)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-Infinity", "-50", "not a number"])
def test_invalid_amount_rejected_with_event_context(tmp_path, amount):
    (tmp_path / "synthetic_events.csv").write_text(
        "event_id,synthetic_member_id,event_timestamp,event_type,amount\n"
        f"E1,M1,9/1/2026 12:00,Payment Initiated,{amount}\n"
    )
    with pytest.raises(ValueError, match="Invalid event E1"):
        MemberData(tmp_path)


def test_yes_no_flags_preserved_at_event_time(data):
    events = data.events("M1003", datetime(2026, 10, 16, 5, 15, tzinfo=timezone.utc))
    reset = next(event for event in events if event["event_id"] == "E00197")
    changed = next(event for event in events if event["event_id"] == "E00201")
    assert reset["password_reset_recent"] is True
    assert reset["profile_change_recent"] is False
    assert changed["profile_change_recent"] is True
    assert changed["password_reset_recent"] is True
