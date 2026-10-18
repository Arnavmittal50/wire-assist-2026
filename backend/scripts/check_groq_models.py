"""Small real-provider benchmark using synthetic text only; never places calls."""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings, ROOT
from app.schemas.contracts import Session, Payment, Segment
from app.services.groq_analysis import GroqAnalysisService
from app.services.member_data import MemberData


async def main():
    settings = Settings()
    if not settings.groq_api_key:
        print('GROQ_API_KEY is missing. No requests sent.')
        return 1
    cases = [
        ('family_identity', [
            ('teller', "What's the purpose of your wire transfer today?"),
            ('member', "I'd like to transfer a million dollars to my brother."),
            ('teller', "Are you sure it's your brother? Do you have verifiable proof?"),
            ('member', 'He gave me a phone call. It sounded like my brother. He also sent me a selfie.')],
         {'IDENTITY_VERIFICATION_NEEDED'}, {'TIME_PRESSURE', 'SECRECY', 'INVESTMENT_RISK'}),
        ('deadline', [('member', 'He said I have to send the money in the next 30 minutes or something terrible will happen.')],
         {'TIME_PRESSURE'}, {'SECRECY', 'INVESTMENT_RISK'}),
        ('teller_question_and_denial', [('teller', 'Did someone tell you to keep this secret or send money urgently?'),
                                       ('member', 'No. Nobody asked for secrecy and I have no deadline. I verified my brother in person.')],
         set(), {'TIME_PRESSURE', 'SECRECY', 'POSSIBLE_IMPERSONATION'}),
    ]
    rows = []
    for model in ('openai/gpt-oss-20b', 'openai/gpt-oss-120b'):
        config = settings.model_copy(update={'groq_model': model, 'groq_precall_model': model, 'groq_min_interval_seconds': 1})
        service = GroqAnalysisService(config)
        for name, turns, required, forbidden in cases:
            session = Session(member_id='M1002', as_of='2026-09-27T02:25:00Z', consent=True,
                              retain_transcript=False, mode='live', payment=Payment(amount=1000000), data_source='synthetic benchmark')
            session.segments = [Segment(segment_id=f's{i}', sequence=i, speaker=role, text=text)
                                for i, (role, text) in enumerate(turns)]
            started = time.perf_counter()
            result, provider, notice = await service.analyze(session, [])
            if provider == 'unavailable' and '429' in notice:
                await asyncio.sleep(max(0, service.next_request - time.monotonic()) + 1)
                result, provider, notice = await service.analyze(session, [])
            kinds = {s.signal_type for i in result.insights for s in i.signals if s.status != 'NOT_ESTABLISHED'}
            row = {'model': model, 'case': name, 'wall_seconds': round(time.perf_counter()-started, 3), 'passed': provider == 'groq' and required <= kinds and not forbidden & kinds,
                   'provider': provider, 'signals': sorted(kinds), 'notice': notice,
                   'metrics': service.metrics[-1] if provider == 'groq' else None}
            rows.append(row)
            print(json.dumps(row), flush=True)
            await asyncio.sleep(22)
        history_session = Session(member_id='M1002', as_of='2026-09-27T02:25:00Z', consent=True,
                                  retain_transcript=False, mode='live', payment=Payment(), data_source='synthetic benchmark')
        events = MemberData(settings.data_dir).events('M1002', history_session.as_of)
        started = time.perf_counter()
        review = await service.initialize(history_session, events)
        if review.provider == 'unavailable' and '429' in review.notice:
            await asyncio.sleep(max(0, service.next_request - time.monotonic()) + 1)
            review = await service.initialize(history_session, events)
        row = {'model': model, 'case': 'M1002_history', 'wall_seconds': round(time.perf_counter()-started, 3), 'passed': review.provider == 'groq' and bool(review.summary),
               'provider': review.provider, 'fact_count': len(review.summary), 'notice': review.notice,
               'metrics': service.metrics[-1] if review.provider == 'groq' else None}
        rows.append(row)
        print(json.dumps(row), flush=True)
    (ROOT / '.tooling' / 'groq-benchmark.json').write_text(json.dumps(rows, indent=2))
    (ROOT / '.tooling' / f'groq-benchmark-{int(time.time())}.json').write_text(json.dumps(rows, indent=2))
    return int(any(not r['passed'] for r in rows))


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
