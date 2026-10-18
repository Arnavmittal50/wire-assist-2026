import asyncio
import hmac
import json
import time
from contextlib import suppress
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.config import Settings
from app.schemas.contracts import ActionInput, AudioControl, CreateSession, Session, SessionSummary, TranscriptInput, SpeakerCorrection
from app.services.audio import AudioCoordinator, AudioFormatError, AudioRateError
from app.services.transcription import TranscriptionUnavailable
from app.services.member_data import MemberData
from app.services.sessions import SessionService
from app.services.twilio_calling import create_twilio_router, public_origin
from app.services.twilio_media import TwilioMedia


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    members = MemberData(config.data_dir)
    service = SessionService(config, members)
    audio = AudioCoordinator(service)
    media = TwilioMedia(config, service, lambda: public_origin(config))

    @asynccontextmanager
    async def lifespan(app):
        yield
        await media.shutdown()
        await audio.shutdown()
        await service.shutdown()

    app = FastAPI(title="Wire Assist 2.0", version="2.0.0", lifespan=lifespan)
    app.state.service = service
    app.state.settings = config
    app.state.audio = audio
    app.state.twilio_media = media
    app.add_middleware(CORSMiddleware, allow_origins=config.origins,
                       allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Authorization", "Content-Type"])
    requests = defaultdict(deque)

    @app.middleware("http")
    async def limits(request: Request, call_next):
        if request.method in {"POST", "PUT", "PATCH"}:
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 32768:
                return JSONResponse({"detail": "Request exceeds the allowed size."}, status_code=413)
            if len(await request.body()) > 32768:
                return JSONResponse({"detail": "Request exceeds the allowed size."}, status_code=413)
        address = request.client.host if request.client else "local"
        clock = time.monotonic()
        bucket = requests[address]
        while bucket and clock - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= 240:
            return JSONResponse({"detail": "Too many requests. Try again shortly."}, status_code=429)
        bucket.append(clock)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    async def authorize(authorization: str | None = Header(default=None)):
        expected = "Bearer " + config.dev_auth_token
        if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
            raise HTTPException(401, "A valid development access token is required.")

    app.include_router(create_twilio_router(config, authorize, media))

    @app.get("/health")
    def health():
        return {"status": "ok", "analysis_provider": "groq" if config.groq_api_key else "unavailable",
                "transcription_provider": "gemini_live" if config.gemini_api_key else "manual",
                "analysis_model": config.groq_precall_model,
                "update_model": config.groq_model,
                "transcription_model": config.gemini_transcription_model,
                "data_source": members.members()[0]["data_source"] if members.members() else "empty",
                "provider_verified": False}

    @app.get("/members", dependencies=[Depends(authorize)])
    def list_members():
        return members.members()

    @app.get("/members/{member_id}", dependencies=[Depends(authorize)])
    def get_member(member_id: str):
        member = members.member(member_id)
        if not member:
            raise HTTPException(404, "Member not found.")
        return member

    @app.get("/members/{member_id}/events", dependencies=[Depends(authorize)])
    def events(member_id: str, as_of: datetime = datetime(2026, 9, 27, 2, 25, tzinfo=timezone.utc)):
        get_member(member_id)
        if as_of.tzinfo is None:
            raise HTTPException(422, "as_of must include a timezone.")
        return members.events(member_id, as_of)

    @app.post("/members/{member_id}/pre-analysis", dependencies=[Depends(authorize)])
    async def pre_analysis(member_id: str, request: CreateSession):
        if request.member_id != member_id:
            raise HTTPException(422, "Member mismatch.")
        member = get_member(member_id)
        # Synthetic history only. This does not grant audio-processing consent.
        preview = Session(**{**request.model_dump(), "consent": True, "mode": "live"}, data_source=member["data_source"])
        return await service.analysis.initialize(preview, members.events(member_id, request.as_of))

    @app.post("/sessions", response_model=Session, dependencies=[Depends(authorize)])
    async def create_session(request: CreateSession):
        session = service.create(request)
        service.start_analysis(session)
        return session

    @app.get("/sessions", response_model=list[Session], dependencies=[Depends(authorize)])
    async def list_sessions():
        return service.list_sessions()

    @app.get("/sessions/{session_id}", response_model=Session, dependencies=[Depends(authorize)])
    async def get_session(session_id: str):
        return service.get(session_id)

    @app.get("/sessions/{session_id}/insights", dependencies=[Depends(authorize)])
    async def get_insights(session_id: str):
        return service.get(session_id).insights

    @app.post("/sessions/{session_id}/transcript", response_model=Session, dependencies=[Depends(authorize)])
    async def transcript(session_id: str, request: TranscriptInput):
        return await service.transcript(session_id, request)

    @app.post("/sessions/{session_id}/audio", response_model=Session, dependencies=[Depends(authorize)])
    async def audio_control(session_id: str, request: AudioControl):
        return await audio.control(session_id, request)

    @app.post("/sessions/{session_id}/transcript/{segment_id}/speaker", response_model=Session, dependencies=[Depends(authorize)])
    async def correct_speaker(session_id: str, segment_id: str, request: SpeakerCorrection):
        return await service.correct_speaker(session_id, segment_id, request.speaker, request.scope)

    @app.post("/sessions/{session_id}/guidance/retry", response_model=Session, dependencies=[Depends(authorize)])
    async def retry_guidance(session_id: str):
        return await service.retry_guidance(session_id)

    @app.post("/sessions/{session_id}/actions", response_model=Session, dependencies=[Depends(authorize)])
    async def record_action(session_id: str, request: ActionInput):
        return await service.action(session_id, request)

    @app.post("/sessions/{session_id}/end", response_model=Session, dependencies=[Depends(authorize)])
    async def end_session(session_id: str):
        await media.stop_session(session_id)
        return await service.finish(session_id, audio)

    @app.get("/sessions/{session_id}/summary", response_model=SessionSummary, dependencies=[Depends(authorize)])
    async def summary(session_id: str):
        return service.summary(session_id)

    @app.delete("/sessions/{session_id}", status_code=204, dependencies=[Depends(authorize)])
    async def delete_session(session_id: str):
        await media.stop_session(session_id)
        service.get(session_id).status = "ended"
        await audio.stop(session_id)
        await service.delete(session_id)
        return Response(status_code=204)

    @app.websocket("/ws/sessions/{session_id}")
    async def websocket(socket: WebSocket, session_id: str):
        origin = socket.headers.get("origin")
        if origin and origin not in config.origins:
            await socket.close(code=4403)
            return
        await socket.accept()
        try:
            raw = await asyncio.wait_for(socket.receive_text(), timeout=5)
            if len(raw) > 1024:
                await socket.close(code=4401)
                return
            auth = json.loads(raw)
            if not isinstance(auth, dict) or auth.get("type") != "authenticate" or not isinstance(auth.get("token"), str) or not hmac.compare_digest(auth["token"].encode(), config.dev_auth_token.encode()):
                await socket.close(code=4401)
                return
            session = service.get(session_id)
        except (ValueError, TimeoutError, HTTPException, WebSocketDisconnect, KeyError, TypeError, RuntimeError):
            with suppress(RuntimeError, WebSocketDisconnect):
                await socket.close(code=4401)
            return
        service.connections.setdefault(session_id, set()).add(socket)
        await service.emit(session_id, "connection_status", {"status": "connected"}, target=socket)
        await service.emit(session_id, "session_status", {"session": session.model_dump(mode="json")}, target=socket)
        try:
            text_times = deque()
            while True:
                incoming = await socket.receive()
                if incoming["type"] == "websocket.disconnect":
                    break
                kind = None
                try:
                    if incoming.get("bytes") is not None:
                        await audio.send(session_id, socket, incoming["bytes"])
                        continue
                    raw = incoming.get("text", "")
                    if not isinstance(raw, str):
                        raise ValueError("Expected JSON text.")
                    if len(raw) > 8192:
                        await socket.close(code=1009)
                        break
                    clock = time.monotonic()
                    while text_times and clock - text_times[0] > 10:
                        text_times.popleft()
                    if len(text_times) >= 30:
                        await socket.close(code=1008)
                        break
                    text_times.append(clock)
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError("Expected a JSON object.")
                    kind = message.pop("type", None)
                    if kind == "ping":
                        await service.emit(session_id, "connection_status", {"status": "connected"}, target=socket)
                    elif kind == "audio_control":
                        await audio.control(session_id, AudioControl.model_validate(message), owner=socket)
                    elif kind == "transcript_submit":
                        await service.transcript(session_id, TranscriptInput.model_validate(message))
                    else:
                        raise ValueError("Unsupported message type.")
                except (ValidationError, ValueError) as error:
                    await audio.stop(session_id, owner=socket)
                    if isinstance(error, AudioFormatError):
                        message = "Invalid audio packet. Use complete PCM16 mono samples at 16 kHz or manual text."
                        code = "invalid_audio_format"
                    elif isinstance(error, AudioRateError):
                        message = "Audio arrived too quickly. Stop and restart recording, or use manual text."
                        code = "audio_rate_error"
                    elif isinstance(error, ValidationError) and kind == "audio_control":
                        message = "Invalid microphone control message. Refresh the page and restart the backend to use matching versions. Speaker must be auto, member, teller, or unknown."
                        code = "invalid_audio_control"
                    elif isinstance(error, ValidationError) and kind == "transcript_submit":
                        message = "Invalid transcript message. Check the text, sequence, and speaker, then retry."
                        code = "invalid_transcript"
                    else:
                        message = "Invalid WebSocket message. Refresh the page and retry."
                        code = "invalid_message"
                    await service.emit(session_id, "error", {"message": message, "code": code}, target=socket)
                    await service.publish(session, "session_status")
                except (HTTPException, TranscriptionUnavailable) as error:
                    await audio.stop(session_id, owner=socket)
                    await service.emit(session_id, "error", {"message": error.detail if isinstance(error, HTTPException) else str(error), "code": "audio_error"}, target=socket)
                    await service.publish(session, "session_status")
        except (WebSocketDisconnect, ValueError):
            pass
        finally:
            await audio.stop(session_id, owner=socket)
            service.connections.get(session_id, set()).discard(socket)
            if session_id in service.sessions:
                await service.publish(session, "session_status")

    return app


app = create_app()
