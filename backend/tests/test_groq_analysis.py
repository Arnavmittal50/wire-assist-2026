import json
import asyncio
import time
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
    fragment = canonical_record_quotes(result('"new_recipient": true, "amount": 1500.0'), {'e1': record})
    assert fragment['summary'][0]['evidence'][0]['quote'] == record
    for fake in ('{"amount": 1000000.0}', '{"new_recipient": 1}', '{"invented": true}', '"amount": 1000000.0, "new_recipient": true'):
        checked = canonical_record_quotes(result(fake), {'e1': record})
        assert checked['summary'][0]['evidence'][0]['quote'] == fake


@pytest.mark.asyncio
async def test_large_relative_to_baseline_is_not_large_transaction_category(config):
    s = session()
    s.payment.amount = 51586.52
    item = insight('LARGE_TRANSACTION', '51586.52', source='proposed_payment')
    item['signals'][0]['source_type'] = 'event'
    item['evidence'][0]['source_type'] = 'event'
    service, _, _ = provider(config, {'insights': [item]})
    result, name, _ = await service.analyze(s, [])
    assert name == 'groq'
    assert all(not i.signals and not i.signal_codes for i in result.insights)


@pytest.mark.asyncio
@pytest.mark.parametrize('amount,expected', [('60,000', []), ('60k', []),
                                           ('100,000', ['LARGE_TRANSACTION']), ('1 million', ['LARGE_TRANSACTION'])])
async def test_spoken_dollar_amount_respects_large_transaction_threshold(config, amount, expected):
    s = session()
    quote = f'It is going to be ${amount} for a house down payment.'
    s.segments = [Segment(segment_id='s1', sequence=0, speaker='member', text=quote)]
    service, _, _ = provider(config, {'insights': [insight('LARGE_TRANSACTION', quote)]})
    result, name, _ = await service.analyze(s, [])
    assert name == 'groq' and result.insights[0].signal_codes == expected


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


def test_compact_history_keeps_every_eligible_row_without_repeated_field_names(config):
    from app.services.member_data import MemberData
    from app.services.analysis import _context
    s = session()
    events = MemberData(config.data_dir).events(s.member_id, s.as_of)
    context, _, records = compact_context(s, events, True)
    full, _, _ = _context(s, events, all_history=True)
    assert len(events) == 37
    assert len(context['events']['rows']) == 35  # Current request and its new-recipient setup are not prior history.
    assert {row[0] for row in context['events']['rows']} == set(records)
    assert len(json.dumps(context)) < len(json.dumps(full)) / 2
    for row in context['events']['rows']:
        original = next(e for e in events if e['event_id'] == row[0])
        for field, value in zip(context['events']['columns'][1:], row[1:]):
            if value is not None:
                assert original[field] == value


@pytest.mark.asyncio
async def test_concurrent_identical_history_reviews_share_one_request(config):
    service, create, _ = provider(config, {'summary': [], 'insights': []})
    first, second = await asyncio.gather(service.initialize(session(), []), service.initialize(session(), []))
    assert first == second and first is not second
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_token_pacing_waits_for_deficit_not_full_reset(config):
    service, create, _ = provider(config, {'insights': []})
    create.return_value.headers.update({'x-ratelimit-remaining-tokens': '1700',
                                        'x-ratelimit-reset-tokens': '47s'})
    await service.analyze(session(), [])
    # 1500 used + 300 headroom - 1700 remaining = 100 tokens, 0.75 sec at 8k TPM.
    delay = service.next_requests[config.groq_model] - time.monotonic()
    assert 0 < delay <= 0.75


@pytest.mark.asyncio
async def test_429_retries_once_respecting_cooldown_and_does_not_leak_error(config, monkeypatch):
    service, create, _ = provider(config, {'insights': []})
    class QuotaError(Exception):
        status_code = 429
        response = SimpleNamespace(headers={'retry-after': '10'})
    create.side_effect = QuotaError('private provider details')
    waits = []
    async def sleep(delay): waits.append(delay)
    monkeypatch.setattr('app.services.groq_analysis.asyncio.sleep', sleep)
    _, name, notice = await service.analyze(session(), [])
    assert name == 'unavailable' and 'HTTP 429' in notice
    assert 'private' not in notice and create.await_count == 2
    assert len(waits) == 1 and 9 <= waits[0] <= 10


@pytest.mark.asyncio
async def test_live_member_updates_do_not_cancel_inflight_request_and_are_reviewed_next(config):
    from app.schemas.contracts import CreateSession, InitialAnalysis, TranscriptInput
    from app.services.member_data import MemberData
    from app.services.sessions import SessionService
    config.groq_debounce_seconds = 0
    service = SessionService(config, MemberData(config.data_dir))
    s = service.create(CreateSession(member_id='M1002', consent=True, mode='live'))
    s.initial_analysis = InitialAnalysis(provider='groq', notice='mocked', event_count=37)
    entered, release = asyncio.Event(), asyncio.Event()
    snapshots = []
    async def analyze(snapshot, events):
        snapshots.append(snapshot)
        if len(snapshots) == 1:
            entered.set()
            await release.wait()
        return AnalysisResult(), 'groq', 'reviewed'
    service.analysis.analyze = analyze
    await service.transcript(s.session_id, TranscriptInput(segment_id='s1', sequence=0, speaker='member', text='An investment with guaranteed returns.'))
    original = service.tasks[s.session_id]
    await asyncio.wait_for(entered.wait(), 2)
    for sequence in (1, 2):
        await service.transcript(s.session_id, TranscriptInput(segment_id=f's{sequence+1}', sequence=sequence, speaker='member', text='I have not checked it independently.'))
    assert service.tasks[s.session_id] is original and not original.cancelled()
    release.set()
    await asyncio.wait_for(original, 2)
    assert len(snapshots) == 2 and len(snapshots[-1].segments) == 3
    assert len(s.analyzed_sentences) == 3 and not s.analysis_pending
    await service.shutdown()


@pytest.mark.asyncio
async def test_m1057_verified_answer_withdraws_gap_and_lowers_attention(config):
    from app.schemas.contracts import CreateSession, InitialAnalysis, TranscriptInput
    from app.services.member_data import MemberData
    from app.services.sessions import SessionService
    config.groq_debounce_seconds = 0
    service = SessionService(config, MemberData(config.data_dir))
    s = service.create(CreateSession(member_id='M1057', consent=True, mode='live'))
    s.initial_analysis = InitialAnalysis(provider='groq', notice='mocked', event_count=0)
    gap = 'This is my first payment to the closing company; I have not checked who owns the account.'
    answer = 'I called my established closing attorney using the number from our original paperwork. They confirmed the account belongs to the closing company.'
    mocked, create, _ = provider(config, {'insights': [insight('IDENTITY_VERIFICATION_NEEDED', gap)]})
    service.analysis = mocked
    baseline = s.attention.score
    await service.transcript(s.session_id, TranscriptInput(segment_id='s1', sequence=0, speaker='member', text=gap))
    await service.tasks[s.session_id]
    assert s.attention.score == baseline + 5
    resolved = insight('IDENTITY_VERIFICATION_NEEDED', answer, source='s2', status='NOT_ESTABLISHED')
    resolved.update(title='Verification explained', observation='The member described an independent callback.',
                    recommended_action='CONTINUE_REVIEW', priority='low', suggested_question='Is there anything else you would like to clarify?')
    create.return_value.parse.return_value.choices[0].message.content = json.dumps({'insights': [resolved]})
    await service.transcript(s.session_id, TranscriptInput(segment_id='s2', sequence=1, speaker='member', text=answer))
    await service.tasks[s.session_id]
    assert s.attention.score == baseline
    assert not any(i.status == 'active' and 'IDENTITY_VERIFICATION_NEEDED' in i.signal_codes for i in s.insights)
    assert s.insights[-1].recommended_action == 'CONTINUE_REVIEW'
    assert s.review_outcome == 'in_review'  # Only a teller can approve the payment.
    await service.shutdown()
