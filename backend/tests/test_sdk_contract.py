"""Installed SDK wire conversion, not cloud integration tests.

The real SDK uses an in-process HTTP mock or an in-memory WebSocket transport.
No DNS, external requests, real credentials, microphone, or model are involved.
This complements the simpler adapter doubles by exercising SDK serialization.
"""
import asyncio
import base64
import json
from datetime import datetime, timezone

import httpx
import pytest
from google import genai
from google.genai import live, types

from app.core.config import Settings
from app.schemas.contracts import Payment, Segment, Session
from app.services.analysis import AnalysisService
from app.services.transcription import MAX_LIVE_SECONDS, PCM_MIME_TYPE, PCM_SAMPLE_RATE


async def test_real_sdk_serializes_batch_diarization_request():
    from app.services.audio_buffer import AudioBatch
    from app.services.diarization import GeminiDiarizationService
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            'id': 'mock', 'status': 'completed', 'steps': [{'type': 'model_output', 'content': [
                {'type': 'text', 'text': 'Hello Hi', 'annotations': [
                    {'type': 'word_info', 'text': 'Hello', 'speaker': 'spk_1', 'start_offset': '0.1s', 'end_offset': '0.3s'},
                    {'type': 'word_info', 'text': 'Hi', 'speaker': 'spk_2', 'start_offset': '0.5s', 'end_offset': '0.7s'}]}]}]})
    transport = httpx.MockTransport(respond)
    def factory(key):
        return genai.Client(api_key=key, http_options=types.HttpOptions(
            base_url='https://offline.invalid', client_args={'transport': transport},
            async_client_args={'transport': transport}))
    adapter = GeminiDiarizationService(Settings(_env_file=None, gemini_api_key='fake-key'), factory)
    words = await adapter.transcribe(AudioBatch('r', 0, 0, 1, b'\x00\x00' * 16000))
    assert {word.speaker for word in words} == {'spk_1', 'spk_2'}
    payload = json.loads(requests[0].content)
    assert payload['model'] == 'gemini-3.5-transcribe'
    assert payload['store'] is False
    mode = payload['generation_config']['transcription_config']['mode']
    assert mode['diarization_mode'] == 'speaker' and mode['timestamp_granularities'] == ['word']
    assert base64.b64decode(payload['input'][0]['content'][0]['data']).startswith(b'RIFF')


@pytest.mark.parametrize("initial", [False, True])
async def test_real_sdk_serializes_application_pydantic_schema_before_mock_http(initial):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            "candidates": [{
                "content": {"role": "model", "parts": [{"text": '{"insights":[]}'}]},
                "finishReason": "STOP",
            }],
        })
    transport = httpx.MockTransport(respond)
    def factory(*, api_key):
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                base_url="https://offline.invalid",
                client_args={"transport": transport},
                async_client_args={"transport": transport},
            ),
        )
    analyzer = AnalysisService(Settings(_env_file=None, gemini_api_key="offline-fake-key"), factory)
    session = Session(
        member_id="M1002", as_of=datetime(2026, 9, 27, tzinfo=timezone.utc),
        consent=True, retain_transcript=False, mode="live", payment=Payment(), data_source="test",
        segments=[Segment(segment_id="s1", sequence=0, speaker="member", text="Please help me review this.")],
    )
    if initial:
        result = await analyzer.initialize(session, [])
        provider, notice = result.provider, result.notice
    else:
        result, provider, notice = await analyzer.analyze(session, [])
    assert len(requests) == 1, notice
    assert result.insights == [] and provider == "gemini"
    request = requests[0]
    assert request.url.host == "offline.invalid"
    model = "gemini-3.5-flash-lite"
    assert model + ":generateContent" in request.url.path
    payload = json.loads(request.content)
    generation = payload["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    schema = generation["responseSchema"]
    wire_schema = json.dumps(schema)
    for unsupported in ("additional_properties", "additionalProperties", "max_length", "maxLength", "max_items", "maxItems"):
        assert unsupported not in wire_schema
    assert schema and "insights" in schema["properties"]
    if initial:
        assert "summary" in schema["properties"]
    nested = schema["properties"]["insights"]["items"]
    assert "evidence" in nested["properties"]
    assert "ASK_FOLLOW_UP" in nested["properties"]["recommended_action"]["enum"]
    assert "untrusted DATA" in payload["systemInstruction"]["parts"][0]["text"]


class MemoryWebSocket:
    def __init__(self):
        self.sent = []
        self.responses = asyncio.Queue()

    async def send(self, message):
        self.sent.append(json.loads(message))

    async def recv(self, **kwargs):
        return json.dumps(await self.responses.get())


async def test_real_live_sdk_serializes_pcm_and_parses_interim_final_server_fields():
    # AsyncSession is instantiated directly only in this transport contract test.
    # The application uses the public client.aio.live.connect context manager.
    client = genai.Client(api_key="offline-fake-key")
    transport = MemoryWebSocket()
    provider_session = live.AsyncSession(client._api_client, transport)
    pcm = b"\x00\x00" * 1600  # 100 ms at 16 kHz, mono, 16-bit samples.
    try:
        await provider_session.send_realtime_input(audio=types.Blob(data=pcm, mime_type=PCM_MIME_TYPE))
        await provider_session.send_realtime_input(audio_stream_end=True)
        sent_audio = transport.sent[0]["realtime_input"]["audio"]
        # This SDK serializes Blob fields in protobuf's accepted snake_case form.
        assert sent_audio["mime_type"] == "audio/pcm;rate=16000"
        assert base64.b64decode(sent_audio["data"]) == pcm
        assert transport.sent[1]["realtime_input"]["audioStreamEnd"] is True
        await transport.responses.put({"serverContent": {"interimInputTranscription": {"text": "I need"}}})
        await transport.responses.put({"serverContent": {
            "inputTranscription": {"text": "I need help."}, "turnComplete": True,
        }})
        updates = [message async for message in provider_session.receive()]
        assert len(updates) == 2
        assert updates[0].server_content.interim_input_transcription.text == "I need"
        assert updates[0].server_content.input_transcription is None
        assert updates[1].server_content.input_transcription.text == "I need help."
        assert updates[1].server_content.turn_complete is True
        assert len(pcm) / (PCM_SAMPLE_RATE * 2) == 0.1
        assert MAX_LIVE_SECONDS < 600
    finally:
        await client.aio.aclose()
        client.close()
