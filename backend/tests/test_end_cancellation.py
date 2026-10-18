import asyncio

import pytest
from fastapi import HTTPException

from app.schemas.contracts import CreateSession, TranscriptInput
from app.services.member_data import MemberData
from app.services.sessions import SessionService


async def test_cancelled_end_caller_cannot_release_other_end_reservation(config):
    service = SessionService(config, MemberData(config.data_dir))
    session = service.create(CreateSession(member_id="M1002", consent=True))
    entered, release = asyncio.Event(), asyncio.Event()
    class Audio:
        calls = 0
        async def stop(self, sid, finalize=False):
            self.calls += 1
            entered.set()
            await release.wait()
    audio = Audio()
    first = asyncio.create_task(service.finish(session.session_id, audio))
    await entered.wait()
    second = asyncio.create_task(service.finish(session.session_id, audio))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert session.session_id in service.ending
    with pytest.raises(HTTPException) as error:
        await service.transcript(session.session_id, TranscriptInput(sequence=0, text="Late turn"))
    assert error.value.status_code == 409
    release.set()
    ended = await second
    assert audio.calls == 1 and ended.status == "ended"
    assert session.session_id not in service.ending and not service.finish_tasks
    await service.shutdown()
