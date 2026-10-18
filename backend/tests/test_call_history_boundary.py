import json

import pytest

from app.schemas.contracts import CreateSession, Segment
from app.services.analysis import _context
from app.services.groq_analysis import compact_context
from app.services.member_data import MemberData, history_before_wire_request
from app.services.risk_engine import calculate_attention
from test_groq_analysis import provider, session


def test_before_call_omits_proposal_and_unfinished_wire_but_preserves_past_payments(config):
    s = session()
    s.payment.amount = 912345
    s.payment.recipient_id = 'UNSPOKEN-RECIPIENT'
    s.payment.purpose = 'UNSPOKEN-PURPOSE'
    events = MemberData(config.data_dir).events(s.member_id, s.as_of)
    context, segments, records = compact_context(s, events, True)
    assert 'proposed_payment' not in context and 'proposed_payment' not in records
    assert 'E00103' not in records and 'E00104' not in records
    assert 'E00098' in records  # Past completed payment remains available.
    assert not segments
    assert '912345' not in json.dumps(context) and 'UNSPOKEN' not in json.dumps(context)
    assert 'R1002-09' not in json.dumps(context)


def test_unconfirmed_live_request_learns_only_from_member_statements(config):
    s = session()
    s.payment_known = False
    s.payment.recipient_id = 'PLACEHOLDER-NEVER-SAID'
    events = MemberData(config.data_dir).events(s.member_id, s.as_of)
    context, _, records = compact_context(s, events, False)
    assert 'proposed_payment' not in context and 'proposed_payment' not in records
    assert 'PLACEHOLDER' not in json.dumps(context)
    assert 'R1002-09' not in json.dumps(context)
    quote = 'I want to send $42,000 to the closing company for my house.'
    s.segments = [Segment(segment_id='m1', sequence=0, speaker='member', text=quote)]
    context, segments, records = compact_context(s, events, False)
    assert context['latest_member_statement']['text'] == quote
    assert segments == {'m1': quote}
    assert 'proposed_payment' not in records


@pytest.mark.asyncio
async def test_initial_review_cache_does_not_depend_on_unspoken_proposal(config):
    service, create, _ = provider(config, {'summary': [], 'insights': []})
    s = session()
    first = await service.initialize(s, [])
    s.payment.amount = 912345
    s.payment.recipient_id = 'OTHER-UNSPOKEN-RECIPIENT'
    second = await service.initialize(s, [])
    assert first == second and create.await_count == 1
    body = create.call_args.kwargs['messages'][1]['content']
    assert 'proposed_payment' not in body and '912345' not in body


def test_history_filter_keeps_completed_wire_lifecycle_and_established_recipient():
    def event(key, kind, **fields):
        return {'event_id': key, 'timestamp': f'2026-09-0{key}T00:00:00Z', 'event_type': kind,
                'recipient_id': 'known', 'payment_rail': 'Wire', 'transaction_direction': 'Outgoing', **fields}
    rows = [event('1', 'Recipient Added'),
            event('2', 'Payment Initiated', payment_id='old', status='Pending'),
            event('3', 'Payment Completed', payment_id='old', status='Completed'),
            event('4', 'Payment Initiated', payment_id='new', status='Pending')]
    assert [e['event_id'] for e in history_before_wire_request(rows)] == ['1', '2', '3']


def test_unconfirmed_request_cannot_get_attention_points_from_a_demo_proposal(config):
    s = session()
    events = MemberData(config.data_dir).events(s.member_id, s.as_of)
    first = calculate_attention(events, s.payment, [], as_of=s.as_of, payment_known=False)
    s.payment.amount = 999999
    s.payment.recipient_id = 'different'
    second = calculate_attention(events, s.payment, [], as_of=s.as_of, payment_known=False)
    assert first == second
    assert not {'FIRST_WIRE', 'UNUSUAL_AMOUNT', 'NEW_RECIPIENT'} & {signal.code for signal in first.signals}


def test_browser_history_endpoint_and_new_call_do_not_treat_the_request_as_known(client):
    rows = client.get('/members/M1002/events?history_only=true').json()
    assert not {'E00103', 'E00104'} & {row['event_id'] for row in rows}
    created = client.post('/sessions', json={'member_id': 'M1002', 'consent': True, 'mode': 'live',
                          'payment': {'amount': 912345, 'recipient_id': 'NOT-SAID'}, 'payment_known': False}).json()
    assert created['payment_known'] is False
    summary = client.get(f"/sessions/{created['session_id']}/summary").json()
    assert summary['payment_known'] is False
