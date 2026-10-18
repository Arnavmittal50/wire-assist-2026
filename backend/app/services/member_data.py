"""Observable, time-bounded HuskyHack data. The answer key is never loaded.

CSV dates are Pacific wall-clock times, not UTC. Each caller supplies its replay
cutoff so recommendations cannot use events that had not happened yet.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd


DEFAULT_DATA_DIR = Path(__file__).resolve().parents[3] / "data"
PACIFIC = ZoneInfo("America/Los_Angeles")
DEFAULT_AS_OF = datetime(2026, 9, 27, 2, 25, tzinfo=timezone.utc)


def history_before_wire_request(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Withhold unfinished wire fixtures representing a request initiated on the call.

    Completed payments keep their lifecycle rows. Withhold recipient setup belonging
    only to unfinished wires too. Callers first enforce the member and replay cutoff.
    """
    ordered = sorted(events, key=lambda event: (event['timestamp'], event.get('event_sequence', 0)))
    latest = {}
    for event in ordered:
        if event.get('payment_id') and event.get('status'):
            latest[event['payment_id']] = event['status']
    requests = [event for event in ordered if event.get('event_type') == 'Payment Initiated'
                and event.get('payment_rail') == 'Wire' and event.get('transaction_direction') == 'Outgoing'
                and latest.get(event.get('payment_id'), event.get('status')) in {'Pending', 'Held'}]
    hidden_ids = {event['payment_id'] for event in requests if event.get('payment_id')}
    hidden_events = {event['event_id'] for event in requests}
    established = {event.get('recipient_id') for event in ordered if event.get('status') == 'Completed'}
    new_recipients = {event.get('recipient_id') for event in requests} - established - {None}
    return [event for event in ordered if event.get('payment_id') not in hidden_ids
            and event['event_id'] not in hidden_events
            and not (event.get('event_type') == 'Recipient Added' and event.get('recipient_id') in new_recipients)]


def _optional_number(value: str, integer: bool = False) -> int | float | None:
    if not value.strip():
        return None
    number = float(value)
    if not isfinite(number):
        raise ValueError("Numeric data must be finite")
    if integer and not number.is_integer():
        raise ValueError("Integer data must not have a fractional part")
    return int(number) if integer else number


def _optional_text(value: str) -> str | None:
    return value if value and value != "Not Applicable" else None


def _normalize(row: dict[str, str]) -> dict[str, Any]:
    occurred = datetime.strptime(row["event_timestamp"], "%m/%d/%Y %H:%M")
    timestamp = occurred.replace(tzinfo=PACIFIC).astimezone(timezone.utc)
    rail = _optional_text(row.get("payment_rail", ""))
    event: dict[str, Any] = {
        "event_id": row["event_id"],
        "member_id": row["synthetic_member_id"],
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "event_type": row["event_type"],
        "event_sequence": int(row.get("event_sequence", "0") or "0"),
        "amount": _optional_number(row.get("amount", "")),
        "channel": row.get("channel", ""),
        "recipient_id": _optional_text(row.get("recipient_id", "")),
        "description": f"{row['event_type']} via {rail}" if rail else row["event_type"],
        "status": _optional_text(row.get("payment_status", "")),
        "payment_id": _optional_text(row.get("payment_id", "")),
        "payment_rail": rail,
        "transaction_direction": _optional_text(row.get("transaction_direction", "")),
        "recipient_status": _optional_text(row.get("recipient_status", "")),
        "new_recipient": row.get("recipient_status") == "New",
        "recipient_age_days": _optional_number(row.get("recipient_age_days", ""), True),
        "days_since_last_same_rail": _optional_number(row.get("days_since_last_same_rail", ""), True),
        "amount_to_baseline_ratio": _optional_number(row.get("amount_to_baseline_ratio", "")),
        "amount_vs_member_baseline": _optional_text(row.get("amount_vs_member_baseline", "")),
        "payment_velocity_band": _optional_text(row.get("payment_velocity_band", "")),
        "available_balance_after": _optional_number(row.get("available_balance_after", "")),
        "member_response": _optional_text(row.get("member_response", "")),
    }
    if event["amount"] is not None and event["amount"] < 0:
        raise ValueError("Event amounts must be nonnegative; direction is a separate field")
    for flag in (
        "profile_change_recent", "password_reset_recent", "trusted_contact_enrolled",
        "warning_displayed", "employee_interaction", "case_open",
    ):
        event[flag] = row.get(flag) == "Yes"
    return event


def _sample_rows() -> list[dict[str, str]]:
    """Small, openly identified fixtures used only when the supplied CSV is absent."""
    rows = []
    samples = [
        ("M1001", "9/25/2026 13:00", "Wire", "8500", "R-SAMPLE-4"),
        ("M1002", "9/8/2026 13:47", "P2P", "87.95", "R-SAMPLE-1"),
        ("M1002", "9/12/2026 7:06", "P2P", "132.93", "R-SAMPLE-1"),
        ("M1002", "9/17/2026 20:21", "P2P", "220.12", "R-SAMPLE-1"),
        ("M1002", "9/21/2026 10:28", "P2P", "306.77", "R-SAMPLE-1"),
        ("M1002", "9/26/2026 19:25", "Wire", "1500", "R-SAMPLE-2"),
        ("M1003", "9/25/2026 14:00", "External Transfer", "2000", "R-SAMPLE-5"),
        ("M1004", "9/25/2026 15:00", "Cash", "7000", ""),
        ("M1057", "9/25/2026 10:00", "Wire", "51587", "R-SAMPLE-3"),
    ]
    for index, (member, timestamp, rail, amount, recipient) in enumerate(samples, 1):
        rows.append({
            "event_id": f"SAMPLE-{index:03d}", "synthetic_member_id": member,
            "event_sequence": str(index), "event_timestamp": timestamp,
            "event_type": "Payment Initiated", "amount": amount,
            "channel": "Branch", "payment_rail": rail,
            "payment_id": f"SAMPLE-P-{index:03d}", "recipient_id": recipient,
            "recipient_status": "New" if recipient else "Not Applicable", "transaction_direction": "Outgoing",
            "payment_status": "Pending", "recipient_age_days": "1" if recipient else "",
            "password_reset_recent": "Yes" if member == "M1003" else "No",
        })
    rows.append({
        "event_id": "SAMPLE-010", "synthetic_member_id": "M1003",
        "event_sequence": "0", "event_timestamp": "9/25/2026 13:50",
        "event_type": "Password Reset", "amount": "0", "channel": "Online Banking",
        "password_reset_recent": "Yes",
    })
    return rows


class MemberData:
    def __init__(self, data_dir: Path | str = DEFAULT_DATA_DIR):
        source = Path(data_dir) / "synthetic_events.csv"
        if source.is_file():
            frame = pd.read_csv(source, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            required = {"event_id", "synthetic_member_id", "event_timestamp", "event_type"}
            missing = required - set(frame.columns)
            if missing:
                raise ValueError(f"Synthetic events CSV is missing columns: {', '.join(sorted(missing))}")
            rows = frame.to_dict(orient="records")
            self.data_source = "huskyhack_csv"
        else:
            rows = _sample_rows()
            self.data_source = "sample_fixture"
        self._events: dict[str, list[dict[str, Any]]] = {}
        seen: set[str] = set()
        for row in rows:
            if not row["event_id"] or not row["synthetic_member_id"]:
                raise ValueError("Events must include nonempty event and member IDs")
            if row["event_id"] in seen:
                raise ValueError(f"Duplicate event ID: {row['event_id']}")
            seen.add(row["event_id"])
            try:
                event = _normalize(row)
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid event {row['event_id']}: {error}") from error
            self._events.setdefault(event["member_id"], []).append(event)
        for events in self._events.values():
            events.sort(key=lambda event: (event["timestamp"], event["event_sequence"]))

    def members(self) -> list[dict[str, str]]:
        return [self.member(member_id) for member_id in sorted(self._events)]

    def member(self, member_id: str) -> dict[str, str] | None:
        if member_id not in self._events:
            return None
        return {
            "member_id": member_id,
            "label": f"Synthetic member {member_id}",
            "data_source": self.data_source,
        }

    def events(self, member_id: str, as_of: datetime = DEFAULT_AS_OF) -> list[dict[str, Any]]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        cutoff = as_of.astimezone(timezone.utc)
        return deepcopy([
            event for event in self._events.get(member_id, [])
            if datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")) <= cutoff
        ])
