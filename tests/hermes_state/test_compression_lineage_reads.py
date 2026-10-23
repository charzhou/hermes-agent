"""Compression identity reads stay independent of token accounting and preserve fork boundaries."""
from contextlib import closing
import threading

import pytest

from hermes_state import SessionDB


@pytest.mark.requires_wal
def test_lineage_does_not_wait_for_queued_token_accounting(tmp_path):
    with closing(SessionDB(tmp_path / "state.db")) as db:
        db.create_session("root", source="cli")
        db.end_session("root", "compression")
        db.create_session("tip", source="cli", parent_session_id="root")
        assert db.get_compression_lineage("tip") == ["root", "tip"]
        done, result = threading.Event(), {}

        def read_lineage():
            try:
                result["lineage"] = db.get_compression_lineage("tip")
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        reader = threading.Thread(target=read_lineage)
        with db._lock:
            db.queue_token_counts("tip", input_tokens=17, output_tokens=3)
            reader.start()
            completed_while_writer_blocked = done.wait(2)
        reader.join(10)
        assert completed_while_writer_blocked, "lineage waited for unrelated token accounting"
        assert "error" not in result
        assert result["lineage"] == ["root", "tip"]
        assert db.flush_token_counts()
        assert db.get_session("tip")["input_tokens"] == 17


def test_lineage_keeps_earliest_continuation_and_isolates_other_forks(tmp_path):
    with closing(SessionDB(tmp_path / "state.db")) as db:
        db.create_session("root", source="cli")
        db.end_session("root", "compression")
        for index, (sid, source, config) in enumerate([
            ("branch", "cli", {"_branched_from": "root"}),
            ("reset", "cli", {"_reset_from": "root"}),
            ("tool", "tool", None),
            ("first", "cli", {"_delegate_from": "another-parent"}),
            ("later", "cli", None),
        ]):
            db.create_session(sid, source=source, parent_session_id="root", model_config=config)
            db._conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (index, sid))
        db.end_session("first", "compression")
        db.create_session("tip", source="cli", parent_session_id="first")
        db._conn.commit()

        for sid in ("root", "first", "tip"):
            assert db.get_compression_lineage(sid) == ["root", "first", "tip"]
        for sid in ("branch", "reset", "tool", "later"):
            assert db.get_compression_lineage(sid) == [sid]
        assert db.get_compression_lineage("missing") == []
