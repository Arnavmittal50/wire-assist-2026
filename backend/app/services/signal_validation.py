"""Conservative evidence gates, independent of Gemini interpretation and scoring.

These gates establish only that a cue was described, never that a scam occurred.
Large means >= $100,000 for this demo; unusual patterns require record baselines.
"""
import json
import re

from app.schemas.contracts import SignalObservation


TIME_PRESSURE = (
    r"\b(?:have to|has to|must|need to|told me to|said to|demanded I|insist\w* I)\b"
    r"[^.!?]{0,90}\b(?:send|pay|transfer|wire|complete|happen)\b"
    r"[^.!?]{0,70}\b(?:today|now|immediately|within|in the next|by|before)\b"
    r"|\b(?:send|pay|transfer|wire)\b[^.!?]{0,40}\b(?:right now|immediately|within \d+ (?:minutes?|hours?))\b"
    r"|\b(?:deadline|urgent demand|rushing me|pressur\w* me to (?:hurry|send|pay)|hurry up)\b"
    r"|\b(?:or else|or something terrible will happen|or I(?:'ll| will) be arrested)\b"
)
SECRECY = r"\b(?:keep (?:it |this )?(?:a )?(?:secret|confidential)|don't (?:tell|mention)|do not tell|told me not to tell|not to tell anyone|keep this between us)\b"
IDENTITY_CLAIM = (
    r"\b(?:sounded|sounds) like (?:my |our |the )?(?:brother|sister|mother|father|son|daughter|wife|husband|friend|boss|bank|relative|him|her|them)\b"
    r"|\bvoice (?:sounded|sounds|was) familiar\b"
    r"|\b(?:he|she|they|caller|recipient|person)\b[^.!?]{0,60}\b(?:sent|showed|gave|provided)\b[^.!?]{0,50}\b(?:selfie|photo|picture)\b"
    r"|\b(?:only proof|only evidence)\b[^.!?]{0,50}\b(?:photo|selfie|voice|picture)\b"
    r"|\b(?:pretending to be|claimed to be|posing as)\b"
)
IDENTITY = IDENTITY_CLAIM + r"|\b(?:never met|haven't met|have not met|met (?:him |her |them |someone )?online|not (?:independently )?verified|haven't verified|have not verified|cannot verify|can't verify)\b"
INVESTMENT = r"\b(?:guaranteed (?:profits?|returns?)|risk.free|double my money|returns? (?:are |is )?guaranteed)\b"
DENIALS = {
    "TIME_PRESSURE": r"\b(?:no (?:deadline|urgency|time pressure)|not urgent|not being (?:rushed|pressured)|nobody is (?:rushing|pressuring)|(?:don't|do not) (?:have|need) to (?:send|pay|transfer)[^.!?]{0,40}(?:today|now)|can (?:wait|take my time)|deadline (?:was|is|has been) (?:removed|cancelled|withdrawn))\b",
    "SECRECY": r"\b(?:no (?:secrecy|secret)|(?:nobody|no one) (?:asked|told) me (?:to keep|not to tell)|(?:can|free to|allowed to) (?:tell|discuss|talk to))\b",
    "IDENTITY_VERIFICATION_NEEDED": r"\b(?:verified (?:his|her|their|the recipient's) identity|confirmed[^.!?]{0,60}(?:known|independent|official)|called[^.!?]{0,70}(?:known|independent|already had|official))\b",
    "INVESTMENT_RISK": r"\b(?:not an investment|no (?:guaranteed returns|investment)|returns? (?:are |is )?not guaranteed)\b",
}
DENIALS["POSSIBLE_IMPERSONATION"] = DENIALS["IDENTITY_VERIFICATION_NEEDED"]
DENIALS["PRESSURE"] = r"\b(?:not being pressured|nobody is pressuring|no (?:one is pressuring|pressure)|not under pressure)\b"


def positive(text, pattern):
    for match in re.finditer(pattern, text, re.I):
        before = text[max(0, match.start() - 60):match.start()]
        if not re.search(r"\b(?:no|nobody|not|never|wasn't|isn't|didn't|don't|doesn't)(?:\s+\w+){0,4}\s*$", before, re.I):
            # A question or hypothetical is not an asserted member experience.
            sentence_start = max(text.rfind('.', 0, match.start()), text.rfind('?', 0, match.start()), text.rfind('!', 0, match.start())) + 1
            sentence_end = re.search(r'[.!?](?:\s|$)', text[match.end():])
            tail = text[match.end():match.end() + sentence_end.end()] if sentence_end else text[match.end():]
            prefix = text[sentence_start:match.start()]
            if tail.rstrip().endswith('?') or re.search(r"\b(?:hypothetically|what if|suppose)\b", prefix, re.I):
                continue
            return True
    return False


def denied(kind, text):
    return bool(kind in DENIALS and re.search(DENIALS[kind], text, re.I))


def contradicted_later(kind, source_id, segments):
    ordered = list(segments)
    start = ordered.index(source_id) + 1 if source_id in segments else 0
    return any(denied(kind, segments[key]) for key in ordered[start:])


def supported(kind, text, source_type="segment"):
    if denied(kind, text):
        return False
    if kind == "LARGE_TRANSACTION":
        if source_type == "event":
            match = re.search(r'"amount":\s*(\d+(?:\.\d+)?)', text)
            return bool(match and float(match[1]) >= 100000)
        if not re.search(r'\b(?:transfer|send|wire|pay|payment|transaction)\b', text, re.I):
            return False
        if positive(text, r"\b(?:a|one|\d+) million (?:dollars|bucks)\b"):
            return True
        return any(float(m[1].replace(',', '')) >= 100000 and positive(text, re.escape(m[0]))
                   for m in re.finditer(r'\$\s*(\d[\d,]*(?:\.\d+)?)', text))
    if kind == "UNUSUAL_PAYMENT_PATTERN":
        if source_type != "event":
            return False
        match = re.search(r'"amount_to_baseline_ratio":\s*(\d+(?:\.\d+)?)', text)
        return bool(match and float(match[1]) > 1.5)
    if source_type == "event":
        # Only an explicit member response is speech evidence in a record.
        try:
            text = json.loads(text).get('member_response', '')
        except (ValueError, AttributeError):
            return False
        if not isinstance(text, str):
            return False
    pattern = {"TIME_PRESSURE": TIME_PRESSURE, "SECRECY": SECRECY,
               "IDENTITY_VERIFICATION_NEEDED": IDENTITY, "POSSIBLE_IMPERSONATION": IDENTITY_CLAIM,
               "INVESTMENT_RISK": INVESTMENT}.get(kind)
    return bool(pattern and positive(text, pattern))


TITLES = {
    "LARGE_TRANSACTION": "Large transaction amount described",
    "POSSIBLE_IMPERSONATION": "Possible impersonation — not confirmed",
    "IDENTITY_VERIFICATION_NEEDED": "Recipient identity needs independent verification",
    "TIME_PRESSURE": "Time pressure described",
    "SECRECY": "Request for secrecy described",
    "INVESTMENT_RISK": "Investment return promise needs review",
    "UNUSUAL_PAYMENT_PATTERN": "Payment exceeds its recorded baseline",
}
LEGACY_TYPES = {"URGENCY": "TIME_PRESSURE", "UNVERIFIED_RELATIONSHIP": "IDENTITY_VERIFICATION_NEEDED",
                "GUARANTEED_RETURNS": "INVESTMENT_RISK"}
QUESTIONS = {
    "LARGE_TRANSACTION": "Can we confirm the amount and purpose of this payment?",
    "POSSIBLE_IMPERSONATION": "Could you contact the recipient using a number you already know?",
    "IDENTITY_VERIFICATION_NEEDED": "How have you independently verified the recipient's identity?",
    "TIME_PRESSURE": "Who set the deadline, and what did they say would happen if you waited?",
    "SECRECY": "Who asked you to keep this payment secret, and why?",
    "INVESTMENT_RISK": "How have you independently checked the investment and its promised returns?",
    "UNUSUAL_PAYMENT_PATTERN": "What explains the change from your usual payments?",
}


def validate_observations(observations, segments, events, evidence):
    """Fail closed, including on clipped negations and evidence from another signal."""
    allowed = {(e.source_type, e.source_id, e.quote) for e in evidence}
    checked = []
    seen = set()
    for item in observations:
        source = (segments if item.source_type == 'segment' else events).get(item.source_id)
        if (not source or not item.evidence_quote.strip() or item.evidence_quote not in source
                or (item.source_type, item.source_id, item.evidence_quote) not in allowed
                or item.signal_type in seen):
            return None
        seen.add(item.signal_type)
        if item.status == 'NOT_ESTABLISHED':
            if not denied(item.signal_type, item.evidence_quote):
                return None
        elif (not supported(item.signal_type, item.evidence_quote, item.source_type)
              or not supported(item.signal_type, source, item.source_type)
              or contradicted_later(item.signal_type, item.source_id, segments)):
            return None
        # Identity evidence cannot confirm an actual impersonation incident.
        if item.signal_type == 'POSSIBLE_IMPERSONATION' and item.status == 'OBSERVED':
            item = item.model_copy(update={'status': 'POSSIBLE'})
        checked.append(item)
    return checked


def detect_observations(segments, events):
    found = {}
    for source_type, sources in [('event', events), ('segment', segments)]:
        for source_id, text in sources.items():
            # Full text avoids turning a negated phrase into a positive quotation.
            if len(text) > 1200:
                continue
            for kind in TITLES:
                negative = source_type == 'segment' and denied(kind, text)
                if not negative and not supported(kind, text, source_type):
                    continue
                status = 'NOT_ESTABLISHED' if negative else 'POSSIBLE' if kind == 'POSSIBLE_IMPERSONATION' else 'OBSERVED'
                found[kind] = SignalObservation(
                    signal_type=kind, status=status, source_type=source_type, source_id=source_id,
                    evidence_quote=text, explanation=("Later member evidence no longer supports this concern." if negative else TITLES[kind]),
                    suggested_question=QUESTIONS[kind])
    return list(found.values())
