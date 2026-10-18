"""Explicit live Gemini regression using synthetic examples; never captures audio."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings
from app.schemas.contracts import Payment, Segment, Session
from app.services.analysis import AnalysisService
from app.services.member_data import DEFAULT_AS_OF, MemberData


FAMILY = [
    ("teller", "What's the purpose of your wire transfer today?"),
    ("member", "I'd like to transfer a million dollars."),
    ("teller", "Who would you like to transfer this money to?"),
    ("member", "My brother."),
    ("teller", "Are you sure it's your brother?"),
    ("member", "He gave me a phone call. It sounded like my brother."),
    ("teller", "Do you have verifiable proof?"),
    ("member", "He also sent me a selfie."),
]


async def main():
    # This explicit script uses only the synthetic fixtures below. Capture model
    # output here to diagnose validation failures without logging real sessions.
    import app.services.analysis as module
    original_grounded = module._grounded
    proposals = []
    def capture(result, segments, events):
        proposals.append(result.model_dump())
        return original_grounded(result, segments, events)
    module._grounded = capture
    settings = Settings()
    if not settings.gemini_api_key.strip():
        raise RuntimeError("Configure GEMINI_API_KEY before explicitly running live checks.")
    analyzer = AnalysisService(settings)
    current = Session(member_id="M1002", as_of=DEFAULT_AS_OF, consent=True, retain_transcript=False,
                      mode="live", payment=Payment(amount=1000000), data_source="synthetic_test")
    history = MemberData(settings.data_dir).events(current.member_id, DEFAULT_AS_OF)
    initial = await analyzer.initialize(current, history)
    assert initial.provider == "gemini" and initial.summary, initial.notice
    print(f"PASS: Gemini initial review: {initial.event_count} events, {len(initial.summary)} cited facts.", flush=True)
    cases = [
        ("family", FAMILY, {"LARGE_TRANSACTION", "IDENTITY_VERIFICATION_NEEDED"},
         {"TIME_PRESSURE", "SECRECY", "INVESTMENT_RISK"}),
        ("explicit timing", [("member", "He said I have to send the money in the next 30 minutes or something terrible will happen.")],
         {"TIME_PRESSURE"}, {"SECRECY"}),
        ("paraphrased timing", [("teller", "What happens if you wait to make the payment?"),
                                ("member", "If this isn't settled before lunch, they say I'll lose my place and the money I've already put in.")],
         {"TIME_PRESSURE"}, {"SECRECY"}),
        ("later clarification", [("member", "He said I have to send the money in the next 30 minutes or something terrible will happen."),
                                  ("member", "I called him using the number I already had. He confirmed there is no deadline and I can wait as long as I want.")],
         set(), {"TIME_PRESSURE", "SECRECY"}),
    ]
    failures = []
    for name, dialogue, required, forbidden in cases:
        proposals.clear()
        started = time.monotonic()
        current.segments = [Segment(segment_id=f"s{i}", sequence=i, speaker=role, text=text)
                            for i, (role, text) in enumerate(dialogue)]
        result, provider, notice = await analyzer.analyze(current, [])
        if provider != 'gemini':
            failures.append(name)
            print(f"FAIL ({time.monotonic() - started:.1f}s): {name}: {notice}", flush=True)
            continue
        active = {signal.signal_type for insight in result.insights for signal in insight.signals
                  if signal.status != "NOT_ESTABLISHED"}
        if not (required <= active and not forbidden & active):
            failures.append(name)
            print(f"FAIL: {name}: categories={sorted(active)}; {notice}", flush=True)
            if '--debug' in sys.argv:
                print(proposals, flush=True)
            continue
        assert all(signal.status != "OBSERVED" for insight in result.insights for signal in insight.signals
                   if signal.signal_type == "POSSIBLE_IMPERSONATION")
        print(f"PASS ({time.monotonic() - started:.1f}s): {name}: {', '.join(sorted(active)) or 'no current concerns'}", flush=True)
    assert not failures, f"Live regressions did not pass: {', '.join(failures)}"


if __name__ == "__main__":
    asyncio.run(main())
