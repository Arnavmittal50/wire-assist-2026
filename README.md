# Wire Assist 2.0

A local teller copilot for consented, synthetic conversations. React/TypeScript/Vite/Tailwind frontend, Python/FastAPI backend, authenticated WebSockets, SQLite, and Google Gemini through `google-genai`. The teller controls every action; payment actions are simulations.

## Start locally

Requires Python 3.12+ and Node.js 22.12+ (Node 24 recommended). From this directory on Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
Copy-Item backend\.env.example backend\.env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open the Vite URL (normally http://127.0.0.1:5173). FastAPI's interactive API contract is at http://127.0.0.1:8000/docs. Use `npm.cmd` on Windows when PowerShell blocks `npm.ps1`; changing system execution policy is unnecessary. On macOS/Linux use `.venv/bin/python` and `npm` in the corresponding commands. Both servers run independently; Docker is unnecessary.

For the exact backend dependency versions used during verification, install `backend/requirements.lock.txt` instead of `backend/requirements.txt`. That lock records the Windows/Python 3.14 verification environment. Frontend `npm.cmd ci` uses its committed package lock.

Leave `GEMINI_API_KEY` empty for the complete manual-text demo. The development access token defaults to `wire-assist-local-dev`; this is local access control, not a production identity system. Configure the backend's `DEV_AUTH_TOKEN` and the frontend's access setting together if you change it. Never put the Gemini key in `frontend/.env` or a `VITE_*` variable.

## Data and replay

The supplied CSV copies are under `data/`; see [provenance and limitations](data/README.md). Runtime imports only observable event columns. The answer key is excluded from the API, analysis, and scoring. The default replay cutoff is September 26, 2026, 19:25 Pacific (`2026-09-27T02:25:00Z`). It includes M1002's first $1,500 wire request and excludes later events. P2P and wire recipient identifiers differ; their relationship is unknown until independently discussed.

## Gemini configuration

Set these backend-only values in `backend/.env`, then restart FastAPI:

```dotenv
GEMINI_API_KEY=your_key_here
GEMINI_ANALYSIS_MODEL=gemini-3.8-flash
GEMINI_UPDATE_MODEL=gemini-3.5-flash-lite
GEMINI_TRANSCRIPTION_MODEL=gemini-3.5-transcribe-live
```

Model selection was checked against Google's documentation on October 17, 2026. Both analysis models support structured JSON; the separate transcription model supports the Live API. Availability, quota, billing, and free-tier eligibility must be verified for your Google project. Merely configuring a key does not verify access.

- [Gemini 3.5 Flash-Lite capabilities](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
- [Gemini 3.8 Flash capabilities](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
- [Structured JSON through Generate Content](https://ai.google.dev/gemini-api/docs/generate-content/structured-output)
- [Live transcription protocol and PCM input](https://ai.google.dev/gemini-api/docs/live-api/live-transcribe)
- [Google Gen AI Python SDK](https://googleapis.github.io/python-genai/)
- [Google pricing and access tiers](https://ai.google.dev/gemini-api/docs/pricing)

A minimal credential/model check makes one small structured-JSON request per configured analysis model (no financial data, retries disabled):

```powershell
.\.venv\Scripts\python.exe backend/scripts/check_models.py
```

Both default models returned valid JSON during the October 17 credentialed check. That check verifies basic Generate Content access, not cloud transcription, full application recommendation quality, or free-tier eligibility.

## Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
npm.cmd test
npm.cmd run build
```

See [phase validation](docs/phase-validation.md) for recorded results. Tests with fake SDK transports verify adapter behavior; they do not verify live Gemini account access.

With both servers running, execute the actual browser workflow tests from `frontend`:

```powershell
npm.cmd run test:e2e
```

Playwright uses installed Chrome by default; set `$env:PLAYWRIGHT_CHANNEL='msedge'` for installed Microsoft Edge. The tests use a browser-generated microphone signal, exercise the real local HTTP/WebSocket services and SQLite, and delete their sessions afterward. They do not call Gemini. Screenshots are written to `frontend/test-results/dashboard.png` and `frontend/test-results/mobile.png`. Configure `E2E_FRONTEND_URL` and `E2E_BACKEND_URL` if using other ports, plus the normal CORS/API settings. `E2E_DEV_AUTH_TOKEN` overrides the test access token.

## Audio and live updates

Recording begins only after explicit session consent and a microphone button click. Browser audio is converted to signed 16-bit little-endian mono PCM at 16 kHz and sent in short packets to the authorized backend WebSocket. The backend authenticates to Google using its own key. No provider key or provider connection is exposed to the browser. Interim text is a preview; only finalized segments enter analysis. Select the speaker manually; the app does not claim automatic diarization.

Pause and Stop release microphone tracks. Resume acquires a fresh browser stream and provider connection. Disconnects stop capture; reconnection refreshes session state but does not automatically resume the microphone. Audio is neither saved nor logged by the application. Provider-side data practices depend on the configured Google account and tier.

Each live recording is capped at 590 seconds, within the documented ten-minute provider session limit. Stop signals the end of audio and waits up to five seconds for finalization; if the provider cannot confirm final text, the UI asks the teller to check and manually enter missing words. Restarting creates a new provider stream.

The no-key demo can exercise browser microphone capture locally, clearly marked as a preview with no transcription. Type the spoken words in the manual entry box to complete the workflow. No fabricated transcript is generated from microphone noise.

## Analysis and attention

At session start, Gemini 3.8 Flash receives the selected member's **entire observable history up to the replay cutoff** and structured payment details. It returns cited summary facts and initial potential issues. The baseline excludes conversation, freeform payment purpose, other members, future events, demographics, and hidden answer-key labels. The interface shows the baseline separately with its model/provider label, evidence, and event count. This history-only baseline is saved in SQLite even when conversation retention is off.

Each finalized member or unassigned-speaker turn is split into sentences; Gemini 3.5 Flash-Lite analyzes each new sentence with the saved baseline, its cited source events, up to 12 recent finalized segments, and recent observations. Earlier completed sentences are not reprocessed. Sentence boundaries use punctuation with basic decimal/abbreviation handling; a final unpunctuated fragment also counts. This is not linguistic sentence detection. Audio must first be finalized by the transcription provider; interim words never trigger analysis. A manual Add turn follows the same path.

New input can cancel an in-flight update; the replacement processes all unfinished sentences. A cancelled remote request may still incur usage. The initial Flash request is shared and is not restarted when new speech arrives. Teller decisions retain priority over late model responses. Ending a review has a bounded wait; unfinished analysis is explicitly reported. With no key or on a provider error, the same pipeline uses clearly labeled deterministic demo output. Select **Live** when starting a review to use Gemini; Demo mode never calls Google even if a key is configured.

Gemini receives only selected-member evidence. Teller questions cannot become member evidence. Structured Pydantic validation checks enums, field sizes and confidence bounds. Source IDs and exact quotes must match the supplied context; unsupported citations are suppressed. The backend owns insight IDs, provider labels, review status and required human review. Conversation text is treated as untrusted data, never as an instruction to execute an action.

Attention is an illustrative sum of explicit rules, capped at 100: routine below 30, review at 30–59, elevated at 60+. It is **not a calibrated fraud probability**. Rules and weights are in `backend/app/services/risk_engine.py`. Age, disability, the member answer key and model confidence do not affect points. Distinct rule codes count once, and employee dismissals/resolutions survive repeated observations. A future event cannot enter the prescreen or model context.

At the default M1002 cutoff, history alone produces 45 points: first recorded wire (15), new recipient (10), and growing P2P payments (20). The observed wire's baseline ratio does not justify an unusually-large-payment signal. P2P growth does not establish that the wire payee is the same person.

Exact citation validation does not prove a model's interpretation is correct. The teller must review any inference. Deterministic demo text matching has limited language coverage and can miss nuance; it is a transparent fallback, not a trained fraud detector. No accuracy or fraud-prevention claim is made.

The same conservative, explicit text-cue rules determine whether a model-extracted conversation signal is eligible for points. Unsupported labels are excluded from scoring while grounded questions can remain available for human review. This reduces unsupported score increases but can miss paraphrased or nuanced cues.

See [API and WebSocket contracts](docs/api.md) for message payloads, authentication, idempotency and ordering.

## Run the complete no-key demonstration

1. Start both servers, select **M1002**, and keep the default September 26 replay cutoff and $1,500 Wire request.
2. Confirm the processing consent checkbox. Choose Demo mode. Optionally enable transcript retention if you want the complete text available after ending/restarting.
3. Start the review. Open payment history and the attention explanation; the source history has four growing P2P initiations and the first recorded wire request.
4. Start the microphone to exercise the clearly labeled local preview. Pause, Resume and Stop release/reacquire the device. With no key, enter text manually; the preview does not transcribe.
5. Choose a demo example or enter: `Someone I met online said I must send the wire today to protect my investment, and asked me not to tell anyone.` Select Member and Add turn.
6. The WebSocket updates the finalized transcript, explicitly labeled demo guidance, evidence and attention. Acknowledge the suggestion, or dismiss it with a reason. Record a simulated verification, specialist, hold or escalation action as appropriate.
7. To demonstrate a false positive, enter an independently checked legitimate explanation, then record the teller's verification with **Mark legitimate**. A member's assertion alone does not clear signals; this employee decision does. Trusted-contact offers are voluntary and give no control over funds.
8. End the review and inspect the summary. Reload a saved session or delete it to exercise persistence and deletion. If retention was disabled, free-text content is removed and the summary retains structured counts, decision types, statuses and timestamps.

For a second source-data example, use **M1057**, cutoff `2026-10-19T16:41:00Z`, and the $51,586.52 Wire initiation **E03702**, copying its recipient identifier from history. The unusual payment triggers attention without reading the answer key. A teller can independently verify the closing instructions and record a legitimate outcome. These scripts are demonstration dialogue, not verbatim rows from the CSV.

For live testing, configure the backend Gemini key, select Live mode, and repeat the flow. Confirm that actual spoken words arrive as finalized segments and the response badge changes to Gemini. A provider error displays a notice; manual entry remains available, and any local analysis is labeled demo fallback. An adapter test passing does not establish a working account, microphone, quota, or model entitlement.

## Persistence and review decisions

SQLite defaults to `backend/wire_assist.sqlite3`. The repository boundary isolates storage from the API and can be replaced with a PostgreSQL repository. Run one FastAPI worker: active audio streams, analysis tasks and socket subscriptions are process-local. Multi-worker coordination requires an external message broker/session owner.

Transcript retention is off by default for each session and can be globally disabled with `ALLOW_TRANSCRIPT_RETENTION=false`. When off, active conversation is temporarily available in memory for review. Before saving, the repository removes transcript segments, conversation evidence quotes, model free text, freeform payment purpose, employee notes and activity prose. Ending also removes that text from memory. Counts, timestamps, rule codes/status, provider labels and action types remain. Retention-enabled sessions save the finalized text and notes; raw audio is never stored in either mode. A hashed receipt preserves duplicate detection even when the original text is not retained.

Delete removes the session and its receipts, closes streams and connections, and cancels pending analysis. SQLite secure deletion is enabled; this is not a guarantee about operating-system backups, swap, storage-device wear leveling, or provider-side retention.

Dismissals/resolutions apply to a rule code for that session. Repeating the observation does not reactivate it. Start a new review when circumstances require a fresh assessment. The employee's legitimate-member decision records the reason and resolves current signals; it never releases or transfers funds. Simulated review holds are restricted to Wire, ACH and External Transfer and describe a maximum four-hour request. P2P/Cash use voluntary review instead. The supplied M1057 historical hold duration is preserved as source data even though it exceeds that challenge limit.

## Configuration and troubleshooting

| Setting | Default / purpose |
| --- | --- |
| `GEMINI_API_KEY` | Empty; backend-only credential |
| `GEMINI_ANALYSIS_MODEL` | `gemini-3.8-flash` |
| `GEMINI_UPDATE_MODEL` | `gemini-3.5-flash-lite` |
| `GEMINI_TRANSCRIPTION_MODEL` | `gemini-3.5-transcribe-live` |
| `DEV_AUTH_TOKEN` | `wire-assist-local-dev`; replace before sharing access |
| `ALLOWED_ORIGINS` | localhost and 127.0.0.1 on port 5173 |
| `DATABASE_PATH`, `DATA_DIR` | Optional absolute storage/data paths |
| `ALLOW_TRANSCRIPT_RETENTION` | `true` permits opt-in; session default is still off |
| `PROVIDER_TIMEOUT_SECONDS` | 30 |
| `MAX_SEGMENTS`, `MAX_SESSIONS` | 300 turns per session; 100 stored sessions |
| Frontend `VITE_API_BASE_URL` | `http://localhost:8000` |
| Frontend `VITE_DEV_AUTH_TOKEN` | Optional local development access token; never a Gemini key |

- **Backend unavailable:** verify `/health` and the Vite API URL. The API returns safe notices and the UI preserves manual input on failed submission.
- **401 / WebSocket rejected:** match the development token in connection settings and backend `.env`. Restart the backend after environment changes. Use an allowed frontend origin.
- **Microphone denied or unavailable:** use localhost/HTTPS in a browser with MediaDevices and AudioWorklet support. Grant permission only for a consented demo; manual input works without permission. Speaker assignment is manual.
- **Key configured but provider fails:** check the Google project's model availability, region, quota and billing. The UI labels fallback responses. No free-tier entitlement is assumed.
- **Unsupported audio:** use the supplied PCM AudioWorklet. A MediaRecorder WebM blob is not the raw PCM format required by this adapter.
- **Disconnected socket:** the UI reconnects and refreshes a full snapshot. Recording stops and requires a new user click; audio is not replayed.
- **Text missing after ending/restarting:** expected when transcript retention was off. The remaining structured summary and receipt count are intentional.
- **CSV missing:** clearly labeled sample fixtures are used. A present malformed CSV fails startup validation; it is not silently replaced with fabricated data.
- **Port changed:** update the frontend API URL or backend CORS origins as needed. Use a single backend worker and the documented dev ports for the easiest setup.

## Boundaries and deployment work

This is a synthetic-data development proof of concept, not a production banking system or compliance certification. There is no authorized banking integration. The application cannot hold, release, stop, escalate a real case, contact another person, or transfer funds; recorded actions are local simulations.

Before real financial-data use, obtain institutional approval and legal/privacy review, validate consent and retention requirements, agree vendor data-processing terms, and evaluate model quality and security. Replace the development token with institutional identity and per-teller authorization, add TLS and managed secrets, encrypted storage/backups, audit integrity, security monitoring, stronger traffic limits and distributed coordination. Evaluate false positives across representative cases and define employee escalation procedures. Trusted-contact consent must be voluntary and revocable, with no authority over funds.

## Project map

- `frontend/src`: typed API client, session WebSocket hook, microphone hook/AudioWorklet, dashboard, reusable Radix/shadcn-style components and tests.
- `backend/app`: FastAPI routes, Pydantic contracts, Gemini analysis and transcription adapters, deterministic risk rules, member importer, session coordination and SQLite repository.
- `backend/tests`: provider contract doubles, scoring/data tests, session integration and lifecycle tests.
- `data`: exact supplied CSV copies and provenance; not served as frontend assets.
- `docs`: API contracts and phase validation results.
