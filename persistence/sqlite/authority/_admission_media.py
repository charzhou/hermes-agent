"""The admission ledger's media reads and writes: the held paths, the transcript mentions, the settled media lists
and the durable retirement candidates. File checks and unlinks stay with the owner, after these return.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from persistence.sqlite._state.keys import prefix_range
from session_rules.media import hosted_document_references

__all__ = [
    "drop_retired_candidates",
    "history_mentions",
    "record_retired_media",
    "retired_candidates",
    "settled_media",
    "unheld_media",
]


# The paths still held: each live admission's media and hosted documents, and every API turn's images.
def _held_media_paths(conn, media_root):
    # Project only references, not potentially large inline-image/history payloads; a live hosted
    # row's prompt is projected too, because its documents are held by text, not by a reference.
    rows = conn.execute("""SELECT status, request_id, json_extract(payload_json,
            '$.attachments_v1.media', '$.native_text_v1.media', '$.api_turn_v1.media'),
            CASE WHEN status!='terminal' AND request_id LIKE 'hosted:%'
                 THEN json_extract(payload_json, '$.text') END
            FROM session_admissions WHERE status!='terminal'
            OR json_type(payload_json, '$.api_turn_v1.media') IS NOT NULL""").fetchall()
    held = set()
    for status, request_id, encoded, text in rows:
        attachments, native, api = json.loads(encoded)
        # API images remain canonical history context after the turn completes.
        references = list(api or ())
        if status != 'terminal':
            references.extend(attachments or ())
            references.extend(native or ())
            references.extend(hosted_document_references(request_id, {'text': text}, media_root))
        held.update(reference['path'] for reference in references)
    return held


_HISTORY_BATCH_SIZE = 64
_HISTORY_DIGEST = re.compile(r'[0-9a-f]{64}(?=[/\\])')


def _history_needles(references):
    """Index raw and JSON-escaped tails by digest, including agent-visible path spellings."""
    needles = {}
    for reference in references:
        raw = reference['path']
        tails = {reference['sha256'] + sep + Path(raw).name for sep in ('/', '\\', '\\\\')}
        tails |= {json.dumps(tail)[1:-1] for tail in tails}
        needles.setdefault(reference['sha256'], []).extend((tail, raw) for tail in tails)
    return needles


def _history_mentions(contents, needles):
    """Scan each message once, testing only candidates whose digest occurs in its text."""
    mentioned = set()
    for content in contents:
        text = content.decode('utf-8', 'replace') if isinstance(content, bytes) else str(content)
        for match in _HISTORY_DIGEST.finditer(text):
            mentioned.update(raw for needle, raw in needles.get(match.group(), ())
                             if text.startswith(needle, match.start()))
    return mentioned


def history_mentions(holder, references):
    """Release even the fallback writer lock between bounded reads; match text off-lock.

    New transcript rows are checked again in the collection transaction. Deletions during
    this pass can only retain extra files, and a copied/branched row gets a new message id.
    """
    needles = _history_needles(references)
    with holder._read_ctx() as conn:
        last_id = conn.execute('SELECT coalesce(max(id), 0) FROM messages').fetchone()[0]
    seen, after_id = set(), 0
    wanted = {raw for entries in needles.values() for _, raw in entries}
    while after_id < last_id:
        with holder._read_ctx() as conn:
            rows = conn.execute('SELECT id, content FROM messages WHERE id>? AND id<=? ORDER BY id LIMIT ?',
                                (after_id, last_id, _HISTORY_BATCH_SIZE)).fetchall()
        if not rows:
            break
        after_id = rows[-1][0]
        seen.update(_history_mentions((row[1] for row in rows), needles))
        if seen >= wanted:
            break
    return last_id, seen


def unheld_media(holder, references, *, after_id, retain_history, media_root):
    """``(candidates, held)`` under the writer: a new admission cannot commit while the held set is computed."""
    needles = _history_needles(references) if retain_history else {}

    def collect(conn):
        held = _held_media_paths(conn, media_root)
        if retain_history:
            rows = conn.execute('SELECT content FROM messages WHERE id>? ORDER BY id LIMIT ?',
                                (after_id, _HISTORY_BATCH_SIZE + 1)).fetchall()
            if len(rows) > _HISTORY_BATCH_SIZE:
                return [], held  # A busy transcript defers collection without scanning under the writer lock.
            held |= _history_mentions((row[0] for row in rows), needles)
        return [reference for reference in references if reference['path'] not in held], held
    return holder._execute_write(collect)


# Each settled admission's attachment and native media, one list per row.
def settled_media(holder):
    with holder._read_ctx() as conn:
        rows = conn.execute("""SELECT json_extract(payload_json, '$.attachments_v1.media', '$.native_text_v1.media')
            FROM session_admissions WHERE status='terminal' AND (json_type(payload_json, '$.attachments_v1.media')
            IS NOT NULL OR json_type(payload_json, '$.native_text_v1.media') IS NOT NULL)""").fetchall()
    media = []
    for (encoded,) in rows:
        attachments, native = json.loads(encoded)
        media.append((attachments or []) + (native or []))
    return media


# Records the references as durable retirement candidates, read back by ``retired_candidates``.
def record_retired_media(holder, references):
    from ..conversation._store.media import retire_media
    # No request id names a hosted prompt, so the media root is never read.
    holder._execute_write(lambda conn: retire_media(conn, {'attachments_v1': {'media': list(references)}}, media_root=None))


def retired_candidates(holder, kind):
    from ..conversation._store.media import PREFIX, SPILLOVER_PREFIX
    with holder._read_ctx() as conn:
        # Primary-key range, not LIKE: the common no-candidate case costs one index probe.
        rows = conn.execute('SELECT key,value FROM state_meta WHERE key>=? AND key<?',
                            prefix_range(PREFIX if kind == 'media' else SPILLOVER_PREFIX)).fetchall()
    return [(key, json.loads(raw)) for key, raw in rows]


def drop_retired_candidates(holder, keys):
    done = [(key,) for key in keys]
    holder._execute_write(lambda conn: conn.executemany('DELETE FROM state_meta WHERE key=?', done))
