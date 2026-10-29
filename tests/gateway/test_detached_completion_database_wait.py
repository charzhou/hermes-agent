"""Detached completion retries find their receipt without blocking the gateway on SessionDB."""
import asyncio
from contextlib import contextmanager
import threading

import pytest

from tests.gateway.test_authority_lane_moves_on import _app_turn
from tests.gateway.test_detached_conversation_lifecycle import _automation_rows, _moved_on, _process_event
from tests.waiting import async_wait_until


@pytest.mark.asyncio
@pytest.mark.parametrize('database_wait', [False, True])
async def test_detached_completion_retry_finds_its_receipt(monkeypatch, database_wait):
    from gateway.session_automation import completion_admission

    runner, _adapter, authority, _source, _sent, _ran, _gates, _lane, ref = await _moved_on(monkeypatch)
    assert await _app_turn(authority, ref, 'start a job') == ('terminal', 'completed')
    live = authority.sessions[ref.session_id]
    event = _process_event(runner, live, ref.session_id, exit_code=0, output='done')
    runner._session_sources.clear()
    assert await runner._inject_watch_notification('[process proc_1 finished]', event) is True
    rows = await async_wait_until(
        lambda: [row for row in _automation_rows(authority, ref.session_id, live.route)
                 if row['status'] == 'terminal'], timeout=10, what='the detached completion receipt')
    # Settlement precedes drain cleanup; finish it before contending on the retry's database read.
    await asyncio.wait_for(live.task, 10)
    assert runner.session_store.lookup_by_session_key(live.route) is None

    if database_wait:
        db = authority.db
        db._wal_active = False
        held, release, expired = threading.Event(), threading.Event(), threading.Event()
        started = asyncio.Event()
        loop, original_read = asyncio.get_running_loop(), db._read_ctx

        @contextmanager
        def observed_read(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            with original_read(*args, **kwargs) as conn:
                yield conn

        def hold_database():
            with db._lock:
                held.set()
                if not release.wait(5):
                    expired.set()

        monkeypatch.setattr(db, '_read_ctx', observed_read)
        holder = asyncio.create_task(asyncio.to_thread(hold_database))
        pending = None
        try:
            assert await asyncio.to_thread(held.wait, 5)
            pending = asyncio.create_task(completion_admission(runner, event))
            await asyncio.wait_for(started.wait(), 10)
            assert not expired.is_set(), 'detached lookup blocked the gateway on the database lock'
            assert not pending.done()
            release.set()
            receipt = await asyncio.wait_for(pending, 10)
        finally:
            release.set()
            await holder
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
    else:
        receipt = await completion_admission(runner, event)

    assert receipt['admission_id'] == rows[0]['admission_id']
    assert receipt['target_session_id'] == ref.session_id
