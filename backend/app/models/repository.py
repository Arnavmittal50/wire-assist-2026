"""SQLite snapshots and transcript receipts for the local, single-process demo.

Transcript retention is opt-in. A detached snapshot removes conversation text
and freeform notes before serialization, while in-memory review remains usable.
Receipt fingerprints permit idempotent retries after restart without storing
transcript plaintext. This repository is not a production encrypted vault.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.contracts import Session


REDACTED = "Conversation text was not retained."


class RepositoryError(RuntimeError):
    """Safe storage error that never includes stored text or SQLite parameters."""


def redact_session(session: Session) -> Session:
    """Return an independent snapshot honoring retention, usable for storage or end-session cleanup."""
    saved = session.model_copy(deep=True)
    if saved.retain_transcript:
        return saved
    saved.segments = []
    saved.payment.purpose = ""
    saved.provider_notice = "Transcript text is not retained. Provider labels identify the analysis source."
    for insight in saved.insights:
        insight.title = "Observation text not retained"
        insight.observation = REDACTED
        insight.suggested_question = REDACTED
        for evidence in insight.evidence:
            if evidence.source_type == "segment":
                evidence.quote = REDACTED
    for signal in saved.attention.signals:
        if signal.origin == "conversation":
            signal.title = "Conversation signal (text not retained)"
        for evidence in signal.evidence:
            if evidence.source_type == "segment":
                evidence.quote = REDACTED
    for action in saved.actions:
        action.note = ""
    for activity in saved.activity:
        activity.description = "Session activity recorded; freeform details were not retained."
    return saved


class SessionRepository:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        try:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA secure_delete = ON")
            self._connection.execute("PRAGMA journal_mode = DELETE")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._connection.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transcript_receipts (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
                    segment_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK (sequence >= 0),
                    fingerprint TEXT NOT NULL,
                    PRIMARY KEY (session_id, segment_id),
                    UNIQUE (session_id, sequence)
                );
            """)
            self._connection.commit()
        except (OSError, sqlite3.Error):
            raise RepositoryError("Session storage could not be initialized.") from None

    @staticmethod
    def _receipt_values(receipt: dict[str, Any]) -> tuple[str, int, str]:
        identifier = receipt.get("segment_id")
        sequence = receipt.get("sequence")
        fingerprint = receipt.get("fingerprint")
        if (
            not isinstance(identifier, str) or not 1 <= len(identifier) <= 100
            or not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0
            or not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint)
        ):
            raise RepositoryError("Transcript receipt is invalid.")
        return identifier, sequence, fingerprint.lower()

    def _insert_receipt(self, session_id: str, receipt: dict[str, Any]):
        identifier, sequence, fingerprint = self._receipt_values(receipt)
        old = self._connection.execute(
            "SELECT fingerprint, sequence FROM transcript_receipts WHERE session_id = ? AND segment_id = ?",
            (session_id, identifier),
        ).fetchone()
        if old is not None:
            if old["fingerprint"] == fingerprint and old["sequence"] == sequence:
                return
            raise RepositoryError("Transcript receipt conflicts with an existing turn.")
        self._connection.execute(
            "INSERT INTO transcript_receipts (session_id, segment_id, sequence, fingerprint) VALUES (?, ?, ?, ?)",
            (session_id, identifier, sequence, fingerprint),
        )

    def save(self, session: Session, receipt: dict[str, Any] | None = None):
        """Atomically save a detached session snapshot and optional transcript receipt."""
        try:
            payload = redact_session(session).model_dump_json()
            with self._lock, self._connection:
                self._connection.execute(
                    "INSERT INTO sessions(session_id, payload, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(session_id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (session.session_id, payload, datetime.now(timezone.utc).isoformat()),
                )
                if receipt is not None:
                    self._insert_receipt(session.session_id, receipt)
        except RepositoryError:
            raise
        except (sqlite3.Error, ValueError, TypeError):
            raise RepositoryError("Session changes could not be saved.") from None

    def load_all(self) -> list[Session]:
        try:
            with self._lock:
                rows = self._connection.execute("SELECT payload FROM sessions ORDER BY updated_at DESC").fetchall()
            sessions = []
            for row in rows:
                session = Session.model_validate_json(row["payload"])
                interrupted = session.audio_status != "stopped" or session.analysis_pending
                session.audio_status = "stopped"
                session.analysis_pending = False
                if interrupted:
                    session.provider_notice = "Session restored after interruption. Microphone is stopped; continue with a new finalized turn."
                sessions.append(session)
            return sessions
        except (sqlite3.Error, ValidationError, ValueError):
            raise RepositoryError("Stored sessions could not be restored.") from None

    def delete(self, session_id: str):
        try:
            with self._lock, self._connection:
                self._connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        except sqlite3.Error:
            raise RepositoryError("Session could not be deleted.") from None

    def segment_receipt(self, session_id: str, segment_id: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT fingerprint, sequence FROM transcript_receipts WHERE session_id = ? AND segment_id = ?",
                    (session_id, segment_id),
                ).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error:
            raise RepositoryError("Transcript receipt could not be read.") from None

    def sequence_exists(self, session_id: str, sequence: int) -> bool:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT 1 FROM transcript_receipts WHERE session_id = ? AND sequence = ?", (session_id, sequence),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            raise RepositoryError("Transcript sequence could not be read.") from None

    def receipt_count(self, session_id: str) -> int:
        try:
            with self._lock:
                row = self._connection.execute(
                    "SELECT COUNT(*) FROM transcript_receipts WHERE session_id = ?", (session_id,),
                ).fetchone()
            return int(row[0])
        except sqlite3.Error:
            raise RepositoryError("Transcript receipt count could not be read.") from None

    def record_receipt(self, session_id: str, segment_id: str, sequence: int, fingerprint: str):
        try:
            with self._lock, self._connection:
                self._insert_receipt(session_id, {"segment_id": segment_id, "sequence": sequence, "fingerprint": fingerprint})
        except RepositoryError:
            raise
        except sqlite3.Error:
            raise RepositoryError("Transcript receipt could not be saved.") from None

    def close(self):
        with self._lock:
            try:
                self._connection.close()
            except sqlite3.Error:
                raise RepositoryError("Session storage could not be closed.") from None
