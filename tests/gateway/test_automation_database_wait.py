"""A producer's database wait must not stop the gateway or change its conversation."""
import asyncio
from contextlib import contextmanager
import threading

import pytest

from gateway.platforms.event import MessageEvent
from hermes_state_runtime import RuntimeStoreError, get_session_admission, list_session_admissions
from tests.gateway.test_automation_compression_media import _admit_human, _discord_owner


async def _native_notice(tmp_path, monkeypatch):
    runner, adapter, authority, source = await _discord_owner(tmp_path, monkeypatch)
    receipt = await _admit_human(
        runner, authority, MessageEvent(text='human', source=source, message_id='human'))
    route = runner.session_store._generate_session_key(source)
    event = MessageEvent(text='process finished', source=source, internal=True,
        metadata={'gateway_session_key': route, 'gateway_session_id': receipt.ref.session_id})
    return runner, adapter, authority, event


@pytest.mark.asyncio
@pytest.mark.parametrize('delegated', [False, True])
async def test_native_notice_database_wait_keeps_gateway_responsive(tmp_path, monkeypatch, delegated):
    _, adapter, authority, event = await _native_notice(tmp_path, monkeypatch)
    db = authority.db
    owner = event.metadata['gateway_session_id']
    if delegated:
        child = owner + '-delegate'
        db.create_session(child, source='subagent', parent_session_id=owner, model_config={'_delegate_from': owner})
        event.metadata['gateway_session_id'] = child
    db._wal_active = False  # Exercise the supported writer-connection read fallback.
    held, release, expired = threading.Event(), threading.Event(), threading.Event()
    read_started = asyncio.Event()
    loop, original_read = asyncio.get_running_loop(), db._read_ctx

    @contextmanager
    def observed_read(*args, **kwargs):
        loop.call_soon_threadsafe(read_started.set)
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
        pending = asyncio.create_task(authority.admit_automation(adapter, event, 'finished'))
        await asyncio.wait_for(read_started.wait(), 10)
        assert not expired.is_set(), 'producer admission blocked the gateway until the database lock expired'
        assert not pending.done()
        release.set()
        receipt = await asyncio.wait_for(pending, 10)
        row = get_session_admission(db, admission_id=receipt.admission_id)
        assert row['status'] == 'queued'
        assert row['target_session_id'] == owner
        assert row['payload']['text'] == 'process finished'
    finally:
        release.set()
        await holder
        if pending is not None:
            await pending
        db.close()


@pytest.mark.asyncio
async def test_notice_waiting_for_database_never_enters_a_reset_conversation(tmp_path, monkeypatch):
    runner, adapter, authority, event = await _native_notice(tmp_path, monkeypatch)
    db = authority.db
    original_session = event.metadata['gateway_session_id']
    route = event.metadata['gateway_session_key']
    read_started = asyncio.Event()
    release, expired = threading.Event(), threading.Event()
    loop, original_read = asyncio.get_running_loop(), db._read_ctx
    first_read = True

    @contextmanager
    def delayed_read(*args, **kwargs):
        nonlocal first_read
        if first_read:
            first_read = False
            loop.call_soon_threadsafe(read_started.set)
            if not release.wait(5):
                expired.set()
        with original_read(*args, **kwargs) as conn:
            yield conn

    monkeypatch.setattr(db, '_read_ctx', delayed_read)
    pending = asyncio.create_task(authority.admit_automation(adapter, event, 'finished'))
    try:
        await asyncio.wait_for(read_started.wait(), 10)
        assert not expired.is_set(), 'producer read did not yield while its database operation waited'
        replacement = await asyncio.to_thread(runner.session_store.reset_session, route)
        assert replacement.session_id != original_session
        release.set()
        result = (await asyncio.wait_for(asyncio.gather(pending, return_exceptions=True), 10))[0]
        if isinstance(result, RuntimeStoreError):
            assert result.reason in {'admission_conflict', 'stale_generation'}
        else:
            assert not isinstance(result, BaseException)
            assert result.ref.session_id == original_session
        assert list_session_admissions(db, session_id=replacement.session_id, pending_only=False) == []
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)
        db.close()


@pytest.mark.asyncio
async def test_later_human_input_cannot_overtake_a_notice_waiting_for_database(tmp_path, monkeypatch):
    import hermes_state_automation

    runner, adapter, authority, event = await _native_notice(tmp_path, monkeypatch)
    db = authority.db
    read_started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_read = hermes_state_automation.latest_native_envelope
    first_read = True

    def delayed_read(*args, **kwargs):
        nonlocal first_read
        if first_read:
            first_read = False
            loop.call_soon_threadsafe(read_started.set)
            assert release.wait(5), 'notice database read was never released'
        return original_read(*args, **kwargs)

    monkeypatch.setattr(hermes_state_automation, 'latest_native_envelope', delayed_read)
    first = asyncio.create_task(authority.admit_automation(adapter, event, 'finished'))
    second = None
    try:
        await asyncio.wait_for(read_started.wait(), 10)
        second = asyncio.create_task(_admit_human(runner, authority, MessageEvent(
            text='follow-up', source=event.source, message_id='follow-up')))
        done, _ = await asyncio.wait({second}, timeout=2.0)
        assert not done, 'later human input overtook the notice during its database read'
        release.set()
        first_receipt, second_receipt = await asyncio.wait_for(asyncio.gather(first, second), 10)
        assert first_receipt.sequence < second_receipt.sequence
    finally:
        release.set()
        await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)
        db.close()
