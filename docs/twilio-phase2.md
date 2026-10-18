# Phase 2: consented call transcription and Groq guidance

Phase 1's browser-to-phone call, configured destination, mute, timer, and hang-up controls are preserved. Check **Enable AI call assistance** to attach a live review to the call. Leave it unchecked for calling without audio processing. No automated test places a phone call.

## What runs where

- **Groq** performs pre-call synthetic-history review and live analysis. Gemini does not perform application risk analysis.
- **Gemini `gemini-3.5-transcribe-live`** transcribes two independent PCM connections. The existing Live adapter is reused; no extra speaker diarization runs on identified call tracks.
- **FastAPI** validates source evidence, manages consent and queues, and calculates deterministic attention scores. Possible impersonation is a hypothesis, not confirmation, and does not score.
- **React** shows pre-call context automatically on member selection, chronologically ordered transcript turns, one current recommendation, an attention indicator, and collapsed previous guidance. Refresh Analysis, acknowledge, and dismiss remain available.

## Consent and track mapping

1. The teller enables AI call assistance and confirms in the dashboard that both participants consent to transcription and analysis. The authenticated `/api/twilio/prepare` endpoint requires `participants_consent: true` and returns an expiring session ticket.
2. The signed `/twilio/voice` webhook binds that ticket to the browser parent CallSid. The existing `<Dial><Number>` connects the configured phone directly. There is no application-generated phone announcement, keypad prompt, or private notice URL.
3. When the signed member-leg callback reports `in-progress`, the backend starts the parent's unidirectional `both_tracks` stream through the [Streams REST API](https://www.twilio.com/docs/voice/api/stream-resource). Duplicate answer callbacks cannot create duplicate streams.

Media endpoint: **`wss://<PUBLIC_BACKEND_HOST>/twilio/media/{ticket}`**. The original external WSS handshake URL is signature-validated against the configured public origin, including the documented optional trailing-slash form. HTTP webhook signatures continue to use HTTPS. Host/forwarded headers are not trusted. The start message must match the configured account, parent call, stream, both tracks, and 8 kHz mono mu-law format. Only one stream connection is accepted per ticket.

| Track on this parent leg | Role | Reason |
| --- | --- | --- |
| `inbound` | Teller | Browser microphone audio entering Twilio on the browser leg |
| `outbound` | Member | Bridged phone audio sent by Twilio back to the browser |

The mapping is verified against the application's parent/child call topology, official track definitions, signed metadata, and automated fixtures with distinct audio on each track. **A real two-person call must still verify the acoustic mapping.** Unknown/mismatched metadata is rejected with a nonblocking error; no words or speaker roles are guessed. Non-sensitive parent, child, and stream IDs are logged for diagnosis. Raw audio and secrets are not logged.

Each track has its own reorder buffer, mu-law decoder, stateful 8-to-16 kHz linear interpolator, bounded queue, Gemini connection, and timestamps. Duplicates are ignored. A 100 ms reorder window tolerates small reordering; missing intervals become silence, while gaps beyond five seconds stop transcription rather than shift words onto incorrect timestamps. Queue overflow stops the affected transcription path, leaving the phone call intact.

The [Gemini Live transcription API](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe) supports incremental raw PCM. The implementation sends 100 ms PCM chunks, with a hybrid end-of-turn hint after 500 ms of silence. It does **not** use 3–5 second batch requests. Finalized text is committed with session/segment/call/stream IDs, role, start/end times, source, final flag and revision. Timing is marked estimated when the provider supplies no offsets. The existing Live session's approximately ten-minute limit remains; a failure is visible without ending the phone call.

At hang-up, bounded draining finalizes already authorized audio, then connections, tasks, queues and buffers close. The session summary uses finalized content and honors transcript retention. Unretained conversation text is removed under the existing retention policy. Provider failure never generates fake transcripts or findings.

## Groq model selection and real API checks

Selected configuration:

```dotenv
GROQ_MODEL=openai/gpt-oss-20b
GROQ_PRECALL_MODEL=openai/gpt-oss-120b
```

Both are production models with [strict structured JSON support](https://console.groq.com/docs/structured-outputs). Groq's [model catalog](https://console.groq.com/docs/models) lists approximately 1,000 tokens/second for 20B and 500 for 120B. These are provider throughput figures, not measured end-to-end latency.

Small synthetic benchmarks used the application's real schema and validators, including the million-dollar family example, explicit 30-minute threat, teller questions/denial, and M1002's eligible history. Development runs exposed invalid questions and reconstructed JSON citations. The implementation now allows one bounded repair request; a reordered record excerpt is accepted only when every typed field/value exactly matches the actual record. Speech quotes are never reconstructed. Invalid or unsupported results remain suppressed.

| Model | Results after validation fixes | Measured request time |
| --- | --- | --- |
| GPT-OSS 20B | Passed the three live regression cases and history review; selected for live guidance | About 0.8–1.4 s provider time; 1.5–3.6 s including connection and one repair in the tested live cases |
| GPT-OSS 120B | Returned three validated M1002 history facts; one live urgency case still failed citation validation after repair, so it is **not** the live fallback | About 2.5 s for the successful history response |

A separate stricter 20B check confirmed that in-person recipient verification clears the identity concern, urgency, secrecy and impersonation hypotheses. These are small regression checks, not a comprehensive model-quality guarantee. The failed 120B live case is retained in benchmark reporting rather than counted as success.

The account's response headers showed **8,000 tokens/minute and 1,000 requests/day** for each candidate. The request-limit header is a daily limit, not requests/minute. Groq's [rate-limit documentation](https://console.groq.com/docs/rate-limits) lists 30 requests/minute for the base free tier and explains that organization limits can differ. Larger advertised paid-plan limits are not assumed for this account.

One history test took **58.7 seconds total** after benchmark traffic depleted the token budget, despite a 2.5-second provider response. The application uses separate model pacing, a bounded rolling conversation/history context, a two-minute pre-call cache, debounce/coalescing, response-header pacing and `Retry-After` cooldown. These reduce wasted calls but cannot eliminate account quota delays. Repeated member selection or rapid Refresh clicks do not create unlimited provider concurrency. No account upgrades or alternative accounts were created.

Reproduce the synthetic Groq comparison:

```powershell
.\.venv\Scripts\python.exe backend/scripts/check_groq_models.py
```

The script prints secret-free timing, signals, validation outcomes and limit headers, and writes reports under ignored `.tooling/`. It performs real API requests; the ordinary automated suite mocks providers.

Two locally synthesized 8 kHz utterances were also mu-law encoded, decoded/resampled, and sent through **two real Gemini Live connections**. Both sentences transcribed correctly with no finalization errors. This verifies the provider/audio path, not Twilio delivery or real-call speaker mapping. Test WAVs and results remain in ignored `.tooling/`, not the repository. `backend/scripts/check_twilio_transcription.py` can rerun this check when those synthetic WAVs exist.

A final `--repeat` continuity check produced two correct finalized utterances on each independent connection with no errors after end-of-turn hints. Those real responses supplied no time offsets, so the app explicitly marks timing as estimated from Twilio media timestamps and speech activity rather than claiming provider word timestamps.

## Configuration and startup

Keep the existing Twilio credentials, Application SID, phone numbers, `PUBLIC_BACKEND_URL`, `GEMINI_API_KEY`, and development authentication. Add only backend settings:

```dotenv
GROQ_API_KEY=
GROQ_MODEL=openai/gpt-oss-20b
GROQ_PRECALL_MODEL=openai/gpt-oss-120b
GROQ_MIN_INTERVAL_SECONDS=8
GROQ_DEBOUNCE_SECONDS=0.7
TWILIO_AUDIO_QUEUE_FRAMES=250
```

The selected models and pacing settings have been saved locally. `.env` remains ignored by Git. Install dependencies with `.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt`. No new frontend package is needed.

Stop the existing backend with Ctrl+C in its owning terminal, then run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

Keep the existing tunnel running, or start one in another terminal:

```powershell
ngrok http http://127.0.0.1:8000
```

The public origin must match `.env` and the TwiML application's `/twilio/voice` POST URL. Restart FastAPI when `.env` changes. Use one backend worker because call/session coordination is process-local.

Frontend, in a separate terminal:

```powershell
cd frontend
npm.cmd run dev
```

Open `http://127.0.0.1:5173` and hard-refresh. Do not run the separate browser transcription microphone while calling.

## Automated and manual verification

Verification completed: the full backend suite passed **287 tests**; subsequent focused runs passed **48** audio/Groq/transcription/staging checks and **22** guidance/staging checks after the final acknowledgement fix. The frontend passed **59 tests**, the production build succeeded, and **3 browser smoke tests** passed, including Phase 2 preparation with intercepted Twilio tokens. Secret scans found no configured credentials in repository files. Real phone acceptance remains untested.

Automated coverage includes unchanged Phase 1 calling, consent gating, signatures, parent/child/stream verification, track routing, mu-law vectors, resampling, packet ordering/gaps/duplicates, independent provider pipelines, transcript ordering, evidence validation, deterministic scoring, provider failures, and end cleanup. Existing application events (`transcript_final`, `analysis_started`, `insight_updated`, `attention_updated`, `session_status`) are retained; call processing adds `transcription_status`, `transcription_error`, and `call_ended`. Every session update retains revision checks.

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests -q --basetemp .tooling/pytest-phase2-manual
cd frontend
npm.cmd test -- --run
npm.cmd run build
```

Manual acceptance, still required:

1. Start/restart FastAPI, ngrok and the frontend. Confirm the tunnel URL matches configuration.
2. Select **M1002**. Wait for Groq pre-call context; a provider error must not disable calling.
3. Check **Enable AI call assistance** to confirm both participants consent. Optionally enable transcript retention for this test using only synthetic dialogue and consenting participants.
4. Click **Call Member**, allow microphone access and answer the configured phone.
5. The call connects directly, with no app announcement or keypad step. Audio processing starts automatically after answer when dashboard consent was confirmed.
6. Teller says “What is the purpose of your wire transfer today?” Verify a **Teller** transcript.
7. Member says “My brother asked me to send money.” Verify a separate **Member** transcript.
8. Briefly overlap speech and verify the tracks remain distinct and transcript timing is chronological.
9. Use the million-dollar/familiar-voice/selfie example. Verify an identity-verification concern without urgency, secrecy or investment claims.
10. Have the member state the explicit 30-minute threat. Verify TIME_PRESSURE and a relevant teller question. Allow for the displayed account quota limits.
11. Clarify that the member independently verified the recipient and is under no deadline; verify the assessment is revised. Try Refresh Analysis, acknowledge and dismiss.
12. Test mute/unmute and hang-up from each participant. Confirm processing stops, the summary appears and retention is honored.
13. Try calling with AI assistance unchecked, and with a deliberately unavailable AI provider: phone controls must remain functional.

Actual Twilio stream availability on this account, signed public WSS delivery and real acoustic role mapping cannot be certified by mocked tests. No real call was made automatically. Stop after Phase 2 testing before adding further features.

## Changed files

- Backend integration: `backend/app/main.py`, `app/core/config.py`, `app/schemas/contracts.py`, `app/services/twilio_calling.py`, new `app/services/twilio_media.py`, new `app/services/groq_analysis.py`, `app/services/analysis.py`, `app/services/sessions.py`, `app/services/transcription.py` (paths after the first are relative to `backend/`).
- Backend setup/tests: `backend/.env.example`, `backend/requirements.txt`, `backend/scripts/check_groq_models.py`, `backend/scripts/check_twilio_transcription.py`, `backend/tests/test_groq_analysis.py`, `backend/tests/test_twilio_media.py`, `backend/tests/test_transcription.py`, `backend/tests/test_phase1.py`. Local ignored `.env` gained model/pacing settings.
- Frontend: `frontend/src/App.tsx`, `App.test.tsx`, `components/CallControls.tsx`, `components/InitialReview.tsx`, `components/InitialReview.test.tsx`, `components/InsightCard.tsx`, `components/ProviderNotice.tsx`, `components/SignalClassification.test.tsx`, `components/Transcript.tsx`, `hooks/useTwilioCall.ts`, `hooks/useTwilioCall.test.tsx`, `services/api.ts`, `types/api.ts`, `styles.css` (all after the first relative to `frontend/src/`), plus `frontend/e2e/twilio-phase2.spec.ts`.
- Documentation: `README.md` and this guide. Generated audio, benchmark reports, test databases and logs remain under ignored `.tooling/`.


## Real-call transcription fix

A real test produced an HTTP 403 on the media WebSocket. Comparing its captured signature locally proved that Twilio signed the `wss://` URL; the old validator incorrectly used `https://`. Validation now uses the configured WSS origin, exact path/query and the documented trailing-slash variant. Signature checks remain mandatory. See [Twilio's security notes](https://www.twilio.com/docs/usage/security). The phone notice and keypad endpoints were removed; consent is confirmed once in the dashboard and streaming starts on the signed answer callback.
