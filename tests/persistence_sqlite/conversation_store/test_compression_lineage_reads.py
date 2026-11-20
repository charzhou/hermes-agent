"""Compression identity reads stay independent of token accounting and preserve fork boundaries."""
import threading

import pytest

from persistence.conversation.usage import TokenCounts, UsageDelta
from tests.fixtures.storage_seed import seed_session


@pytest.mark.requires_wal
def test_lineage_does_not_wait_for_queued_token_accounting(backend, scope_a):
    store = backend.open(scope_a)
    db = store.holder()
    seed_session(store, "root", source="cli")
    store.sessions.end("root", "compression")
    seed_session(store, "tip", source="cli", parent_session_id="root")
    assert store.lineage.lineage("tip") == ("root", "tip")
    done, result = threading.Event(), {}

    def read_lineage():
        try:
            result["lineage"] = store.lineage.lineage("tip")
        except Exception as exc:
            result["error"] = exc
        finally:
            done.set()

    reader = threading.Thread(target=read_lineage)
    with db._lock:
        store.usage.queue_usage("tip", UsageDelta(tokens=TokenCounts(input_tokens=17, output_tokens=3)))
        reader.start()
        completed_while_writer_blocked = done.wait(2)
    reader.join(10)
    assert completed_while_writer_blocked, "lineage waited for unrelated token accounting"
    assert "error" not in result
    assert result["lineage"] == ("root", "tip")
    assert store.usage.flush_usage()
    assert store.sessions.get("tip").input_tokens == 17


def test_lineage_keeps_earliest_continuation_and_isolates_other_forks(backend, scope_a):
    store = backend.open(scope_a)
    db = store.holder()
    seed_session(store, "root", source="cli")
    store.sessions.end("root", "compression")
    for index, (sid, source, config) in enumerate([
        ("branch", "cli", {"_branched_from": "root"}),
        ("reset", "cli", {"_reset_from": "root"}),
        ("tool", "tool", None),
        ("first", "cli", {"_delegate_from": "another-parent"}),
        ("later", "cli", None),
    ]):
        seed_session(store, sid, source=source, parent_session_id="root", model_config=config)
        db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (index, sid))
    store.sessions.end("first", "compression")
    seed_session(store, "tip", source="cli", parent_session_id="first")
    db._conn.commit()

    for sid in ("root", "first", "tip"):
        assert store.lineage.lineage(sid) == ("root", "first", "tip")
    for sid in ("branch", "reset", "tool", "later"):
        assert store.lineage.lineage(sid) == (sid,)
    assert store.lineage.lineage("missing") == ()
