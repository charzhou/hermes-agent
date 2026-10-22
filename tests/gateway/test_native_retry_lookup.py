"""Native retry compatibility keeps an exact fingerprint without blocking the gateway loop."""
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import threading
import weakref

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from gateway.session_authority import initialize_session_authority
from gateway.session_envelope import prepare_native, snapshot_native
from gateway.session_ingress_context import native_callback, register_transport_home
from hermes_constants import get_hermes_home
from hermes_state_runtime import admit_session_input, cancel_session_input


class _Adapter:
    pass


async def _legacy_retry(tmp_path, monkeypatch):
    monkeypatch.setenv('TELEGRAM_ALLOWED_USERS', 'owner')
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    runner.adapters = {Platform.TELEGRAM: _Adapter()}
    authority = await initialize_session_authority(runner, profile_id='default', instance_id='retry')
    register_transport_home(runner, None, get_hermes_home())
    event = MessageEvent(text='original input', message_id='original', source=SessionSource(
        platform=Platform.TELEGRAM, chat_id='chat', user_id='owner', chat_type='dm'))
    event.source._transport_adapter_ref = weakref.ref(runner.adapters[Platform.TELEGRAM])
    ref = authority.register(event.source)
    legacy = snapshot_native(runner, event)
    source = event.source
    principal = 'messaging:' + json.dumps([source.profile, source.platform.value, source.chat_id,
                                         source.thread_id, source.user_id], separators=(',', ':'))
    row = admit_session_input(authority.db, epoch=authority.epoch, principal_id=principal,
                              session_id=ref.session_id, request_id=event.message_id, payload=legacy)
    cancel_session_input(authority.db, epoch=authority.epoch, admission_id=row['admission_id'])
    return authority, event, legacy, principal, ref.session_id


@pytest.mark.asyncio
async def test_legacy_retry_waits_for_database_without_stalling_gateway_loop(tmp_path, monkeypatch):
    authority, event, legacy, _, _ = await _legacy_retry(tmp_path, monkeypatch)
    db = authority.db
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
        with native_callback(authority.runner, event, get_hermes_home()):
            pending = asyncio.create_task(prepare_native(authority.runner, event))
        await asyncio.wait_for(read_started.wait(), 10)
        assert not expired.is_set(), 'native retry held the event loop until the database lock expired'
        assert not pending.done()
        release.set()
        assert await asyncio.wait_for(pending, 10) == legacy
    finally:
        release.set()
        await holder
        if pending is not None:
            await pending
        db.close()


@pytest.mark.asyncio
async def test_legacy_retry_cost_is_independent_of_unrelated_admissions(tmp_path, monkeypatch):
    authority, event, legacy, principal, session_id = await _legacy_retry(tmp_path, monkeypatch)
    db = authority.db
    db._wal_active = False

    async def measured_retry(candidate):
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1
            return 0

        db._conn.set_progress_handler(progress, 10)
        try:
            with native_callback(authority.runner, candidate, get_hermes_home()):
                payload = await prepare_native(authority.runner, candidate)
        finally:
            db._conn.set_progress_handler(None, 0)
        return payload, ticks

    try:
        payload, small = await measured_retry(event)
        assert payload == legacy
        db._execute_write(lambda conn: conn.executemany(
            '''INSERT INTO session_admissions(admission_id, request_id, principal_id, target_session_id,
                   lineage_json, payload_json, payload_digest, intent, status, outcome)
               VALUES(?,?,?,?,?,?,'unrelated','queue','terminal','cancelled')''',
            [(f'noise-{i}', f'noise-{i}', principal, session_id, json.dumps([session_id]), '{}')
             for i in range(2000)]))
        payload, large = await measured_retry(event)
        assert payload == legacy
        assert large < max(small, 1) * 4, (small, large)
        # Matching only the request ID must never discard a changed payload's provenance.
        changed, _ = await measured_retry(replace(event, text='changed input'))
        assert 'provenance' in changed['native_text_v1']
        fresh, _ = await measured_retry(replace(event, message_id='new-input'))
        assert 'provenance' in fresh['native_text_v1']
    finally:
        db.close()
