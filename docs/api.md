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
by this endpoint. Speakers are `member`, `teller`, `unknown`, or `auto`. The UI defaults to `auto`; explicit roles remain authoritative. Auto anchors the first speaker as teller and uses the history model to assign roles in one full-conversation batch, leaving ambiguous or mixed speech unknown. Segments include `speaker_source` (`automatic` or `manual`); automatic labels can be reassessed as context arrives. `speaker_notice` reports incomplete assignment without hiding transcript text. Analysis updates publish resolved roles in the session snapshot.

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

`POST /sessions` schedules the initial history review immediately after consent and returns without waiting for Google. `analysis_pending` covers baseline preparation and sentence updates. `initial_analysis` is null until ready, then contains `summary` (text plus exact event evidence), `insights` (initial hypotheses), `provider`, `model`, `notice`, and `event_count`. It is included in session snapshots and the end-summary API. `insight_updated` broadcasts the completed baseline and subsequent conversation assessments.

`analyzed_sentences` stores completed segment-ID/sentence-index pairs for retry and restart continuity. Finalized member turns are tracked by sentence; Live mode coalesces all pending sentences into one full-context assessment, while Demo mode keeps sequential sentence updates. Teller questions and interim transcripts alone do not trigger new follow-up calls. Failed updates remain eligible for retry. Failed history reviews pause before retry; HTTP 429 pauses analysis requests to the affected model for `PROVIDER_RETRY_COOLDOWN_SECONDS` (default 60). The initial request uses `GEMINI_ANALYSIS_MODEL`; updates use `GEMINI_UPDATE_MODEL`. `/health` exposes both as `analysis_model` and `update_model`, without credentials. Initial history context excludes freeform purpose and all conversation, so it remains available when transcript retention is off.

## Signal observations

`Insight.signals` contains up to seven typed observations. Each carries `signal_type`, epistemic `status` (`OBSERVED`, `POSSIBLE`, `NOT_ESTABLISHED`), `source_type`, `source_id`, `evidence_quote`, `explanation`, and `suggested_question`. The quote must also appear in the parent evidence list. The backend derives `signal_codes` for observed evidence; possible scenarios never score. An insight may have lifecycle status `superseded` after later evidence contradicts it, distinct from employee `resolved` or `dismissed` decisions. These new quote/explanation fields are redacted when transcript retention is disabled.

## Live contextual interpretation

Every live assessment receives all eligible member events and the full finalized conversation with speaker labels. The member-only citation map prevents teller questions and unknown voices becoming risk evidence. A second Gemini pass reviews the proposed assessment against original source context and returns the current supported findings. Backend citation validation and deterministic score weights are separate from that semantic decision; live classification does not call the demo keyword rules.

`InitialAnalysis.provider` and `Session.analysis_sources` may include `unavailable`. Live API, timeout, or contract failures return empty classifications with a safe status notice, without substituting demo output. Initial unavailable reviews can be retried by a later conversation update. Each successful live result supersedes obsolete model findings; failed requests preserve the last successful assessment while employee dismissals/resolutions persist. JSON Schema constraints unsupported by the Gemini Schema endpoint are omitted only from the provider schema; strict local contracts still validate the model response.


`POST /sessions/{session_id}/transcript/{segment_id}/speaker` accepts `{"speaker":"teller"}` (also `member` or `unknown`). It requires the same authentication and an active session. The correction locks that turn to a manual label, supersedes conversation evidence from outdated attribution, and schedules a fresh assessment. Original transcript receipt IDs and text remain unchanged.

Conversation-tier timeouts and HTTP 500/502/503/504 failures can recover using `GEMINI_ANALYSIS_MODEL`. Recovery is bounded to the two configured tiers, keeps the same full context and evidence-review requirements, and identifies the responding model in `provider_notice`. The failed update tier is paused for the configured cooldown.


## Hybrid audio attribution and guidance

Finalized live segments have stable `segment_id`, `session_id`, `recording_id`, `start_time`, `end_time`, `timing_source`, `speaker_id`, `speaker_role`, `transcription_source`, `diarization_status`, and `revision` fields. Roles are `teller`, `member`, or `unknown`; diarization statuses are `PENDING`, `CONFIRMED`, `UNCERTAIN`, or `FAILED`. The compatibility `speaker` field mirrors the current role. `speaker_source=diarization` denotes audio-derived identity and `manual` denotes a turn override. Only confirmed member turns with either source are eligible evidence.

The WebSocket sends `transcript_speaker_updated` with a full session snapshot and changed `segment_ids` for batch updates. `diarization_status` and `diarization_error` carry independent status snapshots; these errors do not stop the microphone. Apply session revisions and segment revisions to the existing bubbles, rather than appending batch transcripts. Session fields include `speaker_roles`, `diarization_pending`, `diarization_notice`, and `guidance_status` (`idle`, `waiting_for_speaker`, `reviewing`, `ready`, `unavailable`).

`POST /sessions/{session_id}/transcript/{segment_id}/speaker` accepts `{ "speaker": "teller", "scope": "speaker" }` to map the identified voice across its matched turns. `scope=segment` corrects only that turn and remains authoritative over automatic updates. Mapping a voice without an identified speaker ID returns 409. Changed member attribution invalidates affected findings and scoring before a new evidence review.

`POST /sessions/{session_id}/guidance/retry` starts an asynchronous review of eligible unreviewed turns after a failed response, returns the current Session, and sends results through the normal socket. It coalesces with an already-running request, respects provider cooldowns, requires authorization and an active session, and does not append transcript text or override a recorded legitimate decision.

Live analysis may cite `source_type=event`, `source_id=proposed_payment` for the entered payment proposal. This source is explicitly supplied with its exact serialized fields; it is not a completed historical transaction and does not change the eligible-history count. Typed signal citations are validated against their original source and included in the returned evidence, even when the model omitted a duplicate insight-level citation.
