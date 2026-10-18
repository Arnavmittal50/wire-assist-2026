# Incremental validation

The application is implemented in five phases. Results below record completed gates,
not intended future results. Provider transport doubles do not establish live account access.

## Phase 1 — frontend, API contracts and dataset

- Backend: **18 passed** (`python -m pytest -q`).
- Frontend: **5 passed** (`npm.cmd test`); TypeScript + Vite production build passed.
- Imported 7,471 supplied events; tested timezone-aware cutoff and answer-key exclusion.
- Installed Google Gen AI SDK 2.31.0 and verified live interim-transcript schema support.

## Phase 2 — microphone and transcription

- Backend: **36 passed**; fake Gemini transports cover PCM validation, multiple turns,
  provisional/final transcripts, failures, bounded finalization and cleanup.
- Frontend: **12 passed**; production build passed. Microphone lifecycle tests use browser API doubles.
- Fixed oversized parameterized test names that exceeded Windows temporary-path limits.
- No configured Gemini key; no real speech-to-text request was made.

## Phase 3 — structured analysis and attention

- Backend: **96 passed**; frontend: **19 passed**; production build passed.
- Exact citations, member/date bounds, invalid credentials, provider errors, structured JSON,
  repeated signals, stale responses and both demo scripts are covered.
- Fixed explicit negation false positives. Model signal labels now need corroborating
  cited-text rules; model prose remains a hypothesis requiring human review.

## Phase 4 — session lifecycle and persistence

- Backend: **133 passed**; frontend: **24 passed**; production build passed.
- Full false-positive review, simulated actions, end/summary/deletion, retained and
  nonretained restarts, durable transcript receipts and SQLite byte-level redaction tested.
- Fixed concurrent audio stop/end finalization and cleanup bookkeeping races.
- No real-money, external case-management or contact action exists.

## Phase 5 — end-to-end validation and documentation

- Final backend suite: **168 passed** (Python 3.14.3, `google-genai` 2.31.0).
- Final frontend suite: **29 passed**; TypeScript + Vite production build passed (Node 24.14.0).
- Real Chrome browser end-to-end suite: **2 passed**, against independently running
  FastAPI and Vite servers. No browser console/page errors; desktop and mobile screenshots reviewed.
- Covered manual conversation through actual HTTP/WebSocket services and SQLite,
  member/history selection, consent, microphone start/pause/resume/stop, evidence guidance,
  acknowledgments/dismissals, simulated teller decisions, legitimate review, summary,
  reload and deletion. All browser-test-created sessions were deleted.
- Browser microphone testing used Chrome's synthetic audio device, not a physical microphone.
- Installed SDK transport tests verify actual JSON-schema serialization, PCM/EOF encoding,
  and interim/final parsing in process, without external requests or real credentials.
- Fixed non-ASCII authorization rejection, source labels when no analysis ran, a cancelled
  concurrent-end reservation race, historical payment status selection, PCM tail flushing,
  and Pacific-date presentation around UTC midnight.
- Two upstream dependency deprecation warnings remain (Starlette HTTPX test-client support
  and the SDK's Python typing alias). No failed tests.

At the original phase-5 validation, no real Gemini response or cloud transcription was verified: no `GEMINI_API_KEY` was
configured. Model access, quota, billing/free-tier eligibility, physical-microphone input,
and actual recognition quality require a credentialed test in the user's environment.
All employee financial/contact actions remain explicitly simulated.

Sources, setup, troubleshooting, limitations, API contracts and exact backend dependency
versions are documented in the root README, `docs/api.md`, and `backend/requirements.lock.txt`.

## Two-stage Gemini analysis update ? October 17, 2026

- Initial consented session review uses `gemini-3.5-flash-lite` with the member's complete eligible observable history, producing cited summary facts and potential issues.
- Finalized member speech triggers `gemini-3.1-flash-lite` with all eligible history and the complete conversation. Pending sentences are coalesced into one Live assessment. Teller and unknown turns provide context only; interim text never triggers analysis.
- The initial review is shared across concurrent updates, persisted separately, and displayed with its provider/model label and evidence. Late results cannot undo employee decisions.
- Regression validation: **178 backend tests, 31 frontend tests, and 2 browser workflow tests passed**; production build passed. Browser workflows used Demo mode with synthetic microphone input and no provider requests.
- Credentialed checks: exactly one small structured-output request to each default analysis model succeeded (`{"ok": true}`); automatic retries were disabled. The key was saved only in ignored `backend/.env` and was not printed.
- These two real responses verify basic model access and structured JSON. Full live financial analysis, cloud audio transcription, physical-microphone recognition quality, billing, and free-tier eligibility were not verified by these tiny calls. Adapter and workflow tests remain explicitly offline/demo.
- Re-run the small access check with `.\.venv\Scripts\python.exe backend/scripts/check_models.py` from the project root.


## Hybrid audio and responsive guidance ? October 17, 2026

- Full backend suite: 245 passed before the final background-status and ambiguous-opening-voice regressions; affected suites were rerun afterward.
- Frontend: 48 tests passed; production TypeScript/Vite build passed.
- Browser: `review-clarity.spec.ts` passed, including a speaker correction withdrawing an incorrect timing signal and mobile layout checks.
- Installed google-genai SDK HTTP serialization verified with an in-process transport for actual WAV payloads, verbatim mode, diarization, word timestamps, and `store=False`.
- Real Gemini synthetic dialogue checks passed for the family-transfer example (identity verification, large amount, possible impersonation; no urgency/secrecy/investment), explicit timing, paraphrased timing, and later clarification withdrawing urgency. Successful update calls took 6.9?12.2 seconds in the final run. Earlier attempts encountered transient HTTP 503s; successful checks do not imply guaranteed availability.
- A real local API session, including concurrent initial history, returned TIME_PRESSURE guidance in 18.1 seconds after a confirmed synthetic member turn. The temporary test session was deleted.
- Real batch transcription accepted one second of generated silence in 4.0 seconds. This verifies model access and audio encoding only. No consented two-speaker speech recording was available, so real speaker-attribution accuracy remains unverified.
- Live audio guidance additionally waits for the first 12-second batch and provider identification. Ambiguous voices remain unknown and require correction; silence across batch boundaries and recording restarts can break identity continuity.
