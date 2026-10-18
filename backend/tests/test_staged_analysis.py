import asyncio
import json

import pytest

from app.schemas.contracts import AnalysisResult, CreateSession, TranscriptInput, ActionInput, ActionType
from app.services.analysis import AnalysisService, sentence_spans
from app.services.member_data import MemberData
from app.services.sessions import SessionService
from test_analysis import event, service, session


async def test_initial_model_sees_all_eligible_history_and_grounded_summary():
    analyzer, generate, _, _ = service({"summary": [
        {"text": "An outgoing payment was recorded.", "evidence": [
            {"source_type": "event", "source_id": "E0", "quote": '"transaction_direction": "Outgoing"'}]},
        {"text": "Invented claim", "evidence": [
            {"source_type": "event", "source_id": "missing", "quote": "not supplied"}]},
    ], "insights": []})
    current = session("PRIVATE CONVERSATION")
    current.payment.purpose = "PRIVATE PURPOSE"
    events = [event(event_id=f"E{i}") for i in range(45)]
    events += [event(event_id="FUTURE", timestamp="2027-01-01T00:00:00Z"), event(member_id="OTHER")]
    baseline = await analyzer.initialize(current, events)
    call = generate.await_args.kwargs
    context = json.loads(call["contents"])
    assert call["model"] == "gemini-3.8-flash"
    assert len(context["events"]) == baseline.event_count == 45
    assert "PRIVATE" not in call["contents"]
    assert len(baseline.summary) == 1 and baseline.provider == "gemini"
    assert "1 unsupported" in baseline.notice
    current.initial_analysis = baseline
    generate.return_value.text = '{"insights": []}'
    await analyzer.analyze(current, events)
    call = generate.await_args.kwargs
    context = json.loads(call["contents"])
    assert call["model"] == "gemini-3.5-flash-lite"
    assert context["initial_review"]["summary"][0]["text"] == baseline.summary[0].text
    assert [e["event_id"] for e in context["events"]] == ["E0"]
    assert context["new_information"]["text"] == "PRIVATE CONVERSATION"


@pytest.mark.parametrize("text, expected", [
    ("Send today. Keep it secret! Why?", ["Send today.", "Keep it secret!", "Why?"]),
    ("Dr. Lee sent $1,500.50. Check it", ["Dr. Lee sent $1,500.50.", "Check it"]),
    ("No punctuation here", ["No punctuation here"]),
    ('They said “send now.” I waited.', ['They said “send now.”', 'I waited.']),
])
def test_finalized_sentence_boundaries(text, expected):
    assert [text[start:end].strip() for start, end in sentence_spans(text)] == expected


async def test_each_sentence_waits_for_one_initial_review_and_survives_restart(config):
    coordinator = SessionService(config, MemberData(config.data_dir))
    current = coordinator.create(CreateSession(member_id="M1002", consent=True, retain_transcript=True))
    entered, release = asyncio.Event(), asyncio.Event()

    class Controlled(AnalysisService):
        starts = 0
        updates = []

        async def initialize(self, snapshot, events):
            self.starts += 1
            entered.set()
            await release.wait()
            return await super().initialize(snapshot, events)

        async def analyze(self, snapshot, events):
            assert snapshot.initial_analysis is not None
            self.updates.append(snapshot.segments[-1].text)
            return AnalysisResult(), "demo", "demo update"

    analyzer = Controlled(config)
    coordinator.analysis = analyzer
    coordinator.start_analysis(current)
    await asyncio.wait_for(entered.wait(), 2)
    first = TranscriptInput(sequence=0, speaker="member", text="First sentence. Second sentence.")
    await coordinator.transcript(current.session_id, first)
    await coordinator.transcript(current.session_id, TranscriptInput(sequence=1, speaker="member", text="Third sentence."))
    assert analyzer.starts == 1 and analyzer.updates == []
    release.set()
    await coordinator.tasks[current.session_id]
    assert analyzer.starts == 1
    assert analyzer.updates == ["First sentence.", "First sentence. Second sentence.", "Third sentence."]
    assert len(current.analyzed_sentences) == 3 and not current.analysis_pending
    await coordinator.transcript(current.session_id, first)
    assert len(analyzer.updates) == 3
    baseline = current.initial_analysis.model_dump()
    await coordinator.shutdown()

    restored = SessionService(config, MemberData(config.data_dir))
    loaded = restored.get(current.session_id)
    assert loaded.initial_analysis.model_dump() == baseline
    assert len(loaded.analyzed_sentences) == 3
    await restored.shutdown()


async def test_teller_speech_and_interim_text_do_not_trigger_sentence_analysis(config):
    coordinator = SessionService(config, MemberData(config.data_dir))
    current = coordinator.create(CreateSession(member_id="M1002", consent=True, mode="live"))
    await coordinator.transcript(current.session_id, TranscriptInput(sequence=0, speaker="teller", text="Were you pressured?"))
    await coordinator.tasks[current.session_id]
    assert current.initial_analysis is not None
    assert current.analyzed_sentences == [] and not current.analysis_pending
    assert current.analysis_provider == "demo" and "fallback" in current.provider_notice
    await coordinator.shutdown()


async def test_late_initial_review_cannot_undo_legitimate_decision(config):
    coordinator = SessionService(config, MemberData(config.data_dir))
    current = coordinator.create(CreateSession(member_id="M1002", consent=True))
    entered, release = asyncio.Event(), asyncio.Event()

    class Delayed(AnalysisService):
        async def initialize(self, snapshot, events):
            entered.set()
            await release.wait()
            return await super().initialize(snapshot, events)

    coordinator.analysis = Delayed(config)
    coordinator.start_analysis(current)
    await asyncio.wait_for(entered.wait(), 2)
    await coordinator.action(current.session_id, ActionInput(action_type=ActionType.MARK_LEGITIMATE, note="Verified independently."))
    task = coordinator.initial_tasks[current.session_id]
    release.set()
    await task
    assert current.review_outcome == "legitimate" and current.attention.score == 0
    assert current.insights == [] and not current.analysis_pending
    assert current.initial_analysis is not None
    await coordinator.shutdown()


async def test_initial_fallback_is_labeled_and_contains_no_conversation(config):
    current = session("PRIVATE CONVERSATION", mode="live")
    current.payment.purpose = "PRIVATE PURPOSE"
    analyzer = AnalysisService(config)
    baseline = await analyzer.initialize(current, [event()])
    assert baseline.provider == "demo" and baseline.model is None
    assert "fallback" in baseline.notice and baseline.summary
    assert "PRIVATE" not in baseline.model_dump_json()
