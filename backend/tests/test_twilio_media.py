import asyncio
import base64
import json
import struct
import xml.etree.ElementTree as ET
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from app.main import create_app
from app.schemas.contracts import CreateSession
from app.services.twilio_media import AudioTrack, MULAW, ROLES, valid_media_signature
from test_twilio_calling import twilio_client, signed, PARENT, CHILD

STREAM = 'MZ' + 'd' * 32


@pytest.mark.parametrize('slash', ['', '/'])
def test_wss_signature_uses_exact_public_scheme_path_and_query(config, slash):
    config.twilio_auth_token = 'synthetic-auth'
    origin = 'https://example.test'
    path = '/twilio/media/synthetic-ticket'
    validator = RequestValidator(config.twilio_auth_token)
    signature = validator.compute_signature('wss://example.test' + path + slash + '?a=1', {})
    assert valid_media_signature(config, origin, path, 'a=1', signature)
    assert not valid_media_signature(config, origin, path, 'a=2', signature)
    assert not valid_media_signature(config, 'https://other.test', path, 'a=1', signature)
    assert not valid_media_signature(config, origin, path + 'wrong', 'a=1', signature)
    https_signature = validator.compute_signature(origin + path + '?a=1', {})
    assert not valid_media_signature(config, origin, path, 'a=1', https_signature)


def test_mulaw_vectors_and_stateful_resampling():
    assert (MULAW[255], MULAW[127], MULAW[0], MULAW[128]) == (0, 0, -32124, 32124)
    track = AudioTrack()
    track.push(1, 0, bytes([128, 128]))
    frames = track.flush()
    assert struct.unpack('<hhhh', frames[0][1]) == (16062, 32124, 32124, 32124)
    track.push(2, 0.25, bytes([128]))
    assert struct.unpack('<hh', track.flush()[0][1]) == (32124, 32124)


def test_reorder_duplicate_and_gap_preserve_timestamps():
    track = AudioTrack()
    track.push(2, 20, b'\xff' * 160)
    track.push(1, 0, b'\xff' * 160)
    track.push(1, 0, b'\xff' * 160)
    frames = track.flush()
    assert [x[0] for x in frames] == [0, 20]
    assert all(len(pcm) == 640 and not speech for _, pcm, speech in frames)
    track.push(4, 60, b'\xff' * 160)
    frames = track.flush()
    assert [x[0] for x in frames] == [40, 60]
    assert frames[0][1] == bytes(640)
    assert track.dropped == 1
    assert track.push(2, 20, b'\xff' * 160) == []


def test_dashboard_consent_starts_stream_on_answer_without_phone_prompts(twilio_client):
    from unittest.mock import AsyncMock
    client, config = twilio_client
    assert client.post('/api/twilio/prepare', json={'session_id': 'x', 'participants_consent': True}, headers={'Authorization': ''}).status_code == 401
    session = client.post('/sessions', json={'member_id': 'M1002', 'consent': True, 'mode': 'live'}).json()
    sid = session['session_id']
    assert client.post('/api/twilio/prepare', json={'session_id': sid, 'participants_consent': False}).status_code == 422
    media = client.app.state.twilio_media
    media.start_stream = AsyncMock()
    ticket = client.post('/api/twilio/prepare', json={'session_id': sid, 'participants_consent': True}).json()['ticket']
    response = signed(client, config, '/twilio/voice', {'CallSid': PARENT, 'From': 'client:wire_assist_teller', 'ProcessingTicket': ticket})
    root = ET.fromstring(response.text)
    assert root.find('.//Stream') is None
    assert root.find('.//Gather') is None and root.find('.//Say') is None
    assert 'url' not in root.find('./Dial/Number').attrib
    assert client.post('/twilio/ai-notice').status_code == 404
    assert media.bindings[ticket].consented
    media.start_stream.assert_not_called()
    signed(client, config, '/twilio/status', {'CallSid': CHILD, 'ParentCallSid': PARENT, 'CallStatus': 'ringing', 'SequenceNumber': '1'})
    media.start_stream.assert_not_called()
    signed(client, config, '/twilio/status', {'CallSid': CHILD, 'ParentCallSid': PARENT, 'CallStatus': 'in-progress', 'SequenceNumber': '2'})
    assert media.bindings[ticket].connected and media.bindings[ticket].task is not None
    assert client.post('/twilio/notice/' + ticket).status_code == 404
    assert client.post('/twilio/consent/' + ticket).status_code == 404


class FakeSocket:
    def __init__(self, config, ticket, messages, valid=True):
        path = '/twilio/media/' + ticket
        self.url = SimpleNamespace(path=path)
        self.scope = {'query_string': b''}
        self.headers = {'x-twilio-signature': RequestValidator(config.twilio_auth_token).compute_signature(config.public_backend_url.replace('https://', 'wss://') + path, {}) if valid else 'invalid'}
        self.messages = iter(messages)
        self.accepted = False
        self.closed = None

    async def accept(self): self.accepted = True
    async def close(self, code=1000): self.closed = code
    async def receive_text(self):
        await asyncio.sleep(0.005)
        return json.dumps(next(self.messages))


def start_message(call=PARENT):
    return {'event': 'start', 'start': {'accountSid': 'AC' + '1' * 32, 'callSid': call,
            'streamSid': STREAM, 'tracks': ['inbound', 'outbound'],
            'mediaFormat': {'encoding': 'audio/x-mulaw', 'sampleRate': 8000, 'channels': 1}}}


@pytest.fixture
def media_setup(config):
    config.twilio_auth_token = 'synthetic-auth'
    config.twilio_account_sid = 'AC' + '1' * 32
    config.public_backend_url = 'https://example.test'
    app = create_app(config)
    service = app.state.service
    session = service.create(CreateSession(member_id='M1002', consent=True, mode='live', retain_transcript=True))
    media = app.state.twilio_media
    ticket = media.prepare(session.session_id)
    b = media.bind(ticket, PARENT)
    b.child, b.consented, b.connected = CHILD, True, True
    return media, service, session, b, config


@pytest.mark.asyncio
async def test_two_tracks_independent_transcripts_cleanup(media_setup):
    media, service, session, b, config = media_setup
    adapters = []

    class FakeAdapter:
        def __init__(self, settings, callback, on_error):
            self.callback, self.closed, self.sent = callback, False, []
            self.index = len(adapters)
            adapters.append(self)
        async def open(self): pass
        async def send(self, pcm):
            self.sent.append(pcm)
            if len(self.sent) == 1:
                await self.callback('Teller question.' if self.index == 0 else 'Member answer.', True,
                                    start_offset=0, end_offset=0.02)
        async def finish(self): pass
        async def close(self): self.closed = True

    media.adapter_factory = FakeAdapter
    messages = [start_message()]
    for i in range(8):
        for index, track in enumerate(ROLES):
            messages.append({'event': 'media', 'streamSid': STREAM, 'sequenceNumber': str(i * 2 + index + 2),
                             'media': {'track': track, 'chunk': str(i + 1), 'timestamp': str(i * 20),
                                       'payload': base64.b64encode(bytes([128 if track == 'inbound' else 0]) * 160).decode()}})
    messages.append({'event': 'stop', 'streamSid': STREAM})
    socket = FakeSocket(config, b.ticket, messages)
    await media.websocket(socket, b.ticket)
    assert socket.accepted and len(adapters) == 2 and all(a.closed for a in adapters)
    assert adapters[0].sent[0] != adapters[1].sent[0]
    assert [(s.speaker, s.text) for s in session.segments] == [('teller', 'Teller question.'), ('member', 'Member answer.')]
    assert all(s.call_sid == PARENT and s.stream_sid == STREAM and s.speaker_source == 'twilio_track'
               and s.diarization_status == 'CONFIRMED' and s.is_final for s in session.segments)
    assert all(t.done() for t in b.workers)
    await media.end(b)
    summary = service.summary(session.session_id)
    assert summary.status == 'ended' and summary.transcript_retained
    assert [s.text for s in summary.transcript] == ['Teller question.', 'Member answer.']
    await service.shutdown()


@pytest.mark.asyncio
async def test_preparing_an_older_call_review_enables_durable_retention(config):
    app = create_app(config)
    service = app.state.service
    session = service.create(CreateSession(member_id='M1002', consent=True, mode='live', retain_transcript=False))
    app.state.twilio_media.prepare(session.session_id)
    assert session.retain_transcript
    assert service.repository.load_all()[0].retain_transcript
    await app.state.twilio_media.shutdown()
    await service.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_signature,no_consent,wrong_leg', [(True, False, False), (False, True, False), (False, False, True)])
async def test_unverified_audio_never_reaches_provider(media_setup, bad_signature, no_consent, wrong_leg):
    media, service, session, b, config = media_setup
    def forbidden(*args): pytest.fail('Unverified/pre-consent audio must not open Gemini')
    media.adapter_factory = forbidden
    b.consented = not no_consent
    socket = FakeSocket(config, b.ticket, [start_message(CHILD if wrong_leg else PARENT)], valid=not bad_signature)
    await media.websocket(socket, b.ticket)
    assert not session.segments
    assert socket.closed is not None
    await service.shutdown()


@pytest.mark.asyncio
async def test_provider_failure_does_not_end_phone_call(media_setup):
    media, service, session, b, config = media_setup
    class Failed:
        async def open(self): raise RuntimeError('synthetic failure')
        async def close(self): pass
    media.adapter_factory = lambda *args: Failed()
    queue = asyncio.Queue()
    queue.put_nowait((0, bytes(640), True))
    await media.worker(b, 'inbound', queue)
    assert not b.ended and session.status == 'active' and not session.segments
    assert 'unavailable' in session.transcription_notice
    await service.shutdown()


@pytest.mark.asyncio
async def test_answer_callback_requests_parent_both_tracks_once(media_setup, monkeypatch):
    from unittest.mock import AsyncMock
    media, service, session, b, config = media_setup
    media.start_stream = AsyncMock()
    media.status(PARENT, CHILD, 'in-progress')
    media.status(PARENT, CHILD, 'in-progress')
    await b.task
    media.start_stream.assert_awaited_once_with(b)
    await media.stop_session(session.session_id)
    assert session.status == 'ended' and b.ticket not in media.bindings
    assert b.draining is False and not b.workers
    await service.shutdown()


@pytest.mark.asyncio
async def test_actual_stream_api_arguments_are_unidirectional_parent_leg(media_setup, monkeypatch):
    from unittest.mock import AsyncMock, Mock
    media, service, session, b, config = media_setup
    create = AsyncMock(return_value=SimpleNamespace(sid=STREAM))
    calls = Mock(return_value=SimpleNamespace(streams=SimpleNamespace(create_async=create)))
    monkeypatch.setattr('twilio.rest.Client', Mock(return_value=SimpleNamespace(calls=calls)))
    await media.start_stream(b)
    calls.assert_called_once_with(PARENT)
    assert create.call_args.kwargs['track'] == 'both_tracks'
    assert create.call_args.kwargs['url'] == 'wss://example.test/twilio/media/' + b.ticket
    assert b.stream == STREAM
    await service.shutdown()
