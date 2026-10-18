"""Offline Phase 1 checks. Signed requests use fake keys; no phone is called."""
import xml.etree.ElementTree as ET

import jwt
import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import create_app
from app.services.twilio_calling import IDENTITY

PARENT = 'CA' + 'a' * 32
CHILD = 'CA' + 'b' * 32


@pytest.fixture
def twilio_client(config):
    config.twilio_account_sid = 'AC' + '1' * 32
    config.twilio_api_key_sid = 'SK' + '2' * 32
    config.twilio_api_key_secret = 'synthetic-secret-for-offline-tests-only'
    config.twilio_auth_token = 'synthetic-auth-token-for-offline-tests'
    config.twilio_twiml_app_sid = 'AP' + '3' * 32
    config.twilio_phone_number = '+12065550123'
    config.test_member_phone_number = '+12065550124'
    config.public_backend_url = 'https://wire-assist.example.test'
    with TestClient(create_app(config)) as client:
        client.headers['Authorization'] = 'Bearer ' + config.dev_auth_token
        yield client, config


def signed(client, config, path, fields, *, signing_url=None, headers=None):
    data = {'AccountSid': config.twilio_account_sid, **fields}
    signature = RequestValidator(config.twilio_auth_token).compute_signature(
        signing_url or config.public_backend_url + path, data)
    return client.post(path, data=data, headers={'X-Twilio-Signature': signature, **(headers or {})})


def start(client, config, **extra):
    return signed(client, config, '/twilio/voice', {'CallSid': PARENT, 'From': 'client:' + IDENTITY, **extra})


def test_token_is_authenticated_short_lived_and_outgoing_only(twilio_client):
    client, config = twilio_client
    assert client.get('/api/twilio/token', headers={'Authorization': ''}).status_code == 401
    response = client.get('/api/twilio/token')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    value = response.json()
    claims = jwt.decode(value['token'], config.twilio_api_key_secret, algorithms=['HS256'],
                        issuer=config.twilio_api_key_sid, options={'verify_aud': False})
    assert claims['sub'] == config.twilio_account_sid
    assert claims['exp'] - claims['nbf'] == 600
    assert claims['grants']['identity'] == value['identity'] == IDENTITY
    assert claims['grants']['voice']['outgoing']['application_sid'] == config.twilio_twiml_app_sid
    assert not claims['grants']['voice'].get('incoming', {}).get('allow', False)
    assert config.twilio_api_key_secret not in response.text and config.twilio_auth_token not in response.text


def test_unconfigured_calling_does_not_break_existing_app(client):
    assert client.get('/health').status_code == 200
    response = client.get('/api/twilio/token')
    assert response.status_code == 503 and 'Twilio setup incomplete' in response.json()['detail']


def test_twiml_dials_only_configured_number_and_does_not_capture_audio(twilio_client):
    client, config = twilio_client
    # The voice endpoint can be installed before the TwiML App is created.
    config.twilio_twiml_app_sid = ''
    response = start(client, config, To='+12065550999', phone_number='+12065550998')
    assert response.status_code == 200 and 'application/xml' in response.headers['content-type']
    root = ET.fromstring(response.text)
    assert [child.tag for child in root] == ['Dial']
    dial = root.find('Dial')
    assert dial.attrib['callerId'] == config.twilio_phone_number
    assert dial.attrib['answerOnBridge'] == 'true'
    assert dial.attrib['action'] == config.public_backend_url + '/twilio/dial-complete'
    number = dial.find('Number')
    assert number.text == config.test_member_phone_number
    assert number.attrib['statusCallbackEvent'] == 'initiated ringing answered completed'
    assert number.attrib['statusCallback'] == config.public_backend_url + '/twilio/status'
    assert root.find('.//Stream') is None and root.find('.//Record') is None


@pytest.mark.parametrize('path', ['/twilio/voice', '/twilio/status', '/twilio/dial-complete'])
def test_all_webhooks_require_signature_even_with_browser_auth(twilio_client, path):
    client, config = twilio_client
    assert client.post(path, data={'AccountSid': config.twilio_account_sid}).status_code == 403
    assert signed(client, config, path, {}, signing_url='http://testserver' + path).status_code == 403


def test_external_signature_ignores_untrusted_proxy_headers_and_preserves_query(twilio_client):
    client, config = twilio_client
    path = '/twilio/voice?test=a%2Bb&value=1'
    fields = {'CallSid': PARENT, 'From': 'client:' + IDENTITY}
    assert signed(client, config, path, fields, headers={'X-Forwarded-Host': 'attacker.invalid', 'X-Forwarded-Proto': 'http'}).status_code == 200
    assert signed(client, config, path, fields, signing_url=config.public_backend_url + '/twilio/voice').status_code == 403
    data = {'AccountSid': config.twilio_account_sid, **fields, 'To': '+12065550999'}
    signature = RequestValidator(config.twilio_auth_token).compute_signature(config.public_backend_url + '/twilio/voice', data)
    data['To'] = '+12065550888'
    assert client.post('/twilio/voice', data=data, headers={'X-Twilio-Signature': signature}).status_code == 403


def test_wrong_account_and_non_browser_calls_rejected(twilio_client):
    client, config = twilio_client
    assert start(client, config, From='+12065550100').status_code == 403
    assert start(client, config, AccountSid='AC' + '9' * 32).status_code == 403


@pytest.mark.parametrize('field,value', [('test_member_phone_number', '2065550124'), ('twilio_phone_number', '+1bad'),
                                       ('public_backend_url', 'http://localhost:8000'), ('public_backend_url', 'https://host.test/path'),
                                       ('public_backend_url', 'https://user:secret@host.test')])
def test_bad_configuration_fails_closed(twilio_client, field, value):
    client, config = twilio_client
    setattr(config, field, value)
    assert client.get('/api/twilio/token').status_code == 503


@pytest.mark.parametrize('outcome', ['completed', 'busy', 'no-answer', 'failed', 'canceled'])
def test_verified_status_lifecycle_rejects_stale_callbacks_and_hangs_up(twilio_client, outcome):
    client, config = twilio_client
    assert start(client, config).status_code == 200
    path = f'/api/twilio/calls/{PARENT}'
    assert client.get(path, headers={'Authorization': ''}).status_code == 401
    assert client.get(path).json()['status'] == 'initiated'
    def callback(status, sequence):
        return signed(client, config, '/twilio/status', {'CallSid': CHILD, 'ParentCallSid': PARENT,
            'CallStatus': status, 'SequenceNumber': str(sequence)})
    callback('ringing', 1)
    assert client.get(path).json()['connected_at'] is None
    callback('in-progress', 2)
    connected = client.get(path).json()
    assert connected['status'] == 'in-progress' and connected['connected_at']
    callback('ringing', 1)
    assert client.get(path).json() == connected
    response = signed(client, config, '/twilio/dial-complete', {'CallSid': PARENT, 'DialCallStatus': outcome})
    assert ET.fromstring(response.text).find('Hangup') is not None
    callback('ringing', 5)
    result = client.get(path).json()
    assert result['status'] == outcome and result['ended_at']
