import time

from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.contracts import Insight


def settled(client, route):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        state = client.get(route).json()
        if not state['analysis_pending']:
            return state
        time.sleep(0.01)
    raise AssertionError('Local analysis did not finish')


def test_summary_keeps_conversation_all_insight_versions_and_scam_match_after_restart(config):
    headers = {'Authorization': 'Bearer ' + config.dev_auth_token}
    texts = [
        'My roofing contractor emailed an amended invoice after the roof repair.',
        'The email replaced the original bank account with a new beneficiary for a wire.',
        'I have not called the contractor using the number in my signed contract.',
    ]
    with TestClient(create_app(config), headers=headers) as client:
        # The omitted setting must now default to retention.
        created = client.post('/sessions', json={'member_id': 'M1002', 'consent': True, 'mode': 'demo'}).json()
        assert created['retain_transcript'] is True
        route = '/sessions/' + created['session_id']
        settled(client, route)
        for index, text in enumerate(texts):
            response = client.post(route + '/transcript', json={'segment_id': f'invoice-{index}',
                'sequence': index, 'speaker': 'member', 'text': text})
            assert response.status_code == 200
            settled(client, route)
        state = client.app.state.service.get(created['session_id'])
        service = client.app.state.service
        first = Insight(title='Verify the invoice change', observation='The beneficiary changed.',
                        suggested_question='Did the contractor confirm the bank details?', signal_codes=['INVOICE_TEST'],
                        evidence=[{'source_type': 'segment', 'source_id': 'invoice-1', 'quote': texts[1]}],
                        recommended_action='OFFER_VERIFICATION', priority='medium', confidence_in_observation=0.9)
        service._merge_insights(state, [first])
        updated = first.model_copy(deep=True, update={'suggested_question': 'Can you call the number in your signed contract?'})
        service._merge_insights(state, [updated])
        assert any(i.suggested_question == first.suggested_question for i in state.insight_history)
        assert client.post(route + '/actions', json={'action_type': 'DISMISS', 'insight_id': updated.insight_id,
            'note': 'Synthetic test: reviewed the question with the teller.'}).status_code == 200
        assert client.post(route + '/end').status_code == 200
        summary = client.get(route + '/summary').json()
        assert [turn['text'] for turn in summary['transcript']] == texts
        assert summary['transcript_retained']
        assert any(i['suggested_question'] == first.suggested_question and i['status'] == 'superseded' for i in summary['insights'])
        assert any(i['suggested_question'] == updated.suggested_question and i['status'] == 'dismissed' for i in summary['insights'])
        assert summary['scam_matches'][0]['report_id'] == 'SYNTHETIC-INVOICE-001'
    with TestClient(create_app(config), headers=headers) as restarted:
        restored = restarted.get(route + '/summary').json()
        for field in ['transcript', 'insights', 'scam_matches', 'actions']:
            assert restored[field] == summary[field]


def test_archived_insights_follow_explicit_nonretention_setting(config):
    from app.models.repository import redact_session
    from app.schemas.contracts import CreateSession
    app = create_app(config)
    service = app.state.service
    state = service.create(CreateSession(member_id='M1002', consent=True, retain_transcript=False, mode='demo'))
    first = Insight(title='Sensitive note', observation='PRIVATE_REVIEW_TEXT', suggested_question='PRIVATE_QUESTION',
                    evidence=[{'source_type': 'segment', 'source_id': 'test', 'quote': 'PRIVATE_REVIEW_TEXT'}],
                    recommended_action='OFFER_VERIFICATION', priority='medium', confidence_in_observation=0.9)
    service._merge_insights(state, [first])
    service._merge_insights(state, [first.model_copy(update={'observation': 'Revised observation'})])
    assert state.insight_history
    assert 'PRIVATE_' not in redact_session(state).model_dump_json()
    service.repository.close()
