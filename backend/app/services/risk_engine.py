"""Deterministic, illustrative attention rules; these are not fraud probabilities.

History weights: first recorded wire 15, new recipient 10, growing payments 20,
above-baseline payment 10 (>1.5x) or 20 (>4x), recent password reset 10,
recent profile change 10, repeated warnings followed by payment 15.
Conversation weights are explicitly listed in CONVERSATION_RULES. Each code
counts at most once, independent of repeated provider observations. Scores are
capped at 100; 0-29 is routine, 30-59 review, and 60-100 elevated.

Only already-grounded finalized member transcript evidence may enter
this function. The analysis boundary validates speakers, source IDs and quotes.
No demographic, answer-key, probability, or provider confidence input is used.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.schemas.contracts import Attention, Evidence, Insight, Payment, Signal
from app.services.member_data import history_before_wire_request


CONVERSATION_RULES: dict[str, tuple[int, str]] = {
    "TIME_PRESSURE": (8, "Time pressure described in the conversation"),
    "LARGE_TRANSACTION": (0, "Large transaction amount described; size alone is not fraud"),
    "IDENTITY_VERIFICATION_NEEDED": (5, "Recipient identity needs independent verification"),
    "INVESTMENT_RISK": (15, "Investment return promise needs review"),
    "UNUSUAL_PAYMENT_PATTERN": (0, "Unusual pattern described; scored separately from records"),
    "URGENCY": (8, "Time pressure described in the conversation"),
    "SECRECY": (15, "Request for secrecy described in the conversation"),
    "UNVERIFIED_RELATIONSHIP": (12, "Recipient relationship needs independent verification"),
    "GUARANTEED_RETURNS": (15, "Guaranteed investment returns described"),
    "COACHING": (15, "Instructions about what to tell the employee"),
    "AUTHORITY_PRESSURE": (15, "Pressure attributed to an authority figure"),
    "PROTECT_FUNDS": (18, "Instruction to move funds for claimed protection"),
    "CONTRADICTORY_EXPLANATION": (8, "Explanation needs clarification"),
    "ROMANCE_INVESTMENT": (0, "An online relationship is linked to an investment request; scenario unconfirmed"),
    "PRESSURE": (10, "External pressure described in the conversation"),
}


def event_evidence_text(event: dict[str, Any]) -> str:
    """Canonical representation shared by prompts and exact-quote validation."""
    return json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _time(event: dict[str, Any]) -> datetime:
    timestamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("Event timestamps must include a timezone")
    return timestamp.astimezone(timezone.utc)


def _evidence(event: dict[str, Any], field: str) -> Evidence:
    # A whole JSON property is an exact contiguous substring of canonical JSON.
    quote = f"{json.dumps(field)}: {json.dumps(event[field], ensure_ascii=False, allow_nan=False)}"
    return Evidence(source_type="event", source_id=event["event_id"], quote=quote[:1200])


def calculate_attention(
    events: list[dict[str, Any]],
    payment: Payment,
    insights: list[Insight],
    previous: Attention | None = None,
    *,
    as_of: datetime | None = None,
    payment_known: bool = True,
) -> Attention:
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    cutoff = as_of or max((_time(event) for event in events), default=datetime.now(timezone.utc))
    cutoff = cutoff.astimezone(timezone.utc)
    observed = sorted((event for event in events if _time(event) <= cutoff), key=_time)
    if not payment_known:
        observed = history_before_wire_request(observed)
    recent = [event for event in observed if _time(event) >= cutoff - timedelta(days=30)]
    signals: dict[str, Signal] = {}
    previous_by_code = {(signal.origin, signal.code): signal for signal in (previous.signals if previous else [])}

    def add(code: str, title: str, points: int, evidence: list[Evidence], origin: str = "history"):
        if not evidence:
            return
        identifier = f"{origin}:{code}"
        existing = signals.get(identifier)
        if existing:
            seen = {(item.source_type, item.source_id, item.quote) for item in existing.evidence}
            existing.evidence.extend(item for item in evidence if (item.source_type, item.source_id, item.quote) not in seen)
            existing.evidence = existing.evidence[:8]
            return
        prior = previous_by_code.get((origin, code))
        signals[identifier] = Signal(
            signal_id=identifier, code=code, title=title, points=points,
            evidence=evidence[:8], origin=origin,
            status=prior.status if prior else "active",
        )

    # Payment lifecycle rows repeat amounts. Only initiation counts for growth.
    initiated_by_id: dict[str, dict[str, Any]] = {}
    for event in recent:
        if event.get("event_type") == "Payment Initiated" and event.get("transaction_direction") == "Outgoing":
            initiated_by_id.setdefault(event.get("payment_id") or event["event_id"], event)
    initiated = sorted(initiated_by_id.values(), key=_time)
    matching = [
        event for event in initiated
        if event.get("payment_rail") == payment.rail
        and event.get("recipient_id") == (payment.recipient_id or None)
        and abs((event.get("amount") or 0) - payment.amount) < 0.005
    ]
    current = matching[-1] if matching and payment_known else None
    if current:
        lifecycle = [event for event in observed if current.get("payment_id") and event.get("payment_id") == current["payment_id"]]
        latest_status = lifecycle[-1].get("status") if lifecycle else current.get("status")
        # Do not describe an already-completed historical wire as a new first wire.
        if latest_status not in {"Pending", "Held"}:
            current = None
    if current:
        earlier_wires = [
            event for event in observed
            if event.get("payment_rail") == "Wire" and event.get("transaction_direction") == "Outgoing"
            and event.get("status") == "Completed" and _time(event) < _time(current)
        ]
        if payment.rail == "Wire" and "days_since_last_same_rail" in current and current["days_since_last_same_rail"] is None and not earlier_wires:
            add("FIRST_WIRE", "No earlier completed wire on the available record", 15,
                [_evidence(current, "payment_rail"), _evidence(current, "days_since_last_same_rail")])
        ratio = current.get("amount_to_baseline_ratio")
        if ratio is not None and ratio > 1.5:
            add("UNUSUAL_AMOUNT", f"Recorded payment is {ratio:g} times its historical baseline", 20 if ratio > 4 else 10,
                [_evidence(current, "amount"), _evidence(current, "amount_to_baseline_ratio")])

    recipient_events = [event for event in recent if payment_known and payment.recipient_id and event.get("recipient_id") == payment.recipient_id]
    if recipient_events:
        recipient = recipient_events[-1]
        age = recipient.get("recipient_age_days")
        age_at_cutoff = age + (cutoff - _time(recipient)).days if age is not None else None
        if recipient.get("new_recipient") and age_at_cutoff is not None and age_at_cutoff <= 30:
            add("NEW_RECIPIENT", "Recipient was added within the last 30 days", 10,
                [_evidence(recipient, "recipient_id"), _evidence(recipient, "recipient_age_days")])

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in initiated:
        if event.get("recipient_id") and (event.get("amount") or 0) > 0:
            groups[(event["recipient_id"], event.get("payment_rail") or "")].append(event)
    for group in groups.values():
        # Require three consecutively increasing payments and at least doubling.
        run = [group[0]]
        best: list[dict[str, Any]] = []
        for event in group[1:]:
            run = run + [event] if event["amount"] > run[-1]["amount"] else [event]
            if len(run) >= 3 and run[-1]["amount"] >= run[0]["amount"] * 2:
                best = run
        if best:
            add("GROWING_PAYMENTS", "Payments to one recipient grew within 30 days", 20,
                [_evidence(event, "amount") for event in best[-4:]])

    for code, event_types, flag, days, title in [
        ("RECENT_PASSWORD_RESET", {"Password Reset"}, "password_reset_recent", 7, "Password reset within the recent observation window"),
        ("RECENT_PROFILE_CHANGE", {"Email Change", "Phone Change", "Address Change"}, "profile_change_recent", 30, "Recent contact or address change needs context"),
    ]:
        source_events = [event for event in observed if event["event_type"] in event_types]
        candidates = [event for event in source_events if _time(event) >= cutoff - timedelta(days=days)]
        if candidates:
            add(code, title, 10, [_evidence(candidates[-1], "event_type")])
        elif not source_events and observed and observed[-1].get(flag) and _time(observed[-1]) >= cutoff - timedelta(days=days):
            # Flags can include source history preceding the supplied CSV window.
            add(code, f"{title} (flag at the latest event)", 10, [_evidence(observed[-1], flag)])

    warnings = [event for event in recent if event["event_type"] == "Warning Displayed"]
    for event in initiated:
        prior_warnings = [warning for warning in warnings if _time(warning) < _time(event) and warning.get("recipient_id") == event.get("recipient_id")]
        # Distinct warned payments prevent repeated rows inflating this rule.
        unique_warnings = {warning.get("payment_id") or warning["event_id"]: warning for warning in prior_warnings}
        if len(unique_warnings) >= 2:
            add("PAYMENTS_AFTER_WARNINGS", "Another payment followed warnings on two earlier payments", 15,
                [_evidence(warning, "event_type") for warning in list(unique_warnings.values())[-2:]] + [_evidence(event, "event_type")])
            break

    for insight in insights:
        if insight.status not in {"active", "acknowledged"}:
            continue
        # Event evidence may provide context, but never creates conversation points.
        segment_evidence = [item for item in insight.evidence if item.source_type == "segment"]
        if not segment_evidence:
            continue
        for code in sorted(set(insight.signal_codes)):
            if insight.signals and not any(s.signal_type == code and s.status == "OBSERVED" for s in insight.signals):
                continue
            rule = CONVERSATION_RULES.get(code)
            if rule:
                specific = [Evidence(source_type=s.source_type, source_id=s.source_id, quote=s.evidence_quote)
                            for s in insight.signals if s.signal_type == code and s.status == "OBSERVED"
                            and s.source_type == "segment"] if insight.signals else segment_evidence
                add(code, rule[1], rule[0], specific, "conversation")

    # Keep human decisions even if a later model response omits the old observation.
    for prior in previous_by_code.values():
        if prior.status != "active" and prior.signal_id not in signals:
            signals[prior.signal_id] = prior.model_copy(deep=True)

    result_signals = list(signals.values())
    score = min(100, sum(signal.points for signal in result_signals if signal.status == "active"))
    level = "elevated" if score >= 60 else "review" if score >= 30 else "routine"
    if previous is None:
        change = f"Initial evidence review: {score} illustrative points."
    else:
        delta = score - previous.score
        change = f"Attention changed by {delta:+d} points after evidence or employee review." if delta else "Attention unchanged; repeated observations do not add points."
    return Attention(
        score=score, level=level, signals=result_signals, change=change,
        explanation="Illustrative attention score, not a fraud probability. Active rules count once; employee-resolved or dismissed signals contribute no points.",
    )
