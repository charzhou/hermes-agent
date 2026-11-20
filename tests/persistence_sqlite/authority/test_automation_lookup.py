"""Notification lookups retain native provenance and automation identity ordering."""

import json

import pytest

from tests.fixtures.storage_seed import seed_session, settle_claim


@pytest.fixture
def admissions(store):
    seed_session(store, 'owned', 'test')
    seed_session(store, 'foreign', 'test')
    epoch = store.runtime.begin_epoch(instance_id='owner')

    def admit(request, payload, *, principal='human', session='owned', terminal=False):
        row, _ = store.admissions.admit(epoch=epoch, principal_id=principal,
                                        session_id=session, request_id=request, payload=payload)
        if terminal:
            claim = store.admissions.claim(epoch=epoch, session_id=session)
            assert claim.admission_id == row.admission_id
            settle_claim(store, claim)
        return row

    return store, admit


def test_latest_native_envelope_tracks_the_last_native_input(admissions):
    store, admit = admissions
    assert store.admissions.latest_native_envelope(session_id='owned') is None
    first = {'source': {'chat_id': '42'}, 'provenance': {'connector': 'discord'}, 'event': {'message_id': 'first'}}
    admit('first', {'text': 'hello', 'native_text_v1': first}, terminal=True)
    admit('foreign', {'native_text_v1': {'provenance': {'connector': 'foreign'}}}, session='foreign')
    admit('local', {'text': 'not native', 'local_automation_v1': {'identity': 'local'}})
    assert store.admissions.latest_native_envelope(session_id='owned') == first

    latest = {**first, 'event': {'message_id': 'completion'}, 'automation': {'identity': 'completion'}}
    admit('completion', {'text': 'done', 'native_text_v1': latest})
    assert store.admissions.latest_native_envelope(session_id='owned') == latest
    admit('unproven', {'native_text_v1': {'source': {'chat_id': '42'}}})
    assert store.admissions.latest_native_envelope(session_id='owned') is None


@pytest.mark.parametrize('descriptor_key', ['native_text_v1', 'local_automation_v1'])
@pytest.mark.parametrize('principal_id', [None, 'automation:route'])
def test_identity_lookup_keeps_first_batch_match_and_ignores_plain_request_ids(admissions, descriptor_key, principal_id):
    store, admit = admissions

    def payload(descriptor):
        value = {'automation': descriptor} if descriptor_key == 'native_text_v1' else descriptor
        return {descriptor_key: value, 'text': 'raw metadata stays intact'}

    admit('result', {'text': 'same request id is not an automation'})
    admit('foreign', payload({'identity': 'result'}), session='foreign')
    admit('overridden', payload({'identity': 'result', 'identities': ['other']}))
    assert store.admissions.find_automation_admission(session_id='owned', identity='result', principal_id=principal_id) is None
    first = admit('batch', payload({'identity': 'batch', 'identities': ['other', 'result']}), principal='other-producer')
    direct = admit('result', payload({'identity': 'result'}), principal='automation:route')
    assert store.admissions.find_automation_admission(session_id='owned', identity='result', principal_id=principal_id) == first
    assert store.admissions.find_automation_admission(session_id='owned', identity='missing', principal_id=principal_id) is None
    assert store.admissions.find_automation_admission(session_id='owned', identity='batch', principal_id=principal_id) is None
    assert direct.seq > first.seq


def test_notification_lookup_work_is_independent_of_human_history_growth(admissions):
    store, admit = admissions
    envelope = {'source': {'chat_id': '42', 'user_id': 'owner'},
                'provenance': {'connector': 'discord'}, 'event': {'message_id': 'human'}}
    first = admit('batch', {'native_text_v1': {**envelope, 'automation': {
        'identity': 'batch', 'identities': ['result']}}}, principal='other-producer', terminal=True)
    admit('result', {'native_text_v1': {**envelope, 'automation': {'identity': 'result'}}},
          principal='automation:route', terminal=True)
    admit('human', {'text': 'hello', 'native_text_v1': envelope}, terminal=True)
    db = store.holder()
    db._wal_active = False  # Measure the supported writer-connection read fallback.

    def measured(lookup):
        ticks = 0

        def progress():
            nonlocal ticks
            ticks += 1
            return 0

        db._conn.set_progress_handler(progress, 10)
        try:
            result = lookup()
        finally:
            db._conn.set_progress_handler(None, 0)
        return result, ticks

    def missing():
        return store.admissions.find_automation_admission(session_id='owned', identity='missing', principal_id='automation:route')

    value, small_native = measured(lambda: store.admissions.latest_native_envelope(session_id='owned'))
    assert value == envelope
    value, small_miss = measured(missing)
    assert value is None
    noise = json.dumps({'text': 'unrelated human message ' * 32, 'native_text_v1': envelope})
    db._execute_write(lambda conn: conn.executemany(
        '''INSERT INTO session_admissions(admission_id, request_id, principal_id, target_session_id,
               lineage_json, payload_json, payload_digest, intent, status, outcome)
           VALUES(?,?,?,'owned','["owned"]',?,'unrelated','queue','terminal','completed')''',
        [(f'noise-{i}', f'noise-{i}', 'human', noise) for i in range(2000)]))

    value, large_native = measured(lambda: store.admissions.latest_native_envelope(session_id='owned'))
    assert value == envelope
    value, large_miss = measured(missing)
    assert value is None
    assert large_native < max(small_native, 1) * 4, (small_native, large_native)
    assert large_miss < max(small_miss, 1) * 4, (small_miss, large_miss)
    matched = store.admissions.find_automation_admission(session_id='owned', identity='result', principal_id='automation:route')
    assert matched.admission_id == first.admission_id
    assert matched.payload == first.payload
