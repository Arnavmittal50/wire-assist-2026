from app.services.analysis import AnalysisService
import asyncio

from app.schemas.contracts import AnalysisResult, CreateSession, TranscriptInput
from app.services.member_data import MemberData
from app.services.sessions import SessionService


async def test_finalized_manual_conversation_produces_structured_insight(config):
    service = SessionService(config, MemberData(config.data_dir))
    session = service.create(CreateSession(member_id="M1002", consent=True))
    initial_score = session.attention.score
    text = "I met him online. He says this investment must happen today and I should keep it secret."
    request = TranscriptInput(segment_id="one", sequence=1, text=text, speaker="member")
    await service.transcript(session.session_id, request)
    await service.tasks[session.session_id]
    assert session.analysis_provider == "demo"
    assert session.insights
    assert session.attention.score >= initial_score
    assert all(i.provider == "demo" and i.requires_human_review for i in session.insights)
    assert all(e.quote in text for i in session.insights for e in i.evidence if e.source_type == "segment")
    count, score = len(session.insights), session.attention.score
    await service.transcript(session.session_id, request)
    assert len(session.insights) == count and session.attention.score == score
    assert len(session.segments) == 1
    await service.shutdown()


async def test_old_analysis_response_cannot_overwrite_newer_state(config):
    service = SessionService(config, MemberData(config.data_dir))
    session = service.create(CreateSession(member_id="M1002", consent=True))
    began = asyncio.Event()
    class DelayedAnalysis(AnalysisService):
        async def analyze(self, snapshot, events):
            if snapshot.analysis_version == 1:
                began.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    # Simulate a provider that cannot be cancelled in-flight.
                    return AnalysisResult(), "demo", "obsolete response"
            return AnalysisResult(), "demo", "current response"
    service.analysis = DelayedAnalysis(config)
    await service.transcript(session.session_id, TranscriptInput(sequence=1, text="First turn", speaker="member"))
    old = service.tasks[session.session_id]
    await began.wait()
    await service.transcript(session.session_id, TranscriptInput(sequence=2, text="Second turn", speaker="member"))
    await service.tasks[session.session_id]
    await old
    assert session.provider_notice == "current response"
    assert session.analysis_version == 2
    assert not session.analysis_pending
    await service.shutdown()


async def test_live_without_key_uses_labeled_fallback(config):
    service = SessionService(config, MemberData(config.data_dir))
    session = service.create(CreateSession(member_id="M1002", consent=True, mode="live"))
    await service.transcript(session.session_id, TranscriptInput(sequence=1, text="I met this person online and need to pay today.", speaker="member"))
    await service.tasks[session.session_id]
    assert session.analysis_provider == "unavailable"
    assert "No live classifications" in session.provider_notice
    assert session.insights == []
    await service.shutdown()
