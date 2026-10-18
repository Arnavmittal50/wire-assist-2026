"""Consented, unidirectional media on the browser parent leg. Never stores audio."""
import asyncio
import base64
import binascii
import heapq
import json
import logging
import re
import secrets
import struct
import time
from contextlib import suppress
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException, WebSocketDisconnect
from twilio.request_validator import RequestValidator

from app.schemas.contracts import TranscriptInput
from app.services.diarization import offset_seconds
from app.services.transcription import create_transcription_adapter

logger = logging.getLogger(__name__)
ROLES = {'inbound': 'teller', 'outbound': 'member'}


def valid_media_signature(settings, origin, path, query, signature):
    """Media handshakes are signed with the original WSS URL, unlike HTTP webhooks.

    Twilio documents a possible trailing-slash variant for Voice WSS requests.
    Both forms use only the trusted configured origin and actual request path/query.
    """
    base = origin.replace('https://', 'wss://', 1)
    suffix = '?' + query if query else ''
    validator = RequestValidator(settings.twilio_auth_token)
    return any(validator.validate(base + candidate + suffix, {}, signature)
               for candidate in {path, path.rstrip('/') + '/'})


def mulaw_sample(value):
    value = (~value) & 255
    magnitude = (((value & 15) << 3) + 132) << ((value >> 4) & 7)
    return 132 - magnitude if value & 128 else magnitude - 132


MULAW = tuple(mulaw_sample(i) for i in range(256))


class AudioTrack:
    """100 ms reorder window; timestamp gaps become silence, never shifted speech."""
    def __init__(self):
        self.pending = []
        self.seen = set()
        self.latest = -1
        self.next_ms = None
        self.previous = 0
        self.dropped = 0

    def push(self, chunk, timestamp, payload):
        if chunk in self.seen or (self.next_ms is not None and timestamp < self.next_ms):
            return []
        self.seen.add(chunk)
        if len(self.seen) > 1000:
            self.seen = set(sorted(self.seen)[-500:])
        self.latest = max(self.latest, timestamp)
        heapq.heappush(self.pending, (timestamp, chunk, payload))
        return self.flush(self.latest - 100)

    def flush(self, through=float('inf')):
        ready = []
        while self.pending and self.pending[0][0] <= through:
            stamp, _, payload = heapq.heappop(self.pending)
            if self.next_ms is not None and stamp < self.next_ms:
                continue
            if self.next_ms is not None and stamp > self.next_ms:
                gap = stamp - self.next_ms
                if gap > 5000:
                    raise ValueError('Audio gap exceeds safe transcription continuity')
                ready.append((self.next_ms, bytes(round(gap * 32)), False))
                self.previous = 0
                self.dropped += 1
            samples = [MULAW[value] for value in payload]
            pcm = bytearray()
            for sample in samples:
                pcm.extend(struct.pack('<hh', (self.previous + sample) // 2, sample))
                self.previous = sample
            ready.append((stamp, bytes(pcm), any(abs(value) > 200 for value in samples)))
            self.next_ms = stamp + len(payload) / 8
        return ready


@dataclass
class Binding:
    ticket: str
    session_id: str
    expires: float
    parent: str = ''
    child: str = ''
    stream: str = ''
    consented: bool = False
    connected: bool = False
    stream_accepted: bool = False
    ended: bool = False
    draining: bool = False
    done: object = field(default_factory=asyncio.Event)
    task: object = None
    end_task: object = None
    socket: object = None
    workers: list = field(default_factory=list)


class TwilioMedia:
    def __init__(self, settings, service, origin, adapter_factory=create_transcription_adapter):
        self.settings, self.service, self.origin = settings, service, origin
        self.adapter_factory = adapter_factory
        self.bindings = {}
        self.cleanup_tasks = set()

    def prepare(self, session_id):
        session = self.service.active(session_id)
        if not session.consent or session.mode != 'live' or session.audio_status != 'stopped':
            raise HTTPException(409, 'Use a consented live review and stop the separate microphone first.')
        self.bindings = {k: b for k, b in self.bindings.items() if b.parent and not b.ended or b.expires > time.monotonic()}
        if any(b.session_id == session_id and not b.ended for b in self.bindings.values()):
            raise HTTPException(409, 'A call is already prepared for this review.')
        if len(self.bindings) >= self.settings.max_sessions:
            raise HTTPException(429, 'Too many prepared calls.')
        # Calls save finalized conversation notes, including reviews opened before
        # retention became the default. Honor the server's storage setting.
        if self.settings.allow_transcript_retention and not session.retain_transcript:
            session.retain_transcript = True
            session.revision += 1
            self.service.repository.save(session)
        ticket = secrets.token_urlsafe(32)
        self.bindings[ticket] = Binding(ticket, session_id, time.monotonic() + 120, consented=True)
        return ticket

    def bind(self, ticket, parent):
        b = self.bindings.get(ticket)
        if not b or b.ended or b.expires < time.monotonic() or b.parent and b.parent != parent:
            raise HTTPException(403, 'Invalid or expired call preparation.')
        self.service.active(b.session_id)
        b.parent = parent
        return b

    async def error(self, b, message):
        session = self.service.sessions.get(b.session_id)
        if session and session.status == 'active' and not b.ended:
            session.transcription_notice = message
            await self.service.publish(session, 'transcription_error', {'message': message})

    async def start_stream(self, b):
        """Official Streams REST API, equivalent to Start/Stream both_tracks."""
        from twilio.http.async_http_client import AsyncTwilioHttpClient
        from twilio.rest import Client
        transport = AsyncTwilioHttpClient(timeout=10)
        try:
            client = Client(self.settings.twilio_account_sid, self.settings.twilio_auth_token, http_client=transport)
            stream = await client.calls(b.parent).streams.create_async(
                url=self.origin().replace('https://', 'wss://', 1) + '/twilio/media/' + b.ticket,
                track='both_tracks', name='wire-assist-' + b.session_id)
            if b.stream and b.stream != stream.sid:
                raise ValueError('Unexpected stream identifier')
            b.stream = stream.sid
            logger.info('Twilio media requested parent=%s stream=%s roles=inbound:teller,outbound:member', b.parent, b.stream)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = getattr(error, 'status', None)
            logger.warning('Twilio stream start failed error=%s http_status=%s', type(error).__name__, code)
            detail = f' (HTTP {code})' if isinstance(code, int) else ''
            await self.error(b, f'Call audio processing could not start{detail}. The phone call can continue normally.')
        finally:
            await transport.close()

    def status(self, parent, child, status):
        for b in self.bindings.values():
            if b.parent == parent and not b.ended:
                if b.child and child and b.child != child:
                    continue
                if child:
                    b.child = child
                if status == 'in-progress':
                    b.connected = True
                    if b.consented and b.task is None:
                        b.task = asyncio.create_task(self.start_stream(b))
                if status in {'completed', 'busy', 'no-answer', 'failed', 'canceled'}:
                    self.schedule_end(b)

    def schedule_end(self, b):
        if b.ended:
            return
        b.ended = True
        b.draining = True
        task = asyncio.create_task(self.end(b))
        b.end_task = task
        self.cleanup_tasks.add(task)
        task.add_done_callback(self.cleanup_tasks.discard)

    async def end(self, b):
        if b.task and not b.task.done():
            b.task.cancel()
            await asyncio.gather(b.task, return_exceptions=True)
        b.draining = True
        if b.socket:
            with suppress(Exception):
                await b.socket.close()
            with suppress(TimeoutError):
                await asyncio.wait_for(b.done.wait(), timeout=8)
        for worker in b.workers:
            worker.cancel()
        if b.workers:
            await asyncio.gather(*b.workers, return_exceptions=True)
        b.draining = False
        session = self.service.sessions.get(b.session_id)
        if session and session.status == 'active':
            session.transcription_notice = ''
            session.audio_status = 'stopped'
            # Finalized content only; do not let late provider callbacks reopen this session.
            await self.service.end(b.session_id)
            await self.service.emit(b.session_id, 'call_ended', {'call_sid': b.parent})
        self.bindings.pop(b.ticket, None)

    async def stop_session(self, session_id):
        for b in list(self.bindings.values()):
            if b.session_id == session_id:
                self.schedule_end(b)
                if b.end_task:
                    await asyncio.shield(b.end_task)

    async def shutdown(self):
        for b in list(self.bindings.values()):
            self.schedule_end(b)
        if self.cleanup_tasks:
            await asyncio.gather(*list(self.cleanup_tasks), return_exceptions=True)

    async def websocket(self, socket, ticket):
        b = self.bindings.get(ticket)
        query = socket.scope.get('query_string', b'').decode('ascii')
        if not b or not b.consented or b.ended or b.socket is not None or b.stream_accepted:
            logger.warning('Twilio media rejected: missing, inactive, or already-used call binding')
            await socket.close(code=4403)
            return
        if not valid_media_signature(self.settings, self.origin(), socket.url.path, query,
                                     socket.headers.get('x-twilio-signature', '')):
            logger.warning('Twilio media rejected: invalid WSS handshake signature')
            await self.error(b, 'Twilio audio connection could not be authenticated. Check the public backend URL and Twilio Auth Token.')
            await socket.close(code=4403)
            return
        await socket.accept()
        b.socket = socket
        queues = {track: asyncio.Queue(maxsize=self.settings.twilio_audio_queue_frames) for track in ROLES}
        tracks = {track: AudioTrack() for track in ROLES}
        started = False
        failed_tracks = set()
        seen_sequences = set()
        try:
            while not b.ended:
                raw = await asyncio.wait_for(socket.receive_text(), timeout=15)
                if len(raw) > 8192:
                    raise ValueError('Media message too large')
                message = json.loads(raw)
                event = message.get('event')
                if event == 'connected':
                    continue
                if event == 'start':
                    start = message['start']
                    if (started or start.get('accountSid') != self.settings.twilio_account_sid
                            or start.get('callSid') != b.parent or set(start.get('tracks', [])) != set(ROLES)
                            or start.get('mediaFormat') != {'encoding': 'audio/x-mulaw', 'sampleRate': 8000, 'channels': 1}
                            or not re.fullmatch(r'MZ[0-9a-fA-F]{32}', start.get('streamSid', ''))
                            or b.stream and b.stream != start['streamSid']):
                        raise ValueError('Unverified track metadata')
                    b.stream = start['streamSid']
                    b.stream_accepted = True
                    started = True
                    logger.info('Twilio media verified parent=%s child=%s stream=%s tracks=inbound:teller,outbound:member', b.parent, b.child, b.stream)
                    b.workers = [asyncio.create_task(self.worker(b, track, queues[track])) for track in ROLES]
                    session = self.service.active(b.session_id)
                    session.audio_status = 'recording'
                    session.transcription_notice = 'Transcribing call audio — teller and member tracks connected.'
                    await self.service.publish(session, 'transcription_status')
                elif event == 'media':
                    if not started or message.get('streamSid') != b.stream:
                        raise ValueError('Unexpected stream')
                    media = message['media']
                    sequence = int(message['sequenceNumber'])
                    if sequence < 1:
                        raise ValueError('Invalid media sequence')
                    if sequence in seen_sequences:
                        continue
                    seen_sequences.add(sequence)
                    if len(seen_sequences) > 1000:
                        seen_sequences = set(sorted(seen_sequences)[-500:])
                    track = media['track']
                    if track not in ROLES:
                        raise ValueError('Unknown track; no speaker assigned')
                    if not b.connected or track in failed_tracks:
                        continue  # No ringback, notice, or pre-consent audio to Gemini.
                    stamp, chunk = int(media['timestamp']), int(media['chunk'])
                    payload = base64.b64decode(media['payload'], validate=True)
                    if not 0 <= stamp <= 600000 or chunk < 1 or not 0 < len(payload) <= 1600:
                        raise ValueError('Invalid audio packet')
                    for frame in tracks[track].push(chunk, stamp, payload):
                        try:
                            queues[track].put_nowait(frame)
                        except asyncio.QueueFull:
                            failed_tracks.add(track)
                            b.workers[list(ROLES).index(track)].cancel()
                            tracks[track].pending.clear()
                            await self.error(b, f'{ROLES[track].title()} transcription fell behind and stopped; call audio is unaffected.')
                            break
                elif event == 'stop':
                    if not started or message.get('streamSid') != b.stream:
                        raise ValueError('Unexpected stream stop')
                    break
        except (WebSocketDisconnect, RuntimeError):
            pass
        except (ValueError, KeyError, TypeError, binascii.Error, TimeoutError):
            await self.error(b, 'Call transcription interrupted: audio metadata or connection could not be verified. Speaker labels were not guessed.')
        finally:
            # Drain the reorder tail and ask Gemini to finalize authorized speech.
            b.draining = True
            for track, queue in queues.items():
                try:
                    tail = tracks[track].flush()
                except ValueError:
                    tail = []
                for frame in tail:
                    with suppress(asyncio.QueueFull):
                        queue.put_nowait(frame)
                with suppress(asyncio.QueueFull):
                    queue.put_nowait(None)
            if b.workers:
                with suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.gather(*b.workers, return_exceptions=True), timeout=6)
            b.draining = False
            for track in tracks.values():
                track.pending.clear()
            for queue in queues.values():
                while not queue.empty():
                    queue.get_nowait()
            b.socket = None
            b.done.set()
            with suppress(Exception):
                await socket.close()
            if not b.ended:
                await self.error(b, 'Call transcription stopped. The phone call can continue; finalized text remains available.')

    async def worker(self, b, track, queue):
        role = ROLES[track]
        base = None
        end = 0.0
        last_end = 0.0
        heard_speech = False
        speech_times = deque(maxlen=15000)
        turn_speech = False
        silence_ms = 0
        buffered = bytearray()
        adapter = None

        async def on_text(text, finalized, start_offset=None, end_offset=None):
            nonlocal heard_speech, last_end
            session = self.service.sessions.get(b.session_id)
            if b.ended and not b.draining or not session or session.status != 'active' or not heard_speech or base is None:
                return
            if not finalized:
                return  # Independent interim previews must not overwrite one another.
            start_value, end_value = offset_seconds(start_offset), offset_seconds(end_offset)
            start_time = base + start_value if start_value is not None else (speech_times[0] if speech_times else last_end or base)
            if end_value is not None:
                end_time = min(end, base + end_value)
            else:
                # Estimate from media speech activity, not provider response arrival.
                # Keep later utterances queued if a delayed final follows a silence gap.
                tail = start_time
                for stamp in speech_times:
                    if stamp - tail > 0.5:
                        break
                    tail = stamp
                end_time = min(end, tail + 0.02)
            if end_time <= last_end or not text.strip():
                return
            if not any(start_time <= stamp < end_time for stamp in speech_times):
                return  # A provider must not invent text for a silent interval.
            last_end = end_time
            while speech_times and speech_times[0] < end_time:
                speech_times.popleft()
            heard_speech = bool(speech_times)
            await self.service.transcript(b.session_id, TranscriptInput(
                segment_id=f'{b.stream}:{track}:{end_time:.3f}', sequence=session.next_sequence,
                speaker=role, text=text[:4000]), source='gemini', metadata={
                    'call_sid': b.parent, 'stream_sid': b.stream, 'audio_track': track,
                    'speaker_source': 'twilio_track', 'speaker_role': role, 'is_final': True,
                    'start_time': max(0, min(start_time, end_time)), 'end_time': end_time,
                    'timing_source': 'provider' if start_value is not None else 'estimated',
                    'transcription_source': 'gemini_live', 'diarization_status': 'CONFIRMED',
                })

        async def on_error(message):
            await self.error(b, f'{role.title()}: {message}')

        try:
            # Wait for the first post-consent, connected frame before contacting Gemini.
            first = await queue.get()
            if first is None:
                return
            adapter = self.adapter_factory(self.settings, on_text, on_error)
            await adapter.open()
            frame = first
            while not b.ended or b.draining:
                if frame is None:
                    if buffered:
                        await adapter.send(bytes(buffered))
                        buffered.clear()
                    await adapter.finish()
                    break
                stamp, pcm, speech = frame
                if base is None:
                    base = stamp / 1000
                end = stamp / 1000 + len(pcm) / 32000
                heard_speech = heard_speech or speech
                if speech:
                    speech_times.append(stamp / 1000)
                turn_speech = turn_speech or speech
                silence_ms = 0 if speech else silence_ms + len(pcm) / 32
                buffered.extend(pcm)
                # Google's documented 100 ms PCM chunks, separately for each voice.
                while len(buffered) >= 3200:
                    chunk = bytes(buffered[:3200])
                    del buffered[:3200]
                    await adapter.send(chunk)
                if turn_speech and silence_ms >= 500 and hasattr(adapter, 'end_turn'):
                    if buffered:
                        await adapter.send(bytes(buffered))
                        buffered.clear()
                    await adapter.end_turn()
                    turn_speech = False
                frame = await queue.get()
        except asyncio.CancelledError:
            raise
        except Exception:
            await on_error('Transcription unavailable. The phone call remains connected.')
        finally:
            buffered.clear()
            speech_times.clear()
            if adapter:
                await adapter.close()
