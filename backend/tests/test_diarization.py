import asyncio
import base64
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import wave

import pytest

from app.schemas.contracts import AnalysisResult, AudioControl, CreateSession, InitialAnalysis, Segment, TranscriptInput
from app.services.analysis import AnalysisService, _context
from app.services.audio import AudioCoordinator
from app.services.audio_buffer import AudioBatch, AudioBuffer, pcm_to_wav
from app.services.diarization import GeminiDiarizationService, DiarizationUnavailable, SpokenWord, parse_annotations
from app.services.member_data import MemberData
from app.services.sessions import SessionService
from app.services.speaker_reconciliation import SpeakerReconciler


def batch(index=0, start=0, end=12, recording='r'):
    return AudioBatch(recording, index, start, end, b'\x01\x00' * 1600)


def word(text, speaker, start, end=None):
    return SpokenWord(text, speaker, start, end if end is not None else start + .15)


def coordinator(config):
    service = SessionService(config, MemberData(config.data_dir))
    current = service.create(CreateSession(member_id='M1002', consent=True, mode='live', retain_transcript=True))
    current.initial_analysis = InitialAnalysis(provider='gemini', model='mock', notice='reviewed', event_count=37)
    service.analysis.analyze = AsyncMock(return_value=(AnalysisResult(), 'gemini', 'reviewed'))
    return service, current


async def tick(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(wait(), 2)


def test_bounded_sample_clock_overlap_and_real_wav_encoding():
    buffer = AudioBuffer('r', 5, 12, 2)
    assert not buffer.append(b'\x01\x00' * 16000 * 11)
    first = buffer.append(b'\x02\x00' * 16000)[0]
    assert (first.start, first.end) == (5, 17)
    assert len(buffer.data) == 2 * 32000
    second = buffer.append(b'\x03\x00' * 16000 * 10)[0]
    assert (second.start, second.end) == (15, 27)
    assert first.pcm[-64000:] == second.pcm[:64000]
    with wave.open(BytesIO(pcm_to_wav(first.pcm))) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(), wav.getnframes()) == (1, 2, 16000, 192000)
    assert buffer.flush() is None
    assert not buffer.data


async def test_sdk_request_is_verbatim_diarization_with_inline_wav_and_no_storage(config):
    config.gemini_api_key = 'mock'
    response = {'status': 'completed', 'steps': [{'content': [{'annotations': [
        {'type': 'word_info', 'text': 'Hello', 'speaker': 'spk_1', 'start_offset': '0.1s', 'end_offset': '0.4s'},
        {'type': 'word_info', 'text': 'Hi', 'speaker': 'spk_2', 'start_offset': '0.6s', 'end_offset': '0.8s'}]}]}]}
    generate = AsyncMock(return_value=response)
    client = SimpleNamespace(aio=SimpleNamespace(interactions=SimpleNamespace(create=generate), aclose=AsyncMock()), close=Mock())
    provider = GeminiDiarizationService(config, lambda key: client)
    words = await provider.transcribe(batch(start=10, end=22))
    assert {w.speaker for w in words} == {'spk_1', 'spk_2'}
    assert words[0].start == 10.1
    args = generate.await_args.kwargs
    assert args['model'] == 'gemini-3.5-transcribe' and args['store'] is False
    assert args['generation_config']['transcription_config']['mode'] == {
        'type': 'verbatim', 'diarization_mode': 'speaker', 'timestamp_granularities': ['word']}
    with wave.open(BytesIO(base64.b64decode(args['input'][0]['data']))) as wav:
        assert wav.getframerate() == 16000
    client.aio.aclose.assert_awaited_once()


async def test_overlapping_batches_reconcile_swapped_local_labels_and_reordered_completion(config):
    service, current = coordinator(config)
    reconcile = SpeakerReconciler(current, 'r', 0)
    first = [word('Welcome', 'a', .1), word('please', 'a', 10), word('explain', 'a', 10.3),
             word('my', 'b', 11), word('brother', 'b', 11.3)]
    second = [word('please', 'spk_2', 10), word('explain', 'spk_2', 10.3),
              word('my', 'spk_1', 11), word('brother', 'spk_1', 11.3), word('called', 'spk_1', 12)]
    reconcile.accept(batch(1, 10, 22), second)
    assert not current.speaker_roles
    reconcile.accept(batch(), first)
    assert current.speaker_roles == {'speaker_1': 'teller', 'speaker_2': 'member'}
    segment = Segment(sequence=0, text='My brother called', speaker='unknown', recording_id='r', start_time=10, end_time=14)
    assert reconcile.match(segment)[0] == 'speaker_2'
    await service.shutdown()


async def test_no_overlap_evidence_never_reuses_a_local_label_or_guesses_role(config):
    service, current = coordinator(config)
    reconcile = SpeakerReconciler(current, 'r', 0)
    reconcile.accept(batch(), [word('Welcome', 'spk_1', 1), word('Hello', 'spk_2', 2)])
    reconcile.accept(batch(1, 10, 22), [word('Send', 'spk_1', 16), word('now', 'spk_1', 16.3)])
    segment = Segment(sequence=0, text='Send now', recording_id='r', start_time=15, end_time=18)
    identity, *_ = reconcile.match(segment)
    assert identity != 'speaker_1' and current.speaker_roles[identity] == 'unknown'
    await service.shutdown()


async def test_unidentified_opening_voice_does_not_make_a_later_voice_the_teller(config):
    service, current = coordinator(config)
    reconcile = SpeakerReconciler(current, 'r', 0)
    reconcile.accept(batch(), [word('Welcome', None, .1), word('Hello', 'spk_2', 2)])
    assert all(role == 'unknown' for role in current.speaker_roles.values())
    assert not reconcile.can_anchor_first_voice
    await service.shutdown()


async def test_mixed_and_simultaneous_speech_is_uncertain(config):
    service, current = coordinator(config)
    reconcile = SpeakerReconciler(current, 'r', 0)
    reconcile.accept(batch(), [word('Send', 'a', 1), word('now', 'b', 1.05)])
    segment = Segment(sequence=0, text='Send now', recording_id='r', start_time=0, end_time=3)
    assert reconcile.match(segment)[3] == 'UNCERTAIN'
    await service.shutdown()


async def test_late_batch_updates_existing_bubble_and_manual_mapping_is_authoritative(config):
    service, current = coordinator(config)
    reconcile = SpeakerReconciler(current, 'r', 0)
    await service.transcript(current.session_id, TranscriptInput(segment_id='s', sequence=0, text='My brother called.', speaker='unknown'), source='gemini', metadata={
        'recording_id': 'r', 'start_time': 1, 'end_time': 5, 'speaker_source': 'diarization', 'diarization_status': 'PENDING'})
    await service.tasks[current.session_id]
    assert service.analysis.analyze.await_count == 0
    reconcile.accept(batch(), [word('Welcome', 'a', .1), word('My', 'b', 2), word('brother', 'b', 2.3), word('called', 'b', 2.6)])
    await service.reconcile_speakers(reconcile)
    await service.tasks[current.session_id]
    assert len(current.segments) == 1 and current.segments[0].segment_id == 's'
    segment = current.segments[0]
    assert segment.speaker == 'member' and segment.diarization_status == 'CONFIRMED' and segment.revision == 1
    await service.correct_speaker(current.session_id, 's', 'teller', scope='speaker')
    await service.tasks[current.session_id]
    await service.reconcile_speakers(reconcile)
    assert segment.speaker == 'teller' and segment.revision == 2
    assert current.speaker_roles[segment.speaker_id] == 'teller'
    assert any('Speaker attribution' in item.description for item in current.activity)
    current.status = 'ended'
    before = current.revision
    await service.reconcile_speakers(reconcile)
    assert current.revision == before
    await service.shutdown()


async def test_results_arriving_before_live_text_are_reconciled_without_rewriting(config):
    service, current = coordinator(config)
    reconcile = SpeakerReconciler(current, 'r', 0)
    reconcile.accept(batch(), [word('Welcome', 'a', .1), word('My', 'b', 2), word('brother', 'b', 2.3), word('called', 'b', 2.6)])
    await service.transcript(current.session_id, TranscriptInput(sequence=0, text='My brother called!', speaker='unknown'), source='gemini', metadata={
        'recording_id': 'r', 'start_time': 0, 'end_time': 5, 'speaker_source': 'diarization', 'diarization_status': 'PENDING'})
    await service.reconcile_speakers(reconcile)
    assert len(current.segments) == 1 and current.segments[0].text == 'My brother called!'
    assert current.segments[0].speaker == 'member'
    await service.shutdown()


async def test_live_text_continues_during_batch_processing_backlog_is_bounded_and_end_cleans(config):
    config.gemini_api_key = 'mock'
    config.batch_duration_seconds = 2
    config.batch_overlap_seconds = 1
    service, current = coordinator(config)
    audio = AudioCoordinator(service)
    entered, release = asyncio.Event(), asyncio.Event()
    async def slow(batch):
        entered.set()
        await release.wait()
        return []
    audio.diarization.provider.transcribe = slow
    class Live:
        async def open(self): pass
        async def send(self, pcm): await self.callback('Immediate live words.', True)
        async def finish(self): pass
        async def close(self): pass
    live = Live()
    def factory(settings, on_transcript, on_error):
        live.callback = on_transcript
        return live
    audio.factory = factory
    owner = object()
    await audio.control(current.session_id, AudioControl(command='start'), owner)
    await audio.send(current.session_id, owner, b'\x00\x00' * 16000 * 2)
    await asyncio.wait_for(entered.wait(), 2)
    assert current.segments[0].text == 'Immediate live words.' and current.segments[0].diarization_status == 'PENDING'
    state = next(iter(audio.diarization.recordings.values()))
    # Feed the same validated format directly to isolate scheduler backpressure
    # from the separate realtime packet-rate guard.
    for _ in range(10):
        audio.diarization.feed(state.recording_id, b'\x00\x00' * 16000)
    assert state.pending <= config.max_pending_batches
    assert state.queue.qsize() <= config.max_pending_batches
    assert len(state.buffer.data) < 2 * 32000
    await audio.stop(current.session_id)
    assert not state.buffer.data and not state.reconciler.recent and state.queue.empty()
    assert not audio.diarization.recordings and state.task.done()
    assert current.segments[0].diarization_status == 'FAILED'
    await service.shutdown()


async def test_diarization_error_does_not_stop_live_adapter(config):
    config.gemini_api_key = 'mock'
    config.batch_duration_seconds = 2
    config.batch_overlap_seconds = 1
    service, current = coordinator(config)
    audio = AudioCoordinator(service)
    audio.diarization.provider.transcribe = AsyncMock(side_effect=DiarizationUnavailable('Model unavailable; live transcription continues.'))
    live = SimpleNamespace(open=AsyncMock(), send=AsyncMock(), finish=AsyncMock(), close=AsyncMock())
    audio.factory = lambda *args, **kwargs: live
    owner = object()
    await audio.control(current.session_id, AudioControl(command='start'), owner)
    await audio.send(current.session_id, owner, b'\x00\x00' * 16000 * 2)
    state = next(iter(audio.diarization.recordings.values()))
    await state.task
    assert current.audio_status == 'recording'
    assert 'Model unavailable' in current.diarization_notice
    live.close.assert_not_called()
    await audio.send(current.session_id, owner, b'\x00\x00' * 160)
    assert live.send.await_count == 2
    await audio.shutdown()
    await service.shutdown()


async def test_unconfirmed_member_label_cannot_supply_risk_evidence(config):
    service, current = coordinator(config)
    current.segments = [Segment(sequence=0, text='Send within 30 minutes.', speaker='member', speaker_source='diarization', diarization_status='PENDING')]
    context, sources, _ = _context(current, [])
    assert not sources and context['conversation'][0]['speaker'] == 'unknown'
    current.segments[0].diarization_status = 'CONFIRMED'
    assert _context(current, [])[1]
    await service.shutdown()


async def test_guidance_does_not_wait_for_history_and_uses_one_model_call_by_default(config):
    from test_analysis import service as analyzer_service, session
    analyzer, generate, _, _ = analyzer_service()
    analyzer._settings.guidance_review_pass = False
    _, provider, _ = await analyzer.analyze(session('Please review this.'), [])
    assert provider == 'gemini' and generate.await_count == 1
    service, current = coordinator(config)
    current.initial_analysis = None
    started = asyncio.Event()
    async def history(*args):
        started.set()
        await asyncio.Event().wait()
    service.analysis.initialize = history
    await service.transcript(current.session_id, TranscriptInput(sequence=0, text='My brother called.', speaker='member'))
    await asyncio.wait_for(service.tasks[current.session_id], 1)
    assert service.analysis.analyze.await_count == 1 and not current.analysis_pending
    await service.shutdown()


async def test_unknown_live_chunks_do_not_cancel_confirmed_member_guidance(config):
    service, current = coordinator(config)
    started, release = asyncio.Event(), asyncio.Event()
    async def analyze(*args):
        started.set()
        await release.wait()
        return AnalysisResult(), 'gemini', 'Completed review'
    service.analysis.analyze = AsyncMock(side_effect=analyze)
    await service.transcript(current.session_id, TranscriptInput(sequence=0, speaker='member', text='My brother called.'))
    await started.wait()
    task = service.tasks[current.session_id]
    for index in range(1, 5):
        await service.transcript(current.session_id, TranscriptInput(sequence=index, speaker='unknown', text='More live words.'),
                                 source='gemini', metadata={'speaker_source': 'diarization', 'diarization_status': 'PENDING'})
        assert service.tasks[current.session_id] is task and not task.cancelled()
    release.set()
    await asyncio.wait_for(task, 1)
    assert current.guidance_status == 'ready' and not current.analysis_pending
    assert current.analyzed_sentences and service.analysis.analyze.await_count == 1
    await service.shutdown()


async def test_entered_payment_and_member_signals_have_independently_validated_citations(config):
    from app.services.analysis import _grounded
    from app.schemas.contracts import Insight, Evidence, SignalObservation
    service, current = coordinator(config)
    current.payment.amount = 1000000
    current.segments = [Segment(sequence=0, segment_id='s0', speaker='member', text='Send within 30 minutes.')]
    context, segments, events = _context(current, [])
    assert context['events'] == []  # Do not invent an historical transaction.
    assert context['proposed_payment_evidence']['source_id'] == 'proposed_payment'
    signals = [SignalObservation(signal_type=kind, status='OBSERVED', source_type=source_type, source_id=source_id,
                                 evidence_quote=quote, explanation='Supported observation', suggested_question='Can we review this?')
               for kind, source_type, source_id, quote in [
                   ('TIME_PRESSURE', 'segment', 's0', '30 minutes'),
                   ('LARGE_TRANSACTION', 'event', 'proposed_payment', '1000000.0')]]
    proposal = AnalysisResult(insights=[Insight(title='Review the payment request', observation='An entered amount and stated deadline.',
        evidence=[Evidence(source_type='segment', source_id='s0', quote='Send within 30 minutes.')],
        suggested_question='Who asked you to send it?', signals=signals, recommended_action='ASK_FOLLOW_UP',
        priority='high', confidence_in_observation=.9)])
    result, suppressed, _ = _grounded(proposal, segments, events)
    assert not suppressed and len(result.insights[0].evidence) == 2
    assert set(result.insights[0].signal_codes) == {'TIME_PRESSURE', 'LARGE_TRANSACTION'}
    proposal.insights[0].signals[1].evidence_quote = 'invented amount'
    assert not _grounded(proposal, segments, events)[0].insights
    await service.shutdown()


async def test_failed_guidance_can_retry_without_duplicate_turns_or_concurrent_requests(config):
    service, current = coordinator(config)
    service.analysis.analyze = AsyncMock(return_value=(AnalysisResult(), 'unavailable', 'Temporary failure'))
    await service.transcript(current.session_id, TranscriptInput(sequence=0, speaker='member', text='My brother called.'))
    await service.tasks[current.session_id]
    assert current.guidance_status == 'unavailable' and not current.analyzed_sentences
    service.analysis.analyze = AsyncMock(return_value=(AnalysisResult(), 'gemini', 'Completed'))
    await service.retry_guidance(current.session_id)
    task = service.tasks[current.session_id]
    await service.retry_guidance(current.session_id)
    assert service.tasks[current.session_id] is task
    await task
    assert current.guidance_status == 'ready' and len(current.segments) == 1
    assert service.analysis.analyze.await_count == 1
    await service.shutdown()


async def test_late_history_success_does_not_hide_live_guidance_failure(config):
    service, current = coordinator(config)
    current.initial_analysis = None
    release = asyncio.Event()
    async def history(*args):
        await release.wait()
        return InitialAnalysis(provider='gemini', notice='History ready', event_count=37)
    service.analysis.initialize = history
    service.analysis.analyze = AsyncMock(return_value=(AnalysisResult(), 'unavailable', 'Guidance failed'))
    await service.transcript(current.session_id, TranscriptInput(sequence=0, speaker='member', text='My brother called.'))
    await service.tasks[current.session_id]
    history_task = service.initial_tasks[current.session_id]
    release.set()
    await history_task
    assert current.initial_analysis.provider == 'gemini'
    assert current.guidance_status == 'unavailable' and current.analysis_provider == 'unavailable'
    assert current.provider_notice == 'Guidance failed'
    await service.shutdown()
