import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.contracts import AnalysisResult, Session, Segment, Payment
from app.services.groq_analysis import GroqAnalysisService, compact_context, response_format, canonical_record_quotes
from app.services.risk_engine import calculate_attention


def session():
    return Session(member_id='M1002', as_of='2026-09-27T02:25:00Z', consent=True,
                   retain_transcript=True, mode='live', payment=Payment(amount=1000000), data_source='test')


def insight(kind, quote, source='s1', status='OBSERVED'):
    return {'title': 'Verify the recipient', 'observation': 'Independent verification is needed.',
            'evidence': [{'source_type': 'segment', 'source_id': source, 'quote': quote}],
            'suggested_question': 'How did you independently verify the recipient?',
            'recommended_action': 'OFFER_VERIFICATION', 'priority': 'high', 'confidence_in_observation': 0.8,
            'signals': [{'signal_type': kind, 'status': status, 'source_type': 'segment', 'source_id': source,
                         'evidence_quote': quote, 'explanation': 'This describes the concern.',
                         'suggested_question': 'How did you independently verify the recipient?'}]}


def provider(config, payload):
    config.groq_api_key = 'synthetic-key'
    config.groq_min_interval_seconds = 0
    raw = SimpleNamespace(headers={'x-ratelimit-limit-tokens': '8000'},
                          parse=AsyncMock(return_value=SimpleNamespace(usage=SimpleNamespace(total_tokens=1500),
                              choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])))
    create = AsyncMock(return_value=raw)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))),
                             close=AsyncMock())
    return GroqAnalysisService(config, lambda **kwargs: client), create, client


@pytest.mark.asyncio
async def test_live_track_evidence_and_no_speculative_scoring(config):
    s = session()
    s.segments = [Segment(segment_id='s1', sequence=0, speaker='member', text='He sounded like my brother.',
                          speaker_source='twilio_track', source='gemini', diarization_status='CONFIRMED')]
    service, create, client = provider(config, {'insights': [insight('POSSIBLE_IMPERSONATION', s.segments[0].text, status='POSSIBLE')]})
    result, name, _ = await service.analyze(s, [])
    assert name == 'groq' and result.insights[0].provider == 'groq'
    assert result.insights[0].signal_codes == []
    assert calculate_attention([], s.payment, result.insights).score == calculate_attention([], s.payment, []).score
    assert create.call_args.kwargs['response_format']['json_schema']['strict']
    client.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('speaker,quote', [('teller', 'Are you under time pressure?'), ('member', 'Quote not in the transcript')])
async def test_unsupported_or_teller_evidence_suppressed(config, speaker, quote):
    s = session()
    s.segments = [Segment(segment_id='s1', sequence=0, speaker=speaker, text='Are you under time pressure?')]
    service, _, _ = provider(config, {'insights': [insight('TIME_PRESSURE', quote)]})
    result, name, _ = await service.analyze(s, [])
    assert name == 'groq' and result.insights == []


@pytest.mark.asyncio
async def test_positive_urgency_and_no_duplicate_points(config):
    s = session()
    quote = 'He said I have to send the money in the next 30 minutes or something terrible will happen.'
    s.segments = [Segment(segment_id='s1', sequence=0, speaker='member', text=quote)]
    service, _, _ = provider(config, {'insights': [insight('TIME_PRESSURE', quote)]})
    result, _, _ = await service.analyze(s, [])
    one = calculate_attention([], s.payment, result.insights)
    two = calculate_attention([], s.payment, result.insights * 2)
    assert one.score == two.score and one.score > 0


@pytest.mark.asyncio
async def test_pre_analysis_citations_cache_and_future_exclusion(config):
    s = session()
    events = [{'event_id': 'e1', 'member_id': 'M1002', 'timestamp': '2026-09-01T00:00:00Z', 'event_type': 'Payment Initiated', 'amount': 500},
              {'event_id': 'future', 'member_id': 'M1002', 'timestamp': '2027-01-01T00:00:00Z', 'event_type': 'Scam Confirmed', 'scam_label': 'hidden'}]
    payload = {'insights': [], 'summary': [{'text': 'One payment for $500 is recorded.', 'evidence': [
        {'source_type': 'event', 'source_id': 'e1', 'quote': '"amount": 500'}]}]}
    service, create, _ = provider(config, payload)
    review = await service.initialize(s, events)
    assert review.provider == 'groq' and len(review.summary) == 1
    assert review.event_count == 1
    assert 'Scam Confirmed' not in create.call_args.kwargs['messages'][1]['content']
    await service.initialize(s, events)
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_malformed_and_provider_failure_nonblocking(config):
    s = session()
    service, create, client = provider(config, {'malformed': True})
    result, name, _ = await service.analyze(s, [])
    assert name == 'unavailable' and not result.insights and s.status == 'active'
    service.next_requests.clear()
    create.side_effect = RuntimeError('synthetic secret must not be shown')
    _, name, notice = await service.analyze(s, [])
    assert name == 'unavailable' and 'synthetic secret' not in notice


def test_rolling_context_and_json_mode():
    s = session()
    s.segments = [Segment(sequence=i, speaker='member', text=f'Utterance {i}.') for i in range(40)]
    context, _, _ = compact_context(s, [], False)
    assert len(context['conversation']) == 24
    assert response_format(AnalysisResult, 'another-supported-json-model') == {'type': 'json_object'}


def test_record_excerpt_requires_exact_typed_values():
    record = '{"amount": 1500.0, "event_type": "Payment Initiated", "new_recipient": true}'
    def result(quote):
        return {'summary': [{'evidence': [{'source_type': 'event', 'source_id': 'e1', 'quote': quote}]}]}
    correct = canonical_record_quotes(result('{"new_recipient": true, "amount": 1500.0}'), {'e1': record})
    assert correct['summary'][0]['evidence'][0]['quote'] == record
    for fake in ('{"amount": 1000000.0}', '{"new_recipient": 1}', '{"invented": true}'):
        checked = canonical_record_quotes(result(fake), {'e1': record})
        assert checked['summary'][0]['evidence'][0]['quote'] == fake


@pytest.mark.asyncio
async def test_same_recommendation_does_not_reset_acknowledgement(config):
    from app.schemas.contracts import CreateSession, InitialAnalysis, TranscriptInput
    from app.services.member_data import MemberData
    from app.services.sessions import SessionService
    config.groq_debounce_seconds = 0
    service = SessionService(config, MemberData(config.data_dir))
    s = service.create(CreateSession(member_id='M1002', consent=True, mode='live'))
    s.initial_analysis = InitialAnalysis(provider='groq', notice='mocked', event_count=0)
    quote = 'He sounded like my brother.'
    result = AnalysisResult.model_validate({'insights': [insight('IDENTITY_VERIFICATION_NEEDED', quote)]})
    async def analyze(*args): return result.model_copy(deep=True), 'groq', 'mocked'
    service.analysis.analyze = analyze
    await service.transcript(s.session_id, TranscriptInput(segment_id='s1', sequence=0, speaker='member', text=quote))
    await service.tasks[s.session_id]
    s.insights[0].status = 'acknowledged'
    await service.transcript(s.session_id, TranscriptInput(segment_id='s2', sequence=1, speaker='member', text='He sent a photo too.'))
    await service.tasks[s.session_id]
    assert len(s.insights) == 1 and s.insights[0].status == 'acknowledged'
    await service.shutdown()
