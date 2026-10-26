"""Thread labels and opening-event markers do not change a producer's conversation owner."""
from copy import deepcopy

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.run import GatewayRunner
from gateway.session_authority import SessionAuthority, initialize_session_authority
from gateway.session_identity import replace_source
from gateway.session_ingress_context import native_callback, register_transport_home
from hermes_state import SessionDB
from hermes_state_runtime import RuntimeStoreError, get_session_admission, list_session_admissions
from plugins.platforms.discord.adapter import DiscordAdapter


async def _renamed_thread(tmp_path, monkeypatch):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setenv('DISCORD_ALLOWED_USERS', '42')
    monkeypatch.setattr(SessionAuthority, '_schedule', lambda self, ref: None)
    runner = GatewayRunner(GatewayConfig(sessions_dir=tmp_path / 'sessions'))
    db = SessionDB(tmp_path / 'state.db')
    runner.session_store._db = runner._session_db = db
    authority = await initialize_session_authority(
        runner, profile_id='default', instance_id='thread-metadata', db=db)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token='fixture-token', typing_indicator=False))
    runner.adapters = {Platform.DISCORD: adapter}
    runner._wire_adapter_handlers(adapter)
    register_transport_home(runner, None, tmp_path)
    identity = dict(chat_id='4200', chat_type='thread', thread_id='4200', user_id='42',
                    user_name='human', guild_id='700', parent_chat_id='4100')
    opening = adapter.build_source(
        **identity, chat_name='Inspect the workspace', chat_topic='Original topic',
        message_id='human-1', auto_thread_created=True, auto_thread_initial_name='Inspect the workspace')
    current = adapter.build_source(
        **identity, chat_name='Workspace inspection', chat_topic='Updated topic', message_id='human-2')
    for source, text in ((opening, 'inspect the workspace'), (current, 'continue the inspection')):
        event = MessageEvent(text=text, source=source, message_id=source.message_id)
        with native_callback(runner, event, tmp_path):
            receipt = await authority.admit_native(event)
    route = runner.session_store._generate_session_key(current)
    entry = runner.session_store.lookup_by_session_key(route)
    return runner, authority, adapter, opening, current, entry, receipt


def _automation_rows(authority, session_id):
    return [row for row in list_session_admissions(authority.db, session_id=session_id, pending_only=False)
            if row['principal_id'].startswith('automation:')]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['completion', 'async_delegation'])
async def test_notice_after_thread_metadata_changes_keeps_owner_and_retry_identity(tmp_path, monkeypatch, kind):
    runner, authority, _adapter, opening, current, entry, latest = await _renamed_thread(tmp_path, monkeypatch)
    try:
        opening_origin = deepcopy(entry.origin.to_dict())
        assert opening_origin == opening.to_dict()
        assert opening_origin['chat_name'] != current.chat_name
        assert opening_origin['auto_thread_created'] is True
        expected_source = deepcopy(get_session_admission(
            authority.db, admission_id=latest.admission_id)['payload']['native_text_v1']['source'])
        expected_source.pop('message_id')
        notice = dict(type=kind, session_id='proc_thread', started_at=1.0, delegation_id='deleg_thread',
                      session_key=entry.session_key, parent_session_id=entry.session_id,
                      platform='discord', chat_type='thread', chat_id=current.chat_id,
                      thread_id=current.thread_id, user_id=current.user_id, message_id='human-1')

        assert await runner._inject_watch_notification('Inspection finished', notice, raise_not_accepted=True) is True
        admitted = _automation_rows(authority, latest.ref.session_id)
        assert len(admitted) == 1
        envelope = admitted[0]['payload']['native_text_v1']
        assert envelope['source'] == expected_source
        assert 'message_id' not in envelope['source']
        assert 'auto_thread_created' not in envelope['source']
        assert envelope['source']['chat_name'] == current.chat_name
        assert envelope['source']['chat_topic'] == current.chat_topic

        assert await runner._inject_watch_notification(
            'Inspection finished', deepcopy(notice), raise_not_accepted=True) is True
        assert [row['admission_id'] for row in _automation_rows(authority, latest.ref.session_id)] == [
            admitted[0]['admission_id']]
        assert entry.origin.to_dict() == opening_origin
    finally:
        authority.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_field', ['chat_id', 'thread_id', 'adapter'])
async def test_thread_notice_still_refuses_changed_routing_or_transport(tmp_path, monkeypatch, changed_field):
    _runner, authority, adapter, _opening, current, entry, latest = await _renamed_thread(tmp_path, monkeypatch)
    try:
        source = current
        if changed_field == 'adapter':
            adapter = DiscordAdapter(PlatformConfig(enabled=True, token='other-fixture-token', typing_indicator=False))
        else:
            source = replace_source(current, **{changed_field: '9999'})
        event = MessageEvent(text='Inspection finished', source=source, internal=True,
                             metadata={'gateway_session_key': entry.session_key,
                                       'gateway_session_id': entry.session_id})
        with pytest.raises(RuntimeStoreError, match='admission_conflict'):
            await authority.admit_automation(adapter, event, 'thread-notice')
        assert _automation_rows(authority, latest.ref.session_id) == []
    finally:
        authority.db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_field', ['scope_id', 'parent_chat_id', 'profile', 'prospective_thread_id'])
async def test_notice_uses_committed_source_when_producer_hints_drift(tmp_path, monkeypatch, changed_field):
    runner, authority, adapter, _opening, current, entry, latest = await _renamed_thread(tmp_path, monkeypatch)
    try:
        source = replace_source(current, **{changed_field: '9999'})
        assert runner.session_store._generate_session_key(source) == entry.session_key
        expected = deepcopy(get_session_admission(
            authority.db, admission_id=latest.admission_id)['payload']['native_text_v1']['source'])
        expected.pop('message_id')
        event = MessageEvent(text='Inspection finished', source=source, internal=True,
                             metadata={'gateway_session_key': entry.session_key,
                                       'gateway_session_id': entry.session_id})
        receipt = await authority.admit_automation(adapter, event, 'thread-notice')
        row = get_session_admission(authority.db, admission_id=receipt.admission_id)
        assert row['target_session_id'] == latest.ref.session_id
        assert row['payload']['native_text_v1']['source'] == expected
    finally:
        authority.db.close()
