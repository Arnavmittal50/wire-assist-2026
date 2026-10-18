# API and WebSocket contracts

Runtime REST schemas are in `backend/app/schemas/contracts.py`; the frontend mirror
is `frontend/src/types/api.ts`. FastAPI exposes OpenAPI at `/docs`.

Every REST route except `/health` requires `Authorization: Bearer <DEV_AUTH_TOKEN>`.
The local proof of concept has one development teller identity. A UUID alone does not authorize a session.

| Method | Path | Result |
| --- | --- | --- |
| GET | `/health` | Configured provider names and model IDs; configuration is not provider verification |
| GET | `/members` | Generic synthetic member IDs and data provenance |
| GET | `/members/{id}` | Selected member metadata |
| GET | `/members/{id}/events?as_of=<ISO8601>` | Normalized history at or before a timezone-aware cutoff |
| POST | `/sessions` | Consented session with member, payment, replay time, mode and retention choice |
| GET | `/sessions` | Persisted sessions, newest first |
| GET | `/sessions/{id}` | Complete current session snapshot |
| POST | `/sessions/{id}/transcript` | Ingest a finalized segment; return current snapshot |
| GET | `/sessions/{id}/insights` | Validated observations and employee review status |
| POST | `/sessions/{id}/audio` | Pause/stop server processing; start/resume require the audio WebSocket |
| POST | `/sessions/{id}/actions` | Record a simulated employee action or review decision |
| POST | `/sessions/{id}/end` | Finish pending processing, end and return session |
| GET | `/sessions/{id}/summary` | Structured review summary |
| DELETE | `/sessions/{id}` | Stop processing and delete session data |

Session creation:

```json
{"member_id":"M1002","as_of":"2026-09-27T02:25:00Z","consent":true,"retain_transcript":false,"mode":"demo","payment":{"amount":1500,"rail":"Wire","recipient_id":"R1002-09","purpose":""}}
```

Transcript ingestion (manual text and provider finals share the same ingestion path):

```json
{"segment_id":"client-generated-uuid","sequence":1,"text":"The recipient asked me to send this today.","speaker":"member"}
```

`segment_id` is the idempotency key. Reusing it with identical content is a no-op;
changed content or a conflicting sequence returns 409. Out-of-order unique
sequences are sorted before context assembly. Provisional text is never accepted
by this endpoint. Speakers are `member`, `teller`, or `unknown`.

Employee actions use `action_type`, optional `insight_id` or `signal_id`, and `note`.
Acknowledgment/dismissal refer to one insight. Resolution/dismissal of a signal
requires its ID. Reasons are required for dismissals, resolutions and a legitimate
review. No endpoint executes a payment.

## WebSocket

Connect to `/ws/sessions/{session_id}` with an allowed browser Origin. Within five
seconds send `{"type":"authenticate","token":"<development token>"}`. The token
is not carried in the URL. An authorized connection receives status and a full snapshot.

Every server message has this envelope:

```json
{"type":"transcript_final","session_id":"uuid","event_id":"uuid","timestamp":"2026-10-17T12:00:00Z","payload":{"session":{},"segment":{}}}
```

| Type | Payload |
| --- | --- |
| `connection_status` | `status: connected` |
| `session_status` | `session`: complete current snapshot |
| `transcript_partial` | `text`, manually assigned `speaker`; ephemeral preview |
| `transcript_final` | `segment` plus `session` |
| `analysis_started` | `analysis_version` |
| `insight_updated` | `session` including provider label, validated insights and attention |
| `attention_updated` | `session` |
| `error` | Safe `message`, optional machine `code`; no credentials/provider stack traces |

Client JSON messages:

- `{"type":"audio_control","command":"start","speaker":"member"}` (also pause/resume/stop).
- `{"type":"transcript_submit",...finalized segment fields}` for the same path as REST.
- `{"type":"ping"}` for connection status.

After live audio start succeeds, send binary PCM16 little-endian mono at 16 kHz
(normally 3,200 bytes per 100 ms). WAV headers and WebM/MediaRecorder blobs are
not accepted. The backend translates bytes to the documented Gemini SDK protocol.
Only one microphone provider stream can own a session at a time.

The client ignores repeated `event_id`s and older `session.revision`s. On reconnect
it fetches a current REST snapshot. It does not replay raw audio or silently restart
the microphone. Backend analysis uses cancellation plus a version guard; the guard
still protects state if a remote request cannot be cancelled. Full snapshots are
intentionally simple for this bounded, single-process proof of concept.

## Two-stage analysis

`POST /sessions` schedules the initial history review immediately after consent and returns without waiting for Google. `analysis_pending` covers baseline preparation and sentence updates. `initial_analysis` is null until ready, then contains `summary` (text plus exact event evidence), `insights` (initial hypotheses), `provider`, `model`, `notice`, and `event_count`. It is included in session snapshots and the end-summary API. `insight_updated` broadcasts the completed baseline and subsequent per-sentence results.

`analyzed_sentences` stores completed segment-ID/sentence-index pairs for retry and restart continuity. Finalized member/unknown turns are split on sentence punctuation; teller questions and interim transcripts do not trigger follow-up calls. The initial request uses `GEMINI_ANALYSIS_MODEL`; updates use `GEMINI_UPDATE_MODEL`. `/health` exposes both as `analysis_model` and `update_model`, without credentials. Initial history context excludes freeform purpose and all conversation, so it remains available when transcript retention is off.
