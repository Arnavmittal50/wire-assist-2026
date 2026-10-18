"""Opt-in real Groq checks using synthetic M1002/M1057 scenarios; no phone calls.

Run from the repository root with .venv/Scripts/python backend/scripts/check_live_guidance.py.
Uses backend/.env, respects provider cooldowns, and prints no credentials.
"""
import asyncio
import json
import sys
import time
from pathlib import Path
from groq import AsyncGroq

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.config import Settings, ROOT
from app.schemas.contracts import Session, Segment, Payment
from app.services.groq_analysis import GroqAnalysisService
from app.services.member_data import MemberData
from app.services.risk_engine import calculate_attention


async def main():
    settings = Settings()
    if not settings.groq_api_key:
        print('GROQ_API_KEY missing; no requests sent.')
        return 1
    raw_outputs = []
    def client_factory(**kwargs):
        client = AsyncGroq(**kwargs)
        create = client.chat.completions.with_raw_response.create
        async def recorded(**request):
            raw = await create(**request)
            response = await raw.parse()
            raw_outputs.append({'model': request['model'], 'content': response.choices[0].message.content})
            (ROOT / '.tooling' / 'live-guidance-raw.json').write_text(json.dumps(raw_outputs, indent=2), encoding='utf-8')
            return raw
        client.chat.completions.with_raw_response.create = recorded
        return client
    service = GroqAnalysisService(settings, client_factory)
    members = MemberData(settings.data_dir)
    rows = []

    def make(member):
        session = Session(member_id=member,
                          as_of='2026-10-19T16:45:00Z' if member == 'M1057' else '2026-09-27T02:25:00Z', consent=True,
                          retain_transcript=False, mode='live', payment=Payment(), data_source='synthetic evaluation')
        if member == 'M1057':
            payment = next(e for e in members.events(member, session.as_of) if e['event_id'] == 'E03702')
            session.payment = Payment(amount=payment['amount'], rail=payment['payment_rail'], recipient_id=payment['recipient_id'])
        return session

    async def assess(name, session, required, forbidden, question_terms=()):
        before = time.perf_counter()
        result, provider, notice = await service.analyze(session, members.events(session.member_id, session.as_of))
        active = {signal.signal_type for item in result.insights for signal in item.signals if signal.status != 'NOT_ESTABLISHED'}
        observed = {signal.signal_type for item in result.insights for signal in item.signals if signal.status == 'OBSERVED'}
        question = ' '.join(item.suggested_question for item in result.insights)
        score = calculate_attention([], session.payment, result.insights).score
        row = {'case': name, 'provider': provider, 'seconds': round(time.perf_counter()-before, 2),
               'passed': provider == 'groq' and required <= active and not forbidden & active
                         and all(term in question.lower() for term in question_terms),
               'question': question, 'signals': sorted(active), 'conversation_points': score,
               'observed_signals': sorted(observed),
               'notice': notice, 'metrics': service.metrics[-1] if provider == 'groq' else None}
        session.insights = result.insights
        rows.append(row)
        print(json.dumps(row), flush=True)
        return row

    for member in ('M1002', 'M1057'):
        if '--precall-only' in sys.argv and member != 'M1002':
            continue
        session = make(member)
        before = time.perf_counter()
        review = await service.initialize(session, members.events(member, session.as_of))
        row = {'case': member + '_precall', 'provider': review.provider,
               'seconds': round(time.perf_counter()-before, 2),
               'passed': review.provider == 'groq' and bool(review.summary),
               'event_count': review.event_count, 'notice': review.notice,
               'metrics': service.metrics[-1] if review.provider == 'groq' else None}
        rows.append(row)
        print(json.dumps(row), flush=True)

    if '--precall-only' in sys.argv:
        return int(any(not row['passed'] for row in rows))

    session = make('M1002')
    session.segments = [Segment(segment_id='s1', sequence=0, speaker='member',
        text='I want to wire $1500 to my girlfriend. We met online a few months ago. She showed me an investment platform with guaranteed returns. I have never met her in person.')]
    investment = await assess('M1002_investment', session, {'INVESTMENT_RISK', 'IDENTITY_VERIFICATION_NEEDED'},
                 {'TIME_PRESSURE', 'SECRECY'}, ('investment',))
    investment['passed'] &= any(word in investment['question'].lower() for word in ('recipient', 'girlfriend', 'identity'))
    investment['passed'] &= 'INVESTMENT_RISK' in investment['observed_signals']
    session.segments += [Segment(segment_id='s2', sequence=1, speaker='teller',
                                 text='Have you independently verified the recipient and the investment?'),
                         Segment(segment_id='s3', sequence=2, speaker='member',
                                 text='No. I have only used the platform link she gave me. How can I check it?')]
    follow = await assess('M1002_next_step', session, {'INVESTMENT_RISK', 'IDENTITY_VERIFICATION_NEEDED'}, {'TIME_PRESSURE', 'SECRECY'})
    follow['passed'] &= 'routing' not in follow['question'].lower()
    follow['passed'] &= follow['question'] != investment['question']
    follow['passed'] &= any(i.recommended_action == 'OFFER_VERIFICATION' for i in session.insights)

    session = make('M1057')
    session.segments = [Segment(segment_id='s1', sequence=0, speaker='member',
        text='This is my first payment to the closing company for my home purchase. I have not checked who owns the receiving account yet.')]
    first = await assess('M1057_probe', session, {'IDENTITY_VERIFICATION_NEEDED'}, {'INVESTMENT_RISK', 'TIME_PRESSURE', 'SECRECY'})
    session.segments += [Segment(segment_id='s2', sequence=1, speaker='teller', text='How can you independently verify the recipient?'),
                         Segment(segment_id='s3', sequence=2, speaker='member',
        text='I just called my established closing attorney using the number from our original paperwork, not the new email. They confirmed the account belongs to the closing company and the amount matches my signed closing statement. Nobody is rushing me.')]
    last = await assess('M1057_verified', session, set(), {'IDENTITY_VERIFICATION_NEEDED', 'INVESTMENT_RISK', 'TIME_PRESSURE', 'SECRECY'})
    last['passed'] &= last['conversation_points'] < first['conversation_points']
    last['passed'] &= all(i.recommended_action in {'CONTINUE_REVIEW', 'NO_ACTION'} for i in session.insights)
    output = ROOT / '.tooling' / 'live-guidance-check.json'
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(rows, indent=2), encoding='utf-8')
    print(json.dumps({'passed': sum(bool(row['passed']) for row in rows), 'total': len(rows)}))
    return int(any(not row['passed'] for row in rows))


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
