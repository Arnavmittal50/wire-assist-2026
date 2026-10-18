import asyncio
import hashlib
import json
import time
from contextlib import suppress
from fastapi import HTTPException, WebSocket

from app.schemas.contracts import (Activity, ActionInput, ActionType, CreateSession, Envelope,
                                  Session, Segment, SessionSummary, TellerAction, TranscriptInput, now)
from app.models.repository import SessionRepository, redact_session
from app.services.analysis import AnalysisService, sentence_spans
from app.services.risk_engine import calculate_attention
from app.services.signal_validation import denied


class SessionService:
    """Single-process POC coordinator. All mutations run on the ASGI event loop."""

    def __init__(self, settings, members):
        self.settings = settings
        self.members = members
        self.repository = SessionRepository(settings.database_path)
        self.sessions: dict[str, Session] = {s.session_id: s for s in self.repository.load_all()}
        self.connections: dict[str, set[WebSocket]] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.initial_tasks: dict[str, asyncio.Task] = {}
        self.initial_retry_after: dict[str, float] = {}
        self.analysis = AnalysisService(settings)
        self.ending: set[str] = set()
        self.finish_tasks: dict[str, asyncio.Task] = {}

    def get(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Session not found.")
        return session

    def start_analysis(self, session: Session):
        session.analysis_pending = True
        session.provider_notice = "Preparing the initial member-history review."
        self.repository.save(session)
        self.tasks[session.session_id] = asyncio.create_task(self._analyze(session.model_copy(deep=True)))

    async def _initialize(self, session_id: str):
        session = self.get(session_id)
        history = self.members.events(session.member_id, session.as_of)
        baseline = await self.analysis.initialize(session.model_copy(deep=True), history)
        current = self.sessions.get(session_id)
        if current is not session or session.status != "active":
            return
        session.initial_analysis = baseline
        if baseline.provider == "unavailable":
            self.initial_retry_after[session_id] = time.monotonic() + self.settings.provider_retry_cooldown_seconds
        else:
            self.initial_retry_after.pop(session_id, None)
        if baseline.provider not in session.analysis_sources:
            session.analysis_sources.append(baseline.provider)
        # Initial hypotheses never undo a human decision made while the model was running.
        if not session.analyzed_sentences and session.review_outcome != "legitimate":
            self._merge_insights(session, [i.model_copy(deep=True) for i in baseline.insights])
        if (not session.analyzed_sentences and session.review_outcome != "legitimate"
                and session.guidance_status != "unavailable"):
            session.analysis_provider = baseline.provider
            session.provider_notice = baseline.notice
        await self.publish(session, "insight_updated")

    def _initial_task(self, session_id):
        session = self.get(session_id)
        if session.initial_analysis is not None and not (session.mode == 'live' and session.initial_analysis.provider != 'gemini'):
            return None
        if time.monotonic() < self.initial_retry_after.get(session_id, 0):
            return None
        task = self.initial_tasks.get(session_id)
        if task is None:
            task = asyncio.create_task(self._initialize(session_id))
            self.initial_tasks[session_id] = task
            def completed(done):
                if not done.cancelled():
                    done.exception()
                if self.initial_tasks.get(session_id) is done:
                    self.initial_tasks.pop(session_id, None)
            task.add_done_callback(completed)
        return task

    async def _ensure_initial(self, session_id):
        task = self._initial_task(session_id)
        if task is not None:
            await asyncio.shield(task)

    def active(self, session_id: str, *, allow_ending=False) -> Session:
        session = self.get(session_id)
        if session.status != "active":
            raise HTTPException(409, "This session has ended.")
        if session_id in self.ending and not allow_ending:
            raise HTTPException(409, "This session is finishing. Wait for the summary.")
        return session

    def create(self, request: CreateSession) -> Session:
        if len(self.sessions) >= self.settings.max_sessions:
            raise HTTPException(429, "The local session limit was reached. Delete an older session before starting another.")
        if not request.consent:
            raise HTTPException(422, "Explicit consent is required before conversation processing.")
        member = self.members.member(request.member_id)
        if not member:
            raise HTTPException(404, "Member not found.")
        session = Session(**request.model_dump(), data_source=member["data_source"])
        session.retain_transcript = request.retain_transcript and self.settings.allow_transcript_retention
        history = self.members.events(session.member_id, session.as_of)
        session.attention = calculate_attention(history, session.payment, [], as_of=session.as_of)
        if session.mode == "live":
            session.analysis_provider = "unavailable"
            session.provider_notice = "Live analysis requested. Gemini access has not yet been verified."
        session.activity.append(Activity(description="Consented review session created."))
        self.sessions[session.session_id] = session
        self.repository.save(session)
        return session

    async def publish(self, session: Session, event_type: str, extra: dict | None = None):
        session.revision += 1
        if session.session_id in self.sessions:
            self.repository.save(session)
        payload = {"session": session.model_dump(mode="json"), **(extra or {})}
        await self.emit(session.session_id, event_type, payload)

    async def transcript(self, session_id: str, request: TranscriptInput, source="manual", metadata=None) -> Session:
        session = self.active(session_id, allow_ending=source == "gemini")
        fingerprint = hashlib.sha256(json.dumps(request.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
        receipt = self.repository.segment_receipt(session_id, request.segment_id)
        if receipt:
            if receipt["fingerprint"] != fingerprint:
                raise HTTPException(409, "This segment ID was already used for different content.")
            return session
        existing = next((s for s in session.segments if s.segment_id == request.segment_id), None)
        if existing:
            if (existing.text, existing.sequence, existing.speaker) != (request.text, request.sequence, request.speaker):
                raise HTTPException(409, "This segment ID was already used for different content.")
            return session
        if self.repository.sequence_exists(session_id, request.sequence) or any(s.sequence == request.sequence for s in session.segments):
            raise HTTPException(409, "This transcript sequence already exists. Refresh and retry.")
        if session.segment_count >= self.settings.max_segments:
            raise HTTPException(422, "This demonstration session reached its transcript limit.")
        data = request.model_dump()
        if data['speaker'] == 'auto':
            data['speaker'] = 'unknown'
        segment = Segment(**data, source=source, session_id=session_id, **(metadata or {}))
        session.segments.append(segment)
        session.segments.sort(key=lambda s: (s.sequence, s.segment_id))
        session.segment_count += 1
        session.next_sequence = max(session.next_sequence, request.sequence + 1)
        if session.review_outcome == "legitimate":
            session.review_outcome = "in_review"
        # Unattributed live chunks and teller turns must not repeatedly cancel
        # guidance already running on confirmed member evidence.
        running = self.tasks.get(session_id)
        keep_running = bool(running and not running.done() and session.guidance_status == 'reviewing' and not (
            segment.speaker == 'member' and segment.diarization_status == 'CONFIRMED'))
        if not keep_running:
            session.analysis_version += 1
        session.analysis_pending = True
        session.activity.append(Activity(description="Finalized conversation turn received."))
        self.repository.save(session, receipt={"segment_id": request.segment_id,
                                              "sequence": request.sequence, "fingerprint": fingerprint})
        await self.publish(session, "transcript_final", {"segment": segment.model_dump(mode="json")})
        if keep_running:
            return session
        previous = self.tasks.pop(session_id, None)
        if previous:
            previous.cancel()
        self.tasks[session_id] = asyncio.create_task(self._analyze(session.model_copy(deep=True)))
        return session

    async def _analyze(self, snapshot: Session):
        sid, version = snapshot.session_id, snapshot.analysis_version
        try:
            await self.emit(sid, "analysis_started", {"analysis_version": version})
            history = self.members.events(snapshot.member_id, snapshot.as_of)
            session = self.sessions.get(sid)
            if not session or session.status != "active" or session.analysis_version != version:
                return
            if snapshot.mode == 'live' and snapshot.segments:
                # History remains independently visible; do not delay live guidance.
                self._initial_task(sid)
            else:
                await self._ensure_initial(sid)
            if self.sessions.get(sid) is not session or session.status != 'active' or session.analysis_version != version:
                return
            snapshot.initial_analysis = session.initial_analysis
            work = []
            for index, segment in enumerate(snapshot.segments):
                if not segment.finalized or segment.speaker != "member" or segment.diarization_status != "CONFIRMED" or segment.speaker_source not in {"manual", "diarization"}:
                    continue
                for sentence_index, (_, end) in enumerate(sentence_spans(segment.text)):
                    key = f"{segment.segment_id}:{sentence_index}"
                    if key not in session.analyzed_sentences:
                        work.append((key, index, end))
            if not work:
                session.analysis_pending = False
                session.guidance_status = "waiting_for_speaker" if any(s.diarization_status in {"PENDING", "UNCERTAIN", "FAILED"} for s in session.segments) else "ready"
                if session.initial_analysis and not session.analyzed_sentences and session.review_outcome != "legitimate":
                    session.analysis_provider = session.initial_analysis.provider
                    session.provider_notice = session.initial_analysis.notice
                await self.publish(session, "insight_updated")
            # Live assessments already review all context. Coalesce accumulated
            # sentences instead of replaying two provider calls per old sentence.
            pending_keys = [item[0] for item in work]
            if snapshot.mode == "live" and work:
                work = [work[-1]]
            if work:
                session.guidance_status = "reviewing"
                await self.publish(session, "insight_updated")
            for key, index, end in work:
                turn = snapshot.model_copy(deep=True)
                if snapshot.mode != "live":
                    turn.segments = turn.segments[:index + 1]
                    turn.segments[-1].text = turn.segments[-1].text[:end]
                turn.insights = session.insights.copy()
                turn.attention = session.attention.model_copy(deep=True)
                result, provider, notice = await self.analysis.analyze(turn, history)
                session = self.sessions.get(sid)
                if not session or session.status != "active" or session.analysis_version != version:
                    return
                if provider == "unavailable":
                    # Keep the last successful assessment and leave these turns
                    # eligible for a later retry; a failed request is not evidence.
                    session.analysis_provider = provider
                    session.guidance_status = "unavailable"
                    if provider not in session.analysis_sources:
                        session.analysis_sources.append(provider)
                    session.provider_notice = notice
                    session.analysis_pending = False
                    await self.publish(session, "insight_updated")
                    break
                session.analyzed_sentences.extend(pending_keys if session.mode == "live" else [key])
                # Only later, attributed member statements can contradict a cue.
                latest = turn.segments[-1]
                for old in session.insights:
                    if session.mode == "live":
                        # Each reviewed LLM result is the current full-context
                        # assessment, replacing obsolete model hypotheses.
                        if old.status in {"active", "acknowledged"}:
                            old.status = "superseded"
                        continue
                    kinds = {s.signal_type for s in old.signals} | {
                        {"URGENCY": "TIME_PRESSURE", "UNVERIFIED_RELATIONSHIP": "IDENTITY_VERIFICATION_NEEDED"}.get(c, c)
                        for c in old.signal_codes}
                    source_ids = {e.source_id for e in old.evidence if e.source_type == "segment"}
                    if (old.status in {"active", "acknowledged"} and source_ids
                            and any(denied(kind, latest.text) for kind in kinds)):
                        old.status = "superseded"
                self._merge_insights(session, result.insights)
                session.analysis_provider = provider
                if provider not in session.analysis_sources:
                    session.analysis_sources.append(provider)
                session.provider_notice = notice
                session.analysis_pending = key != work[-1][0]
                session.guidance_status = "ready"
                session.attention = calculate_attention(history, session.payment, session.insights,
                                                        previous=session.attention, as_of=session.as_of)
                await self.publish(session, "insight_updated")
                await self.emit(sid, "attention_updated", {"session": session.model_dump(mode="json")})
        except asyncio.CancelledError:
            raise
        except Exception:
            session = self.sessions.get(sid)
            if session and session.status == "active" and session.analysis_version == version:
                session.analysis_pending = False
                session.analysis_provider = "unavailable"
                session.guidance_status = "unavailable"
                session.provider_notice = "Analysis could not complete. Review the transcript and continue with employee judgment."
                await self.publish(session, "insight_updated")
        finally:
            if self.tasks.get(sid) is asyncio.current_task():
                self.tasks.pop(sid, None)

    async def retry_guidance(self, session_id):
        session = self.active(session_id)
        running = self.tasks.get(session_id)
        if running and not running.done():
            return session
        if session.review_outcome == 'legitimate':
            raise HTTPException(409, 'This review has a recorded decision. Add new member evidence before reviewing again.')
        # Failed turns remain unanalyzed. Reuse them without fabricating a new
        # transcript turn or bypassing provider cooldowns and human decisions.
        session.analysis_version += 1
        session.analysis_pending = True
        session.guidance_status = 'reviewing'
        await self.publish(session, 'insight_updated')
        self.tasks[session_id] = asyncio.create_task(self._analyze(session.model_copy(deep=True)))
        return session

    @staticmethod
    def _invalidate_assessment(session, history):
        session.analyzed_sentences = []
        for insight in session.insights:
            if insight.status in {"active", "acknowledged"} and insight.source_segment_ids:
                insight.status = "superseded"
        session.attention = calculate_attention(history, session.payment, session.insights,
                                                previous=session.attention, as_of=session.as_of)

    async def correct_speaker(self, session_id, segment_id, speaker, scope="segment"):
        session = self.active(session_id)
        segment = next((s for s in session.segments if s.segment_id == segment_id), None)
        if segment is None:
            raise HTTPException(404, "Transcript turn not found.")
        if scope == 'speaker' and not segment.speaker_id:
            raise HTTPException(409, 'This turn has no confirmed voice identity. Correct this turn individually.')
        targets = [segment]
        if scope == 'speaker':
            session.speaker_roles[segment.speaker_id] = speaker
            targets = [item for item in session.segments if item.speaker_id == segment.speaker_id]
        for item in targets:
            item.speaker = item.speaker_role = speaker
            item.speaker_source = 'diarization' if scope == 'speaker' else 'manual'
            item.diarization_status = 'CONFIRMED' if speaker != 'unknown' else 'UNCERTAIN'
            item.revision += 1
        session.analysis_version += 1
        session.analysis_pending = True
        session.speaker_notice = ""
        if session.review_outcome == "legitimate":
            session.review_outcome = "in_review"
        history = self.members.events(session.member_id, session.as_of)
        self._invalidate_assessment(session, history)
        session.activity.append(Activity(description="Transcript speaker corrected; evidence review requested."))
        previous = self.tasks.pop(session_id, None)
        if previous:
            previous.cancel()
        await self.publish(session, "transcript_speaker_updated")
        self.tasks[session_id] = asyncio.create_task(self._analyze(session.model_copy(deep=True)))
        return session

    async def reconcile_speakers(self, reconciler):
        session = reconciler.session
        if (self.sessions.get(session.session_id) is not session or session.status != "active"
                or session.session_id in self.ending):
            return
        changed = []
        member_evidence_changed = False
        for segment in session.segments:
            if segment.speaker_source != "diarization":
                continue
            match = reconciler.match(segment)
            if match is None:
                continue
            identity, start, end, status = match
            role = session.speaker_roles.get(identity, "unknown") if identity else "unknown"
            if status == "CONFIRMED" and role == "unknown":
                status = "UNCERTAIN"
            before = (segment.speaker_id, segment.speaker, segment.diarization_status)
            after = (identity, role, status)
            if before == after:
                continue
            member_evidence_changed |= ((before[1] == 'member' and before[2] == 'CONFIRMED')
                                        or (role == 'member' and status == 'CONFIRMED'))
            segment.speaker_id, segment.speaker, segment.diarization_status = after
            segment.speaker_role = role
            if start is not None and end is not None:
                segment.start_time, segment.end_time = start, end
                segment.timing_source = "provider"
            segment.revision += 1
            changed.append(segment.segment_id)
            session.activity.append(Activity(description=f"Speaker attribution updated for turn {segment.segment_id}: {before[1]} to {role}; {status}."))
        if changed:
            running = self.tasks.get(session.session_id)
            if not member_evidence_changed and running and not running.done() and session.guidance_status == 'reviewing':
                await self.publish(session, "transcript_speaker_updated", {"segment_ids": changed})
                return
            session.analysis_version += 1
            history = self.members.events(session.member_id, session.as_of)
            if member_evidence_changed:
                self._invalidate_assessment(session, history)
            session.analysis_pending = True
            previous = self.tasks.pop(session.session_id, None)
            if previous:
                previous.cancel()
            await self.publish(session, "transcript_speaker_updated", {"segment_ids": changed})
            self.tasks[session.session_id] = asyncio.create_task(self._analyze(session.model_copy(deep=True)))

    @staticmethod
    def _merge_insights(session, insights):
        # A repeated observation cannot reset an employee's dismissal or add points twice.
        for insight in insights:
            withdrawn = {s.signal_type for s in insight.signals if s.status == "NOT_ESTABLISHED"}
            for old in session.insights:
                if old.status in {"active", "acknowledged"} and withdrawn & {s.signal_type for s in old.signals}:
                    old.status = "superseded"
            def signature_of(item):
                return tuple(sorted(s.signal_type for s in item.signals)) or tuple(sorted(set(item.signal_codes))) or (item.title.casefold(),)
            signature = signature_of(insight)
            previous = next((i for i in reversed(session.insights) if
                             signature_of(i) == signature), None)
            if previous:
                insight.insight_id = previous.insight_id
                if previous.status != "superseded":
                    insight.status = previous.status
                session.insights[session.insights.index(previous)] = insight
            else:
                session.insights.append(insight)
        session.insights = session.insights[-100:]

    async def emit(self, session_id: str, event_type: str, payload: dict, *, target: WebSocket | None = None):
        message = Envelope(type=event_type, session_id=session_id, payload=payload).model_dump(mode="json")
        stale = []
        recipients = (target,) if target is not None else tuple(self.connections.get(session_id, set()))
        for socket in recipients:
            try:
                await asyncio.wait_for(socket.send_json(message), timeout=2)
            except Exception:
                stale.append(socket)
        for socket in stale:
            self.connections.get(session_id, set()).discard(socket)

    async def shutdown(self):
        if hasattr(self, "diarization"):
            await self.diarization.shutdown()
        finishing = list(self.finish_tasks.values())
        for task in finishing:
            task.cancel()
        if finishing:
            await asyncio.gather(*finishing, return_exceptions=True)
        tasks = [*self.tasks.values(), *self.initial_tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.repository.close()

    def list_sessions(self):
        return sorted(self.sessions.values(), key=lambda s: s.created_at, reverse=True)

    async def action(self, session_id: str, request: ActionInput) -> Session:
        session = self.active(session_id)
        kind = request.action_type
        if len(session.actions) >= 200:
            raise HTTPException(422, "This session reached its action limit.")
        if kind in {ActionType.DISMISS, ActionType.RESOLVE_SIGNAL, ActionType.DISMISS_SIGNAL, ActionType.MARK_LEGITIMATE} and not request.note.strip():
            raise HTTPException(422, "Record the reason or independent verification for this decision.")
        insight = next((i for i in session.insights if i.insight_id == request.insight_id), None)
        signal = next((s for s in session.attention.signals if s.signal_id == request.signal_id), None)
        if request.insight_id and insight is None:
            raise HTTPException(404, "Insight not found in this session.")
        if request.signal_id and signal is None:
            raise HTTPException(404, "Signal not found in this session.")
        if kind in {ActionType.ACKNOWLEDGE, ActionType.DISMISS} and not insight:
            raise HTTPException(422, "Select an insight for this action.")
        if kind in {ActionType.RESOLVE_SIGNAL, ActionType.DISMISS_SIGNAL} and not signal:
            raise HTTPException(422, "Select a signal for this action.")
        if kind == ActionType.CONSIDER_HOLD and session.payment.rail not in {"Wire", "ACH", "External Transfer"}:
            raise HTTPException(422, "A simulated hold is available only for Wire, ACH or External Transfer. Offer voluntary review for this payment.")
        if kind == ActionType.ACKNOWLEDGE:
            insight.status = "acknowledged"
        elif kind == ActionType.DISMISS:
            insight.status = "dismissed"
            for item in session.attention.signals:
                if item.origin == "conversation" and item.code in insight.signal_codes:
                    item.status = "dismissed"
        elif kind in {ActionType.RESOLVE_SIGNAL, ActionType.DISMISS_SIGNAL}:
            signal.status = "resolved" if kind == ActionType.RESOLVE_SIGNAL else "dismissed"
            for item in session.insights:
                if signal.code in item.signal_codes and all(
                    s.status != "active" for s in session.attention.signals
                    if s.code in item.signal_codes and s.origin == "conversation"
                ):
                    item.status = "resolved" if kind == ActionType.RESOLVE_SIGNAL else "dismissed"
        elif kind == ActionType.MARK_LEGITIMATE:
            session.review_outcome = "legitimate"
            for item in session.attention.signals:
                item.status = "resolved"
            for item in session.insights:
                item.status = "resolved"
        elif kind in {ActionType.ESCALATE_CASE, ActionType.OFFER_SPECIALIST, ActionType.CONSIDER_HOLD}:
            session.review_outcome = "needs_follow_up"
        # Cancel old inference before it can overwrite a human decision.
        session.analysis_version += 1
        pending = self.tasks.pop(session_id, None)
        if pending:
            pending.cancel()
        resume_analysis = bool(pending) and kind != ActionType.MARK_LEGITIMATE
        session.analysis_pending = resume_analysis
        if pending and kind == ActionType.MARK_LEGITIMATE:
            session.provider_notice = "Pending analysis stopped after the employee completed the legitimate-member review."
        note = request.note.strip()
        if kind == ActionType.CONSIDER_HOLD:
            note = "SIMULATED request for a review hold of up to 4 hours. No funds held. " + note
        elif kind == ActionType.OFFER_TRUSTED_CONTACT:
            note = "SIMULATED voluntary contact offer; member consent required; no control over funds. " + note
        action_data = request.model_dump()
        action_data["note"] = note[:2000]
        session.actions.append(TellerAction(**action_data))
        session.activity.append(Activity(description="Employee recorded: " + kind.value.replace("_", " ").lower() + " (simulation)."))
        session.attention = calculate_attention(self.members.events(session.member_id, session.as_of), session.payment,
                                                session.insights, session.attention, as_of=session.as_of)
        await self.publish(session, "session_status")
        if resume_analysis:
            self.tasks[session_id] = asyncio.create_task(self._analyze(session.model_copy(deep=True)))
        return session

    async def finish(self, session_id: str, audio) -> Session:
        """One end operation per session; a disconnected caller cannot release its guard."""
        session = self.get(session_id)
        if session.status == "ended":
            return session
        task = self.finish_tasks.get(session_id)
        if task is None:
            self.ending.add(session_id)
            task = asyncio.create_task(self._finish(session_id, audio), name="finish-review")
            self.finish_tasks[session_id] = task
            # Retrieve errors even if all HTTP callers have disconnected.
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        return await asyncio.shield(task)

    async def _finish(self, session_id: str, audio) -> Session:
        try:
            if hasattr(audio, "diarization"):
                await audio.diarization.stop_session(session_id)
            await audio.stop(session_id, finalize=True)
            return await self.end(session_id)
        finally:
            self.ending.discard(session_id)
            if self.finish_tasks.get(session_id) is asyncio.current_task():
                self.finish_tasks.pop(session_id, None)

    async def end(self, session_id: str) -> Session:
        session = self.get(session_id)
        if hasattr(self, "diarization"):
            await self.diarization.stop_session(session_id)
        if session.status == "ended":
            return session
        pending = self.tasks.get(session_id)
        if pending:
            try:
                await asyncio.wait_for(asyncio.shield(pending), timeout=self.settings.provider_timeout_seconds + 6)
            except (TimeoutError, asyncio.CancelledError):
                pending.cancel()
                session.analysis_pending = False
                session.provider_notice = "Final analysis did not complete before the review ended. Review the recorded evidence manually."
        if session.status == "ended":
            return session
        session.status = "ended"
        initial = self.initial_tasks.pop(session_id, None)
        if initial:
            initial.cancel()
            await asyncio.gather(initial, return_exceptions=True)
        session.ended_at = now()
        session.audio_status = "stopped"
        session.analysis_pending = False
        session.activity.append(Activity(description="Review ended. No real payment action was taken."))
        if not session.retain_transcript:
            redacted = redact_session(session)
            # Keep the object identity held by socket/provider handlers while purging text.
            for field in type(session).model_fields:
                setattr(session, field, getattr(redacted, field))
        await self.publish(session, "session_status")
        return session

    def summary(self, session_id: str) -> SessionSummary:
        session = self.get(session_id)
        return SessionSummary(session_id=session_id, member_id=session.member_id, status=session.status,
                              review_outcome=session.review_outcome, created_at=session.created_at,
                              ended_at=session.ended_at, payment=session.payment, attention=session.attention,
                              transcript_segment_count=session.segment_count,
                              transcript_retained=session.retain_transcript,
                              analysis_providers=sorted(session.analysis_sources),
                              initial_analysis=session.initial_analysis,
                              observations=[i.observation for i in session.insights if i.status not in {"dismissed", "superseded"}],
                              unresolved_signals=[s.title for s in session.attention.signals if s.status == "active"],
                              actions=session.actions)

    async def delete(self, session_id: str):
        self.initial_retry_after.pop(session_id, None)
        if hasattr(self, "diarization"):
            await self.diarization.stop_session(session_id)
        session = self.get(session_id)
        session.status = "ended"
        initial = self.initial_tasks.pop(session_id, None)
        if initial:
            initial.cancel()
            await asyncio.gather(initial, return_exceptions=True)
        pending = self.tasks.pop(session_id, None)
        if pending:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        self.sessions.pop(session_id, None)
        self.repository.delete(session_id)
        for socket in tuple(self.connections.pop(session_id, set())):
            with suppress(Exception):
                await socket.close(code=4404)
