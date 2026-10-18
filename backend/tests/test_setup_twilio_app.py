"""Setup API contract tests with synthetic credentials; no Twilio requests."""
from urllib.parse import parse_qs

import httpx
import pytest

from app.core.config import Settings
from scripts.setup_twilio_app import SetupError, setup


@pytest.fixture
def inputs(tmp_path):
    settings = Settings(_env_file=None, twilio_account_sid='AC' + '1' * 32,
                        twilio_auth_token='synthetic-token',
                        public_backend_url='https://example.test/')
    env = tmp_path / '.env'
    env.write_text('# Keep this comment\nGEMINI_API_KEY=synthetic-value\nTWILIO_TWIML_APP_SID=\n')
    return settings, env


def application(settings):
    return {'sid': 'AP' + '2' * 32, 'account_sid': settings.twilio_account_sid,
            'friendly_name': 'Wire Assist', 'voice_url': 'https://example.test/twilio/voice',
            'voice_method': 'POST'}


def test_create_and_save_sid(inputs):
    settings, env = inputs
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == 'api.twilio.com'
        assert request.url.path == f'/2010-04-01/Accounts/{settings.twilio_account_sid}/Applications.json'
        if request.method == 'GET':
            return httpx.Response(200, json={'applications': [], 'next_page_uri': None})
        assert parse_qs(request.content.decode()) == {
            'FriendlyName': ['Wire Assist'], 'VoiceUrl': ['https://example.test/twilio/voice'],
            'VoiceMethod': ['POST']}
        return httpx.Response(201, json=application(settings))

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        assert setup(settings, env, client) == 'AP' + '2' * 32
    assert [r.method for r in requests] == ['GET', 'POST']
    assert env.read_text() == ('# Keep this comment\nGEMINI_API_KEY=synthetic-value\n'
                               'TWILIO_TWIML_APP_SID=AP' + '2' * 32 + '\n')


@pytest.mark.parametrize('stage,status,code,message', [
    ('GET', 401, 20003, 'Authenticate'),
    ('POST', 403, 20005, 'Synthetic trial restriction: upgrade required'),
])
def test_denial_stops_and_preserves_env(inputs, stage, status, code, message):
    settings, env = inputs
    original = env.read_bytes()
    methods = []

    def respond(request):
        methods.append(request.method)
        if request.method == stage:
            return httpx.Response(status, json={'code': code, 'message': message})
        return httpx.Response(200, json={'applications': []})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SetupError) as error:
            setup(settings, env, client)
    assert str(error.value) == f'Twilio HTTP {status}; code {code}: {message}'
    assert methods == (['GET'] if stage == 'GET' else ['GET', 'POST'])
    assert env.read_bytes() == original


def test_rerun_reuses_matching_app(inputs):
    settings, env = inputs

    def respond(request):
        assert request.method == 'GET'
        return httpx.Response(200, json={'applications': [application(settings)]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        setup(settings, env, client)
    assert 'TWILIO_TWIML_APP_SID=AP' + '2' * 32 in env.read_text()


def test_missing_settings_do_not_send_request(inputs):
    settings, env = inputs
    settings.twilio_auth_token = ''

    def respond(request):
        pytest.fail('Must not send a request without credentials')

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(SetupError, match='TWILIO_AUTH_TOKEN'):
            setup(settings, env, client)
