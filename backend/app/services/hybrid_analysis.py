"""Groq history review and Gemini live guidance, with shared evidence rules."""
from app.services.analysis import AnalysisService
from app.services.groq_analysis import GroqAnalysisService, PROMPT, compact_context, canonical_record_quotes
from app.schemas.contracts import ActionType


class HybridAnalysisService:
    def __init__(self, settings, *, gemini_client_factory=None):
        self.history = GroqAnalysisService(settings)
        # Live analysis stays on the requested Gemini model, including failures;
        # it never falls back to the rate-limited Groq account.
        live_settings = settings.model_copy(update={'gemini_analysis_model': settings.gemini_update_model})
        kwargs = {'client_factory': gemini_client_factory} if gemini_client_factory else {}
        self.live = AnalysisService(live_settings, live_prompt=PROMPT,
                                    live_context_builder=compact_context,
                                    record_quote_normalizer=canonical_record_quotes, **kwargs)

    async def initialize(self, session, events):
        return await self.history.initialize(session, events)

    async def analyze(self, session, events):
        result, provider, notice = await self.live.analyze(session, events)
        if provider == 'gemini' and session.mode == 'live' and session.payment.rail == 'Wire':
            identity_already_confirmed = any(check.check_type == 'RECIPIENT_IDENTITY' and check.status == 'CONFIRMED'
                for prior in session.insights if prior.status in {'active', 'acknowledged'}
                for check in prior.verification_checks)
            for insight in result.insights:
                # An unfinished procedural check is a follow-up, not a scam signal.
                # No attention points or missing evidence are invented here.
                confirmed = {check.check_type for check in insight.verification_checks if check.status == 'CONFIRMED'}
                if {'RECIPIENT_IDENTITY', 'PAYMENT_INSTRUCTIONS'} <= confirmed:
                    continue
                completion = insight.recommended_action in {ActionType.CONTINUE_REVIEW, ActionType.NO_ACTION}
                newly_confirmed_identity = 'RECIPIENT_IDENTITY' in confirmed and not identity_already_confirmed
                other_concern = any(signal.status != 'NOT_ESTABLISHED' and signal.signal_type not in
                    {'LARGE_TRANSACTION', 'UNUSUAL_PAYMENT_PATTERN'} for signal in insight.signals)
                routine_followup = insight.recommended_action in {ActionType.ASK_FOLLOW_UP, ActionType.OFFER_VERIFICATION}
                if not completion and not (newly_confirmed_identity and routine_followup and not other_concern):
                    continue
                insight.recommended_action = ActionType.ASK_FOLLOW_UP
                insight.priority = 'low'
                if 'RECIPIENT_IDENTITY' in confirmed:
                    insight.title = 'Check the wire instructions'
                    insight.observation = 'The recipient is confirmed. How the account and routing numbers were checked is still unclear.'
                    insight.suggested_question = 'How did you confirm the account and routing numbers?'
                else:
                    insight.title = 'Confirm the recipient'
                    insight.observation = 'Who will receive the wire and how its instructions were checked still need confirmation.'
                    insight.suggested_question = "Have you confirmed who you're sending this money to?"
        return result, provider, notice
