from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.contracts import ActionType, Evidence, Insight, Payment
from app.services.member_data import DEFAULT_AS_OF, MemberData
from app.services.risk_engine import CONVERSATION_RULES, calculate_attention, event_evidence_text


@pytest.fixture(scope="module")
def data():
    return MemberData()


def insight(*codes, source="segment", quote="They told me to keep this secret."):
    return Insight(
        title="Conversation needs context", observation="An instruction was described.",
        evidence=[Evidence(source_type=source, source_id="s1", quote=quote)],
        suggested_question="Could we verify this independently?",
        recommended_action=ActionType.ASK_FOLLOW_UP, priority="medium",
        confidence_in_observation=0.8, signal_codes=list(codes),
    )


def event(identifier, days_ago=0, **overrides):
    return {
        "event_id": identifier, "timestamp": (DEFAULT_AS_OF - timedelta(days=days_ago)).isoformat(),
        "event_type": "Payment Initiated", "transaction_direction": "Outgoing",
        "payment_id": identifier + "-payment", "payment_rail": "P2P",
        "recipient_id": "R1", "amount": 10.0, "status": "Pending",
        "days_since_last_same_rail": 1, "recipient_age_days": 50, "new_recipient": False,
        "password_reset_recent": False, "profile_change_recent": False,
        **overrides,
    }


def codes(attention):
    return {signal.code for signal in attention.signals if signal.status == "active"}


def test_real_m1002_prescreen_uses_observable_growth_and_first_wire(data):
    attention = calculate_attention(data.events("M1002"), Payment(), [], as_of=DEFAULT_AS_OF)
    assert attention.score == 45
    assert attention.level == "review"
    assert codes(attention) == {"FIRST_WIRE", "NEW_RECIPIENT", "GROWING_PAYMENTS"}
    assert "fraud probability" in attention.explanation


def test_every_history_quote_is_exact_substring_of_referenced_event(data):
    events = data.events("M1002")
    by_id = {item["event_id"]: item for item in events}
    attention = calculate_attention(events, Payment(), [], as_of=DEFAULT_AS_OF)
    for signal in attention.signals:
        for evidence in signal.evidence:
            assert evidence.quote in event_evidence_text(by_id[evidence.source_id])


def test_future_events_are_rejected_even_if_caller_passes_full_history(data):
    full = data.events("M1002", datetime(2026, 11, 1, tzinfo=timezone.utc))
    actual = calculate_attention(full, Payment(), [], as_of=DEFAULT_AS_OF)
    expected = calculate_attention(data.events("M1002"), Payment(), [], as_of=DEFAULT_AS_OF)
    assert actual == expected
    assert "PAYMENTS_AFTER_WARNINGS" not in codes(actual)


def test_repeated_insights_and_duplicate_codes_do_not_stack_points():
    observation = insight("SECRECY", "SECRECY", "UNRECOGNIZED_CODE")
    first = calculate_attention([], Payment(), [observation])
    repeated = calculate_attention([], Payment(), [observation] * 5, first)
    assert first.score == repeated.score == 15
    assert len(repeated.signals) == 1
    assert repeated.change.startswith("Attention unchanged")


@pytest.mark.parametrize("status", ["dismissed", "resolved"])
def test_employee_signal_status_survives_repeated_and_omitted_observations(status):
    observation = insight("SECRECY")
    first = calculate_attention([], Payment(), [observation])
    first.signals[0].status = status
    cleared = calculate_attention([], Payment(), [observation], first)
    omitted = calculate_attention([], Payment(), [], cleared)
    returned = calculate_attention([], Payment(), [observation], omitted)
    assert cleared.score == omitted.score == returned.score == 0
    assert returned.signals[0].status == status


def test_resolved_history_stays_resolved_after_recomputation(data):
    history = data.events("M1002")
    first = calculate_attention(history, Payment(), [], as_of=DEFAULT_AS_OF)
    for signal in first.signals:
        signal.status = "resolved"
    reviewed = calculate_attention(history, Payment(), [], first, as_of=DEFAULT_AS_OF)
    assert reviewed.score == 0
    assert reviewed.level == "routine"
    assert all(signal.status == "resolved" for signal in reviewed.signals)


def test_event_only_insight_does_not_count_as_conversation():
    result = calculate_attention([], Payment(), [insight("SECRECY", source="event")])
    assert result.score == 0


def test_mixed_evidence_uses_only_grounded_segment_portion():
    observation = insight("SECRECY")
    observation.evidence.append(Evidence(source_type="event", source_id="E1", quote='"amount": 10'))
    result = calculate_attention([], Payment(), [observation])
    assert result.score == 15
    assert all(item.source_type == "segment" for item in result.signals[0].evidence)


def test_inactive_insight_does_not_count():
    observation = insight("SECRECY")
    observation.status = "dismissed"
    assert calculate_attention([], Payment(), [observation]).score == 0


def test_growth_never_counts_lifecycle_or_duplicate_initiation_rows():
    start = event("E1", days_ago=3, amount=10)
    higher = event("E2", days_ago=2, amount=30)
    duplicate = {**higher, "event_id": "E3", "amount": 60}
    completed = {**higher, "event_id": "E4", "event_type": "Payment Completed", "amount": 100}
    result = calculate_attention([start, higher, duplicate, completed], Payment(), [], as_of=DEFAULT_AS_OF)
    assert "GROWING_PAYMENTS" not in codes(result)


def test_growth_requires_one_recipient_and_recent_window():
    events = [event("E1", days_ago=40, amount=10), event("E2", days_ago=2, amount=20), event("E3", days_ago=1, amount=50, recipient_id="R2")]
    assert "GROWING_PAYMENTS" not in codes(calculate_attention(events, Payment(), [], as_of=DEFAULT_AS_OF))


def test_recent_flags_expire_and_future_reset_does_not_leak():
    old = event("old", days_ago=8, event_type="Password Reset", password_reset_recent=True)
    future = event("future", days_ago=-1, event_type="Password Reset", password_reset_recent=True)
    assert "RECENT_PASSWORD_RESET" not in codes(calculate_attention([old, future], Payment(), [], as_of=DEFAULT_AS_OF))
    recent = event("recent", days_ago=2, event_type="Password Reset", password_reset_recent=True)
    assert "RECENT_PASSWORD_RESET" in codes(calculate_attention([old, recent, future], Payment(), [], as_of=DEFAULT_AS_OF))


def test_known_source_timestamp_takes_precedence_over_stale_true_flag():
    reset = event("reset", days_ago=10, event_type="Password Reset", password_reset_recent=True)
    last = event("last", days_ago=4, password_reset_recent=True)
    result = calculate_attention([reset, last], Payment(), [], as_of=DEFAULT_AS_OF)
    assert "RECENT_PASSWORD_RESET" not in codes(result)


def test_profile_changes_use_30_day_window():
    fresh = event("profile", days_ago=29, event_type="Address Change", profile_change_recent=True)
    result = calculate_attention([fresh], Payment(), [], as_of=DEFAULT_AS_OF)
    assert "RECENT_PROFILE_CHANGE" in codes(result)
    later = calculate_attention([fresh], Payment(), [], as_of=DEFAULT_AS_OF + timedelta(days=2))
    assert "RECENT_PROFILE_CHANGE" not in codes(later)


def test_legitimate_lookalike_gets_observation_rules_without_answer_key(data):
    cutoff = datetime(2026, 10, 19, 16, 45, tzinfo=timezone.utc)
    history = data.events("M1057", cutoff)
    payment_record = next(item for item in history if item["event_id"] == "E03702")
    payment = Payment(amount=payment_record["amount"], recipient_id=payment_record["recipient_id"])
    result = calculate_attention(history, payment, [], as_of=cutoff)
    assert "UNUSUAL_AMOUNT" in codes(result)
    assert "NEW_RECIPIENT" in codes(result)
    assert "legitimate" not in result.model_dump_json()
    assert next(signal for signal in result.signals if signal.code == "UNUSUAL_AMOUNT").points == 20


def test_no_unusual_amount_assumption_when_requested_payment_differs(data):
    result = calculate_attention(data.events("M1002"), Payment(amount=999999), [], as_of=DEFAULT_AS_OF)
    assert "UNUSUAL_AMOUNT" not in codes(result)


def test_preexisting_wire_history_prevents_first_wire_signal():
    old = event("old", days_ago=15, payment_rail="Wire", status="Completed", event_type="Payment Completed")
    current = event("current", payment_rail="Wire", recipient_id="R1002-09", amount=1500, days_since_last_same_rail=None)
    result = calculate_attention([old, current], Payment(), [], as_of=DEFAULT_AS_OF)
    assert "FIRST_WIRE" not in codes(result)


def test_repeated_warning_rule_requires_distinct_earlier_payments():
    warning_one = event("warn1", days_ago=3, event_type="Warning Displayed")
    repeated = {**warning_one, "event_id": "repeated", "timestamp": (DEFAULT_AS_OF - timedelta(days=2)).isoformat()}
    payment = event("later")
    assert "PAYMENTS_AFTER_WARNINGS" not in codes(calculate_attention([warning_one, repeated, payment], Payment(), [], as_of=DEFAULT_AS_OF))
    warning_two = event("warn2", days_ago=1, event_type="Warning Displayed")
    result = calculate_attention([warning_one, warning_two, payment], Payment(), [], as_of=DEFAULT_AS_OF)
    assert "PAYMENTS_AFTER_WARNINGS" in codes(result)


def test_demographics_and_confidence_never_change_scoring(data):
    history = data.events("M1002")
    extra = deepcopy(history)
    for item in extra:
        item.update(age=90, disability="fictional", scenario_type="answer key not allowed")
    low = insight("SECRECY")
    low.confidence_in_observation = 0.1
    high = low.model_copy(update={"confidence_in_observation": 1.0})
    first = calculate_attention(history, Payment(), [low], as_of=DEFAULT_AS_OF)
    second = calculate_attention(extra, Payment(), [high], as_of=DEFAULT_AS_OF)
    assert first == second


def test_score_cap_and_thresholds():
    result = calculate_attention([], Payment(), [insight(*list(CONVERSATION_RULES)[:8]), insight(*list(CONVERSATION_RULES)[8:])])
    assert result.score == 100
    assert result.level == "elevated"
    assert calculate_attention([], Payment(), []).level == "routine"


def test_naive_as_of_rejected():
    with pytest.raises(ValueError, match="timezone"):
        calculate_attention([], Payment(), [], as_of=datetime(2026, 9, 1))
