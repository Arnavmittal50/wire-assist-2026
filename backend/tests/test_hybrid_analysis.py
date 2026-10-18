import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.schemas.contracts import InitialAnalysis
from app.services.hybrid_analysis import HybridAnalysisService
from test_analysis import session
from test_groq_analysis import insight


def hybrid(config, payload):
    config.gemini_api_key = 'fake-test-key'
    generate = AsyncMock(return_value=SimpleNamespace(text=json.dumps(payload)))
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate), aclose=AsyncMock()), close=Mock())
    service = HybridAnalysisService(config, gemini_client_factory=lambda **kwargs: client)
    service.history.analyze = AsyncMock(side_effect=AssertionError('Live must not use Groq'))
    return service, generate


@pytest.mark.asyncio
async def test_live_uses_flash_lite_shared_prompts_and_evidence_validation(config):
    text = 'The investment promises guaranteed returns.'
    service, generate = hybrid(config, {'insights': [insight('INVESTMENT_RISK', text, source='s0')]})
    result, provider, notice = await service.analyze(session(text), [])
    assert provider == 'gemini' and result.insights[0].provider == 'gemini'
    request = generate.call_args.kwargs
    assert request['model'] == 'gemini-3.5-flash-lite'
    assert 'recipient and the investment' in request['config'].system_instruction
    assert 'closing company' in request['config'].system_instruction
    assert result.insights[0].signal_codes == ['INVESTMENT_RISK']
    service.history.analyze.assert_not_called()
    assert 'gemini-3.5-flash-lite' in notice


@pytest.mark.asyncio
async def test_gemini_failure_never_falls_back_to_groq(config):
    service, generate = hybrid(config, {'insights': []})
    class QuotaError(Exception):
        code = 429
    generate.side_effect = QuotaError('private details')
    result, provider, notice = await service.analyze(session('Please verify this investment.'), [])
    assert provider == 'unavailable' and '429' in notice and 'private' not in notice
    assert not result.insights
    service.history.analyze.assert_not_called()
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_pre_call_stays_on_groq(config):
    service, generate = hybrid(config, {'insights': []})
    service.history.initialize = AsyncMock(return_value=InitialAnalysis(provider='groq', model=config.groq_precall_model,
                                                                       notice='history', event_count=37))
    initial = await service.initialize(session(), [])
    assert initial.provider == 'groq'
    generate.assert_not_called()


def test_health_exposes_the_live_model_and_separate_pre_call_provider(client):
    health = client.get('/health').json()
    assert health['update_model'] == 'gemini-3.5-flash-lite'
    assert health['analysis_model'] == 'openai/gpt-oss-120b'
    assert health['precall_provider'] == 'unavailable'


def completed_payload(identity_quote, routing_quote=None, routing_source='s0', routing_source_type='segment'):
    return {'insights': [{
        'title': 'Continue review', 'observation': 'The recipient is confirmed.',
        'evidence': [{'source_type': 'segment', 'source_id': 's0', 'quote': identity_quote}],
        'suggested_question': '', 'recommended_action': 'CONTINUE_REVIEW', 'priority': 'low',
        'confidence_in_observation': 0.9, 'signals': [],
        'verification_checks': [
            {'check_type': 'RECIPIENT_IDENTITY', 'status': 'CONFIRMED', 'evidence': [
                {'source_type': 'segment', 'source_id': 's0', 'quote': identity_quote}]},
            {'check_type': 'PAYMENT_INSTRUCTIONS', 'status': 'CONFIRMED' if routing_quote else 'NOT_ESTABLISHED',
             'evidence': [{'source_type': routing_source_type, 'source_id': routing_source, 'quote': routing_quote}] if routing_quote else []}]
    }]}


@pytest.mark.asyncio
async def test_agent_confirmed_in_person_still_requires_routing_number_followup(config):
    quote = "Yes, I'm sending it to the closing attorney. We confirmed this in person."
    service, _ = hybrid(config, completed_payload(quote))
    result, provider, _ = await service.analyze(session(quote), [])
    assert provider == 'gemini'
    current = result.insights[0]
    assert current.recommended_action == 'ASK_FOLLOW_UP'
    assert current.suggested_question == 'How did you confirm the account and routing numbers?'
    assert not current.signal_codes  # Routine verification does not invent risk points.


@pytest.mark.asyncio
async def test_newly_confirmed_agent_moves_to_how_the_numbers_were_checked(config):
    quote = 'I met my closing attorney in person.'
    payload = completed_payload(quote)
    payload['insights'][0].update(recommended_action='ASK_FOLLOW_UP', suggested_question='Who gave you the account and routing numbers?')
    service, generate = hybrid(config, payload)
    current = session(quote)
    result, _, _ = await service.analyze(current, [])
    assert result.insights[0].suggested_question == 'How did you confirm the account and routing numbers?'
    # Once this transition was made, keep later contextual steps instead of repeating it.
    current.insights = result.insights
    payload['insights'][0]['suggested_question'] = 'Did you call using a number you already had?'
    generate.return_value.text = json.dumps(payload)
    later, _, _ = await service.analyze(current, [])
    assert later.insights[0].suggested_question == 'Did you call using a number you already had?'


@pytest.mark.asyncio
@pytest.mark.parametrize('source_type,source,quote', [('segment', 'teller', 'Did you check both numbers?'),
                                                    ('segment', 's0', 'An invented callback')])
async def test_teller_or_invented_instruction_proof_cannot_clear_verification(config, source_type, source, quote):
    from app.schemas.contracts import Segment
    identity = 'I met my closing attorney in person.'
    service, _ = hybrid(config, completed_payload(identity, quote, source, source_type))
    current = session(identity)
    current.segments.append(Segment(segment_id='teller', sequence=1, speaker='teller', text='Did you check both numbers?'))
    result, provider, _ = await service.analyze(current, [])
    assert provider == 'gemini' and result.insights[0].recommended_action == 'ASK_FOLLOW_UP'
    check = next(c for c in result.insights[0].verification_checks if c.check_type == 'PAYMENT_INSTRUCTIONS')
    assert check.status == 'NOT_ESTABLISHED' and not check.evidence


@pytest.mark.asyncio
async def test_confirmed_agent_and_independently_checked_numbers_can_finish(config):
    from app.schemas.contracts import Segment
    identity = 'I met my closing attorney in person.'
    routing = 'I called the closing office on the number from our original paperwork and read back both the account and routing numbers.'
    service, _ = hybrid(config, completed_payload(identity, routing, 's1'))
    current = session(identity)
    current.segments.append(Segment(segment_id='s1', sequence=1, speaker='member', text=routing))
    result, provider, _ = await service.analyze(current, [])
    assert provider == 'gemini' and result.insights[0].recommended_action == 'CONTINUE_REVIEW'
    assert result.insights[0].suggested_question == ''
    assert {c.check_type for c in result.insights[0].verification_checks if c.status == 'CONFIRMED'} == {'RECIPIENT_IDENTITY', 'PAYMENT_INSTRUCTIONS'}
