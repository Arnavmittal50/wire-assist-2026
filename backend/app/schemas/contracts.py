"""REST and WebSocket wire contracts. Text from people/providers is untrusted."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator


def now() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return str(uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionType(StrEnum):
    ASK_FOLLOW_UP = "ASK_FOLLOW_UP"
    OFFER_VERIFICATION = "OFFER_VERIFICATION"
    OFFER_SPECIALIST = "OFFER_SPECIALIST"
    CONSIDER_HOLD = "CONSIDER_HOLD"
    OFFER_TRUSTED_CONTACT = "OFFER_TRUSTED_CONTACT"
    ESCALATE_CASE = "ESCALATE_CASE"
    CONTINUE_REVIEW = "CONTINUE_REVIEW"
    NO_ACTION = "NO_ACTION"
    ACKNOWLEDGE = "ACKNOWLEDGE"
    DISMISS = "DISMISS"
    RESOLVE_SIGNAL = "RESOLVE_SIGNAL"
    DISMISS_SIGNAL = "DISMISS_SIGNAL"
    MARK_LEGITIMATE = "MARK_LEGITIMATE"


class Payment(StrictModel):
    amount: float = Field(default=1500, gt=0, le=10000000, allow_inf_nan=False)
    rail: Literal["Wire", "ACH", "External Transfer", "P2P", "Cash"] = "Wire"
    recipient_id: str = Field(default="R1002-09", max_length=100)
    purpose: str = Field(default="", max_length=500)


class CreateSession(StrictModel):
    member_id: str = Field(min_length=1, max_length=50)
    as_of: datetime = datetime(2026, 9, 27, 2, 25, tzinfo=timezone.utc)
    consent: bool
    retain_transcript: bool = False
    mode: Literal["demo", "live"] = "demo"
    payment: Payment = Field(default_factory=Payment)

    @field_validator("as_of")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("as_of must include a timezone")
        return value.astimezone(timezone.utc)


class TranscriptInput(StrictModel):
    segment_id: str = Field(default_factory=uid, min_length=1, max_length=100)
    sequence: int = Field(ge=0, le=1000000)
    text: str = Field(min_length=1, max_length=4000)
    speaker: Literal["member", "teller", "unknown", "auto"] = "unknown"

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text cannot be blank")
        return value.strip()


class Segment(TranscriptInput):
    timestamp: datetime = Field(default_factory=now)
    source: Literal["manual", "gemini"] = "manual"
    finalized: bool = True
    speaker_source: Literal["manual", "automatic", "diarization"] = "manual"
    session_id: str = ""
    recording_id: str | None = None
    start_time: float = Field(default=0, ge=0)
    end_time: float = Field(default=0, ge=0)
    timing_source: Literal["provider", "estimated", "manual"] = "manual"
    speaker_id: str | None = None
    speaker_role: Literal["teller", "member", "unknown"] = "unknown"
    transcription_source: Literal["manual", "gemini_live"] = "manual"
    diarization_status: Literal["PENDING", "CONFIRMED", "UNCERTAIN", "FAILED"] = "UNCERTAIN"
    revision: int = 0

    @model_validator(mode="before")
    @classmethod
    def legacy_manual_labels(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            role = data.get("speaker", "unknown")
            data.setdefault("speaker_role", role if role in {"member", "teller"} else "unknown")
            if (data.get("source", "manual") == "manual" and data.get("speaker_source", "manual") == "manual"
                    and role in {"member", "teller"}):
                data.setdefault("diarization_status", "CONFIRMED")
        return data


class SpeakerCorrection(StrictModel):
    speaker: Literal["teller", "member", "unknown"]
    scope: Literal["segment", "speaker"] = "segment"


class Evidence(StrictModel):
    source_type: Literal["segment", "event"]
    source_id: str = Field(max_length=100)
    quote: str = Field(min_length=1, max_length=1200)


class SignalObservation(StrictModel):
    signal_type: Literal["LARGE_TRANSACTION", "POSSIBLE_IMPERSONATION", "IDENTITY_VERIFICATION_NEEDED",
                         "TIME_PRESSURE", "SECRECY", "INVESTMENT_RISK", "UNUSUAL_PAYMENT_PATTERN"]
    status: Literal["OBSERVED", "POSSIBLE", "NOT_ESTABLISHED"]
    source_type: Literal["segment", "event"]
    source_id: str = Field(min_length=1, max_length=100)
    evidence_quote: str = Field(min_length=1, max_length=1200)
    explanation: str = Field(min_length=1, max_length=1200)
    suggested_question: str = Field(min_length=1, max_length=600)


class Insight(StrictModel):
    insight_id: str = Field(default_factory=uid)
    title: str = Field(max_length=140)
    observation: str = Field(max_length=1200)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)
    source_segment_ids: list[str] = Field(default_factory=list, max_length=8)
    source_event_ids: list[str] = Field(default_factory=list, max_length=8)
    suggested_question: str = Field(max_length=600)
    recommended_action: ActionType
    priority: Literal["low", "medium", "high"]
    confidence_in_observation: float = Field(ge=0, le=1)
    requires_human_review: bool = True
    signal_codes: list[str] = Field(default_factory=list, max_length=8)
    signals: list[SignalObservation] = Field(default_factory=list, max_length=7)
    status: Literal["active", "acknowledged", "dismissed", "resolved", "superseded"] = "active"
    provider: Literal["demo", "gemini"] = "demo"


class AnalysisResult(StrictModel):
    insights: list[Insight] = Field(default_factory=list, max_length=3)


class SummaryFact(StrictModel):
    text: str = Field(min_length=1, max_length=600)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)


class InitialAnalysisResult(AnalysisResult):
    summary: list[SummaryFact] = Field(default_factory=list, max_length=6)


class InitialAnalysis(InitialAnalysisResult):
    provider: Literal["demo", "gemini", "unavailable"]
    model: str | None = None
    notice: str
    event_count: int


class Signal(StrictModel):
    signal_id: str
    code: str
    title: str
    points: int
    evidence: list[Evidence]
    status: Literal["active", "dismissed", "resolved"] = "active"
    origin: Literal["history", "conversation"]


class Attention(StrictModel):
    score: int = 0
    level: Literal["routine", "review", "elevated"] = "routine"
    signals: list[Signal] = Field(default_factory=list)
    explanation: str = "Illustrative attention score, not a fraud probability."
    change: str = "Session opened."


class ActionInput(StrictModel):
    action_type: ActionType
    insight_id: str | None = Field(default=None, max_length=100)
    signal_id: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=2000)


class TellerAction(ActionInput):
    action_id: str = Field(default_factory=uid)
    timestamp: datetime = Field(default_factory=now)
    simulated: bool = True


class Activity(StrictModel):
    event_id: str = Field(default_factory=uid)
    timestamp: datetime = Field(default_factory=now)
    description: str


class Session(StrictModel):
    session_id: str = Field(default_factory=uid)
    member_id: str
    as_of: datetime
    consent: bool
    retain_transcript: bool
    mode: Literal["demo", "live"]
    payment: Payment
    created_at: datetime = Field(default_factory=now)
    ended_at: datetime | None = None
    status: Literal["active", "ended"] = "active"
    audio_status: Literal["stopped", "recording", "paused"] = "stopped"
    revision: int = 0
    analysis_version: int = 0
    analysis_pending: bool = False
    initial_analysis: InitialAnalysis | None = None
    analyzed_sentences: list[str] = Field(default_factory=list)
    analysis_provider: Literal["demo", "gemini", "unavailable"] = "demo"
    analysis_sources: list[Literal["demo", "gemini", "unavailable"]] = Field(default_factory=list)
    provider_notice: str = "Deterministic demo analysis. No model has been called."
    speaker_notice: str = ""
    speaker_roles: dict[str, Literal["teller", "member", "unknown"]] = Field(default_factory=dict)
    audio_clock_seconds: float = 0
    diarization_pending: int = 0
    diarization_notice: str = ""
    guidance_status: Literal["idle", "waiting_for_speaker", "reviewing", "ready", "unavailable"] = "idle"
    data_source: str
    segments: list[Segment] = Field(default_factory=list)
    segment_count: int = 0
    next_sequence: int = 0
    insights: list[Insight] = Field(default_factory=list)
    attention: Attention = Field(default_factory=Attention)
    actions: list[TellerAction] = Field(default_factory=list)
    activity: list[Activity] = Field(default_factory=list)
    review_outcome: Literal["in_review", "legitimate", "needs_follow_up"] = "in_review"


class Envelope(StrictModel):
    type: str
    session_id: str
    event_id: str = Field(default_factory=uid)
    timestamp: datetime = Field(default_factory=now)
    payload: dict


class AudioControl(StrictModel):
    command: Literal["start", "pause", "resume", "stop"]
    speaker: Literal["member", "teller", "unknown", "auto"] = "auto"


class SessionSummary(StrictModel):
    session_id: str
    member_id: str
    status: str
    review_outcome: str
    created_at: datetime
    ended_at: datetime | None
    payment: Payment
    attention: Attention
    transcript_segment_count: int
    transcript_retained: bool
    analysis_providers: list[str]
    initial_analysis: InitialAnalysis | None = None
    observations: list[str]
    unresolved_signals: list[str]
    actions: list[TellerAction]
    notice: str = "Decision support only. Employee payment actions are simulated; no funds moved."
