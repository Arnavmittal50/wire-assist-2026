import json

import pytest

from app.schemas.contracts import CreateSession, Payment, Segment, TranscriptInput
from app.services.member_data import MemberData
from app.services.risk_engine import calculate_attention
from app.services.sessions import SessionService
from app.services.signal_validation import TITLES, detect_observations
from test_analysis import insight, service, session


DIALOGUE = [
    ("teller", "What's the purpose of your wire transfer today?"),
    ("member", "I'd like to transfer a million dollars."),
    ("teller", "Who would you like to transfer this money to?"),
    ("member", "My brother."),
    ("teller", "Are you sure it's your brother?"),
    ("member", "He gave me a phone call. It sounded like my brother."),
    ("teller", "Do you have verifiable proof?"),
    ("member", "He also sent me a selfie."),
]
URGENT = "He said I have to send the money in the next 30 minutes or something terrible will happen."


def model_insight(kind, quote, *, status="OBSERVED", source_id="s0"):
    return insight(title=TITLES[kind], observation=TITLES[kind], signal_codes=[kind],
                   evidence=[dict(source_type="segment", source_id=source_id, quote=quote)],
                   signals=[dict(signal_type=kind, status=status, source_type="segment",
                                 source_id=source_id, evidence_quote=quote,
                                 explanation=TITLES[kind], suggested_question="Could we verify the details?")])


@pytest.mark.parametrize('mode', ['demo', 'live'])
async def test_family_conversation_has_identity_concern_without_urgency_secrecy_investment(mode):
    expected = model_insight('IDENTITY_VERIFICATION_NEEDED', DIALOGUE[7][1], source_id='s7')
    analyzer, _, _, _ = service({'insights': [expected]})
    current = session(mode=mode)
    current.segments = [Segment(segment_id=f's{i}', sequence=i, speaker=role, text=text)
                        for i, (role, text) in enumerate(DIALOGUE)]
    result, _, _ = await analyzer.analyze(current, [])
    kinds = {s.signal_type for i in result.insights for s in i.signals if s.status != 'NOT_ESTABLISHED'}
    assert 'IDENTITY_VERIFICATION_NEEDED' in kinds
    assert not kinds & {'TIME_PRESSURE', 'SECRECY', 'INVESTMENT_RISK'}
    assert calculate_attention([], Payment(), result.insights).score == 0
    if mode == 'demo':
        assert {'LARGE_TRANSACTION', 'POSSIBLE_IMPERSONATION'} <= kinds
        assert all(s.status == 'POSSIBLE' for i in result.insights for s in i.signals if s.signal_type == 'POSSIBLE_IMPERSONATION')


@pytest.mark.parametrize('mode', ['demo', 'live'])
async def test_explicit_thirty_minute_threat_is_time_pressure(mode):
    analyzer, _, _, _ = service({'insights': [model_insight('TIME_PRESSURE', URGENT)]})
    result, _, _ = await analyzer.analyze(session(URGENT, mode=mode), [])
    assert any(s.signal_type == 'TIME_PRESSURE' and s.status == 'OBSERVED' for i in result.insights for s in i.signals)
    assert calculate_attention([], Payment(), result.insights).score >= 8


@pytest.mark.parametrize('kind', ['TIME_PRESSURE', 'SECRECY', 'INVESTMENT_RISK'])
@pytest.mark.parametrize('quote', [DIALOGUE[1][1], DIALOGUE[5][1], DIALOGUE[7][1]])
async def test_model_cannot_attach_unrelated_signal_to_real_quote(kind, quote):
    analyzer, _, _, _ = service({'insights': [model_insight(kind, quote)]}, review_result={'insights': []})
    result, provider, _ = await analyzer.analyze(session(quote), [])
    assert provider == 'gemini' and result.insights == []


@pytest.mark.parametrize('role', ['teller', 'unknown', 'auto'])
async def test_only_member_evidence_can_support_signal(role):
    analyzer, _, _, _ = service({'insights': [model_insight('TIME_PRESSURE', URGENT)]}, review_result={'insights': []})
    result, _, _ = await analyzer.analyze(session(URGENT, speaker=role), [])
    assert result.insights == []


@pytest.mark.parametrize('text', [
    'I want to send a million dollars today.',
    'I visited the bank today.',
    'There is no deadline.',
    'Nobody gave me a deadline.',
    'Hypothetically, I have to send the money in the next 30 minutes.',
    'Do I have to send the money in the next 30 minutes?',
])
async def test_ordinary_timing_negation_and_hypothetical_do_not_establish_urgency(text):
    observations = detect_observations({'s0': text}, {})
    assert not any(s.signal_type == 'TIME_PRESSURE' and s.status == 'OBSERVED' for s in observations)
    analyzer, _, _, _ = service()
    result, _, _ = await analyzer.analyze(session(text, mode='demo'), [])
    assert not any(c in {'URGENCY', 'TIME_PRESSURE'} for i in result.insights for c in i.signal_codes)


async def test_clipped_negation_rejected():
    analyzer, _, _, _ = service({'insights': [model_insight('TIME_PRESSURE', 'deadline')]}, review_result={'insights': []})
    result, _, _ = await analyzer.analyze(session('There is no deadline.'), [])
    assert not result.insights


async def test_provider_cannot_revive_an_old_quote_after_member_contradicts_it():
    analyzer, _, _, _ = service({'insights': [model_insight('TIME_PRESSURE', URGENT)]}, review_result={'insights': []})
    result, _, _ = await analyzer.analyze(session(URGENT, 'There is no deadline now. I can take my time.'), [])
    assert not result.insights


async def test_unsupported_claim_without_signal_code_rejected():
    item = insight(title='Time pressure', observation='Urgency is present.', signal_codes=[],
                   evidence=[dict(source_type='segment', source_id='s0', quote=DIALOGUE[1][1])])
    analyzer, _, _, _ = service({'insights': [item]}, review_result={'insights': []})
    result, _, _ = await analyzer.analyze(session(DIALOGUE[1][1]), [])
    assert not result.insights


async def test_possible_status_cannot_score_even_with_observed_code():
    analyzer, _, _, _ = service({'insights': [model_insight('TIME_PRESSURE', URGENT, status='POSSIBLE')]})
    result, _, _ = await analyzer.analyze(session(URGENT), [])
    assert result.insights[0].signals[0].status == 'POSSIBLE'
    assert calculate_attention([], Payment(), result.insights).score == 0


async def test_legacy_provider_hypothesis_does_not_bypass_status_scoring():
    item = insight(title='Possible time pressure', observation='The member might be under time pressure.',
                   signal_codes=['URGENCY'], evidence=[dict(source_type='segment', source_id='s0', quote=URGENT)])
    analyzer, _, _, _ = service({'insights': [item]})
    result, _, _ = await analyzer.analyze(session(URGENT), [])
    assert result.insights[0].signal_codes == []
    assert calculate_attention([], Payment(), result.insights).score == 0


async def test_impersonation_is_never_confirmed_by_voice_or_selfie():
    quote = 'It sounded like my brother.'
    analyzer, _, _, _ = service({'insights': [model_insight('POSSIBLE_IMPERSONATION', quote)]})
    result, _, _ = await analyzer.analyze(session(quote), [])
    assert result.insights[0].signals[0].status == 'POSSIBLE'
    assert calculate_attention([], Payment(), result.insights).score == 0


async def test_later_contradiction_removes_earlier_attention(config):
    coordinator = SessionService(config, MemberData(config.data_dir))
    current = coordinator.create(CreateSession(member_id='M1002', consent=True))
    baseline = current.attention.score
    try:
        await coordinator.transcript(current.session_id, TranscriptInput(sequence=0, text=URGENT, speaker='member'))
        await coordinator.tasks[current.session_id]
        assert current.attention.score > baseline
        await coordinator.transcript(current.session_id, TranscriptInput(sequence=1, text='There is no deadline. I can take my time.', speaker='member'))
        await coordinator.tasks[current.session_id]
        assert not any(s.code in {'TIME_PRESSURE', 'URGENCY'} and s.status == 'active' for s in current.attention.signals)
        assert current.attention.score == baseline
        # A new explicit demand can establish the signal again; automatic
        # reassessment must not masquerade as a permanent employee dismissal.
        await coordinator.transcript(current.session_id, TranscriptInput(sequence=2, text=URGENT, speaker='member'))
        await coordinator.tasks[current.session_id]
        assert any(s.code == 'TIME_PRESSURE' and s.status == 'active' for s in current.attention.signals)
    finally:
        await coordinator.shutdown()










async def test_speaker_correction_removes_teller_words_from_risk_evidence(config):
    coordinator = SessionService(config, MemberData(config.data_dir))
    current = coordinator.create(CreateSession(member_id='M1002', consent=True))
    baseline = current.attention.score
    try:
        await coordinator.transcript(current.session_id, TranscriptInput(segment_id='incorrect', sequence=0, speaker='member', text=URGENT))
        await coordinator.tasks[current.session_id]
        assert current.attention.score > baseline
        await coordinator.correct_speaker(current.session_id, 'incorrect', 'teller')
        await coordinator.tasks[current.session_id]
        assert current.segments[0].speaker == 'teller' and current.segments[0].speaker_source == 'manual'
        assert current.attention.score == baseline
        assert all(item.status == 'superseded' for item in current.insights if item.source_segment_ids)
    finally:
        await coordinator.shutdown()


def test_record_amount_and_baseline_are_distinct_categories():
    record = json.dumps({'amount': 1000000, 'amount_to_baseline_ratio': 5})
    kinds = {s.signal_type for s in detect_observations({}, {'e1': record})}
    assert kinds == {'LARGE_TRANSACTION', 'UNUSUAL_PAYMENT_PATTERN'}


@pytest.mark.parametrize('text', [
    'I took a selfie on vacation.', 'It sounded like a good idea.',
    'My house is worth a million dollars.', 'I am not sending $1,000,000.',
])
def test_unrelated_photos_sounds_and_assets_are_not_payment_risk(text):
    assert detect_observations({'s0': text}, {}) == []


async def test_contextual_paraphrase_is_not_overruled_by_keyword_rules(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Live analysis must not call demo keyword classification')
    monkeypatch.setattr('app.services.analysis._demo', forbidden)
    monkeypatch.setattr('app.services.analysis._detect_codes', forbidden)
    monkeypatch.setattr('app.services.signal_validation.supported', forbidden)
    quote = "If this isn't settled before lunch, they say I'll lose my place and everything I've already put in."
    analyzer, generate, _, _ = service({'insights': [model_insight('TIME_PRESSURE', quote)]})
    result, provider, _ = await analyzer.analyze(session(quote), [])
    assert provider == 'gemini' and result.insights[0].signal_codes == ['TIME_PRESSURE']
    assert calculate_attention([], Payment(), result.insights).score == 8
    assert generate.await_count == 2
    reviewer_data = json.loads(generate.await_args.kwargs['contents'])
    assert reviewer_data['context']['finalized_segments'][0]['text'] == quote
    assert reviewer_data['proposed_assessment']['insights']


async def test_model_review_replaces_old_signals_without_keyword_denial(config):
    from app.schemas.contracts import AnalysisResult, InitialAnalysis
    from app.services.analysis import AnalysisService
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    coordinator = SessionService(config, MemberData(config.data_dir))
    current = coordinator.create(CreateSession(member_id='M1002', consent=True, mode='live'))
    baseline = current.attention.score
    first = AnalysisResult.model_validate({'insights': [model_insight('TIME_PRESSURE', URGENT)]})
    coordinator.analysis = SimpleNamespace(
        initialize=AsyncMock(return_value=InitialAnalysis(provider='gemini', model='test', notice='test', event_count=0)),
        analyze=AsyncMock(side_effect=[(first, 'gemini', 'first review'), (AnalysisResult(), 'gemini', 'reassessed')]))
    try:
        await coordinator.transcript(current.session_id, TranscriptInput(sequence=0, speaker='member', text=URGENT))
        await coordinator.tasks[current.session_id]
        assert current.attention.score == baseline + 8
        await coordinator.transcript(current.session_id, TranscriptInput(sequence=1, speaker='member', text='The situation changed after we spoke again'))
        await coordinator.tasks[current.session_id]
        assert current.attention.score == baseline
        assert all(i.status == 'superseded' for i in current.insights)
    finally:
        await coordinator.shutdown()
