"""Media history matching must not hold the shared database lock while scanning text."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

import pytest

from gateway import session_ingress_media as media
from persistence.sqlite.authority import _admission_media
from persistence_assembly import open_persistence, profile_scope
from tests.fixtures.storage_holder import raw_holder
from tests.fixtures.storage_seed import seed_messages, seed_session


@pytest.mark.parametrize('fallback', [False, True], ids=['read-pool', 'shared-connection'])
def test_history_matching_allows_writes_and_rechecks_new_references(tmp_path, monkeypatch, fallback):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    staged = tmp_path / 'report.pdf'
    staged.write_bytes(b'%PDF-1.4 retained')
    references = media.capture_native_media([staged])
    retained = Path(references[0]['path'])
    entered, resume = threading.Event(), threading.Event()
    original = _admission_media._history_mentions

    def paused(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert resume.wait(10), 'history scan was never released'
        return original(*args, **kwargs)

    with open_persistence(profile_scope(home=tmp_path)) as store:
        seed_session(store, 's', 'test')
        seed_messages(store, 's', {'role': 'user', 'content': 'unrelated history'})
        if fallback:
            monkeypatch.setattr(raw_holder(store), '_checkout_read_conn', lambda: None)
        monkeypatch.setattr(_admission_media, '_history_mentions', paused)
        with ThreadPoolExecutor(max_workers=2) as workers:
            cleanup = workers.submit(media.release_unheld_media, store, references)
            try:
                assert entered.wait(5), 'cleanup never reached history matching'
                writer = workers.submit(seed_messages, store, 's', {'role': 'user', 'content': f'Open {retained}'})
                writer.result(timeout=3)
            finally:
                resume.set()
            assert cleanup.result(timeout=5) == 0
        assert retained.exists(), 'the concurrent transcript reference must retain the file'
        raw_holder(store)._execute_write(lambda conn: conn.execute('DELETE FROM messages'))
        assert media.release_unheld_media(store, references) == 1
        assert not retained.exists()


@pytest.mark.parametrize('escaped', [False, True], ids=['raw-path', 'json-path'])
def test_history_batches_preserve_reference_spellings(tmp_path, monkeypatch, escaped):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    staged = tmp_path / 'résumé "draft".pdf'
    staged.write_bytes(b'%PDF-1.4 retained')
    references = media.capture_native_media([staged])
    retained = Path(references[0]['path'])
    spelling = str(retained).replace('/', chr(92))
    content = json.dumps(spelling) if escaped else spelling
    with open_persistence(profile_scope(home=tmp_path)) as store:
        seed_session(store, 's', 'test')
        seed_messages(store, 's', *[{'role': 'user', 'content': 'unrelated'} for _ in range(150)])
        seed_messages(store, 's', {'role': 'user', 'content': content})
        monkeypatch.setattr(raw_holder(store), '_checkout_read_conn', lambda: None)
        assert media.release_unheld_media(store, references) == 0
        assert retained.exists()
