"""Browser calling with optional, explicitly consented dual-track processing."""
from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from starlette.datastructures import FormData
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Dial, VoiceResponse

IDENTITY = "wire_assist_teller"  # Existing single-teller development authentication.
TOKEN_TTL = 600
TERMINAL = {"completed", "busy", "no-answer", "failed", "canceled"}
RANK = {"queued": 0, "initiated": 0, "ringing": 1, "in-progress": 2,
        **{status: 3 for status in TERMINAL}}


class CallStore:
    """Bounded, process-local call metadata; no numbers, tokens or audio stored."""
    def __init__(self):
        self.calls = {}

    def prune(self):
        cutoff = time.monotonic() - 7200
        self.calls = {sid: row for sid, row in self.calls.items() if row["created"] > cutoff}

    def start(self, sid):
        self.prune()
        if sid not in self.calls:
            if len(self.calls) >= 1000:
                raise HTTPException(503, "Call status capacity reached. Try again later.")
            self.calls[sid] = {"created": time.monotonic(), "status": "initiated", "sequence": -1,
                               "connected_at": None, "ended_at": None}

    def update(self, sid, status, sequence=None):
        self.prune()
        row = self.calls.get(sid)
        if row is None or status not in RANK or row["status"] in TERMINAL:
            return
        if sequence is not None and sequence <= row["sequence"]:
            return
        if RANK[status] < RANK[row["status"]]:
            return
        row["status"] = status
        if sequence is not None:
            row["sequence"] = sequence
        stamp = datetime.now(timezone.utc).isoformat()
        if status == "in-progress" and not row["connected_at"]:
            row["connected_at"] = stamp
        if status in TERMINAL:
            row["ended_at"] = stamp


def public_origin(settings):
    value = settings.public_backend_url.strip().rstrip("/")
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path or url.query or url.fragment):
        raise HTTPException(503, "Set PUBLIC_BACKEND_URL to the tunnel's full HTTPS origin without a path.")
    return value


def configured(settings, *, token=False):
    names = ["twilio_account_sid", "twilio_auth_token", "twilio_phone_number", "test_member_phone_number"]
    if token:
        names += ["twilio_api_key_sid", "twilio_api_key_secret", "twilio_twiml_app_sid"]
    missing = [name.upper() for name in names if not getattr(settings, name).strip()]
    if missing:
        raise HTTPException(503, "Twilio setup incomplete. Set " + ", ".join(missing) + " in backend/.env.")
    for name in ("twilio_phone_number", "test_member_phone_number"):
        if not re.fullmatch(r"\+[1-9][0-9]{7,14}", getattr(settings, name)):
            raise HTTPException(503, f"{name.upper()} must use E.164 format (a leading + and country code).")
    for name, prefix in [("twilio_account_sid", "AC"), *(
            [("twilio_api_key_sid", "SK"), ("twilio_twiml_app_sid", "AP")] if token else [])]:
        if not re.fullmatch(prefix + r"[a-fA-F0-9]{32}", getattr(settings, name)):
            raise HTTPException(503, f"Check {name.upper()} in backend/.env.")
    return public_origin(settings)


def create_twilio_router(settings, authorize, media=None):
    router = APIRouter()
    store = CallStore()

    async def signed_form(request: Request):
        if not settings.twilio_auth_token:
            raise HTTPException(503, "Twilio webhook validation is not configured.")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            raise HTTPException(415, "Expected a Twilio form request.")
        try:
            form = FormData(parse_qsl((await request.body()).decode("utf-8"), keep_blank_values=True,
                                      max_num_fields=200, errors="strict"))
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid Twilio form.") from None
        # Canonical external origin is trusted configuration, never Host or
        # X-Forwarded-* supplied by a caller. Works when a tunnel terminates TLS.
        external = public_origin(settings) + request.url.path
        query = request.scope.get("query_string", b"").decode("ascii")
        if query:
            external += "?" + query
        signature = request.headers.get("x-twilio-signature", "")
        if not RequestValidator(settings.twilio_auth_token).validate(external, form, signature):
            raise HTTPException(403, "Invalid Twilio signature.")
        if form.get("AccountSid") != settings.twilio_account_sid:
            raise HTTPException(403, "Unexpected Twilio account.")
        return form

    @router.get("/api/twilio/token", dependencies=[Depends(authorize)])
    async def token():
        configured(settings, token=True)
        access = AccessToken(settings.twilio_account_sid, settings.twilio_api_key_sid,
                             settings.twilio_api_key_secret, identity=IDENTITY, ttl=TOKEN_TTL)
        access.add_grant(VoiceGrant(outgoing_application_sid=settings.twilio_twiml_app_sid,
                                    incoming_allow=False))
        return {"token": access.to_jwt(), "identity": IDENTITY, "expires_in": TOKEN_TTL}

    @router.post("/twilio/voice")
    async def voice(request: Request):
        form = await signed_form(request)
        origin = configured(settings)
        if form.get("From") != "client:" + IDENTITY:
            raise HTTPException(403, "Only the authenticated browser caller is supported.")
        sid = form.get("CallSid", "")
        if not re.fullmatch(r"CA[a-fA-F0-9]{32}", sid):
            raise HTTPException(400, "Missing or invalid call identifier.")
        store.start(sid)
        response = VoiceResponse()
        ticket = form.get("ProcessingTicket", "")
        if ticket and media:
            media.bind(ticket, sid)
        dial = Dial(caller_id=settings.twilio_phone_number, answer_on_bridge=True, timeout=30,
                    action=origin + "/twilio/dial-complete", method="POST")
        # No caller-provided To/phone/recipient field is ever used.
        dial.number(settings.test_member_phone_number, status_callback=origin + "/twilio/status",
                    status_callback_method="POST", status_callback_event="initiated ringing answered completed")
        response.append(dial)
        return Response(str(response), media_type="application/xml")

    @router.post("/twilio/status")
    async def status(request: Request):
        form = await signed_form(request)
        try:
            sequence = int(form.get("SequenceNumber", "-1"))
        except ValueError:
            raise HTTPException(400, "Invalid callback sequence.") from None
        store.update(form.get("ParentCallSid"), form.get("CallStatus"), sequence)
        if media:
            media.status(form.get("ParentCallSid"), form.get("CallSid"), form.get("CallStatus"))
        return Response(status_code=204)

    @router.post("/twilio/dial-complete")
    async def complete(request: Request):
        form = await signed_form(request)
        store.update(form.get("CallSid"), form.get("DialCallStatus"))
        if media:
            media.status(form.get("CallSid"), form.get("DialCallSid"), form.get("DialCallStatus"))
        response = VoiceResponse()
        response.hangup()
        return Response(str(response), media_type="application/xml")

    @router.get("/api/twilio/calls/{call_sid}", dependencies=[Depends(authorize)])
    async def call_status(call_sid: str):
        store.prune()
        row = store.calls.get(call_sid)
        if row is None:
            raise HTTPException(404, "Call status not available yet.")
        return {key: row[key] for key in ("status", "connected_at", "ended_at")}

    if media:
        from pydantic import BaseModel, ConfigDict

        class PrepareCall(BaseModel):
            model_config = ConfigDict(extra="forbid")
            session_id: str
            participants_consent: bool

        @router.post("/api/twilio/prepare", dependencies=[Depends(authorize)])
        async def prepare(data: PrepareCall):
            if not data.participants_consent:
                raise HTTPException(422, "Confirm both participants consent to audio processing in the dashboard.")
            return {"ticket": media.prepare(data.session_id)}

        @router.websocket("/twilio/media/{ticket}")
        async def media_socket(socket: WebSocket, ticket: str):
            await media.websocket(socket, ticket)

    return router
