"""Targeted runtime-ledger reads for trusted background notifications."""

import json


_AUTOMATION_ROWS = """(json_type(payload_json, '$.local_automation_v1') = 'object'
    OR json_type(payload_json, '$.native_text_v1.automation') = 'object')"""
_DESCRIPTOR = """coalesce(nullif(json_extract(payload_json, '$.local_automation_v1'), '{}'),
    json_extract(payload_json, '$.native_text_v1.automation'), '{}')"""
_IDENTITY_MATCH = f"""CASE WHEN json_type({_DESCRIPTOR}, '$.identities') IS NULL
    THEN json_extract({_DESCRIPTOR}, '$.identity') = :identity
    ELSE EXISTS (SELECT 1 FROM json_each({_DESCRIPTOR}, '$.identities') WHERE value = :identity) END"""


def latest_native_envelope(db, *, session_id: str) -> dict | None:
    """The latest native input's envelope, if it carries connector provenance.

    Each status uses the existing (target, status, seq) index backwards. Selecting
    those four candidates avoids sorting and decoding the session's full history.
    A later native envelope without provenance still masks an older usable one.
    """
    candidates = ' UNION ALL '.join(
        "SELECT * FROM (SELECT seq, payload_json FROM session_admissions "
        f"WHERE target_session_id=? AND status='{status}' "
        "AND json_type(payload_json, '$.native_text_v1') IS NOT NULL ORDER BY seq DESC LIMIT 1)"
        for status in ('queued', 'started', 'unknown', 'terminal'))
    with db._read_ctx() as conn:
        row = conn.execute(
            "SELECT json_extract(payload_json, '$.native_text_v1') FROM (" + candidates
            + ") ORDER BY seq DESC LIMIT 1", (session_id,) * 4).fetchone()
    envelope = json.loads(row[0]) if row is not None and row[0] is not None else None
    return envelope if isinstance(envelope, dict) and 'provenance' in envelope else None


def find_automation_admission(db, *, session_id: str, identity: str, principal_id: str | None = None) -> dict | None:
    """First admission carrying this automation identity, including a batch member.

    An exact producer request uses the unique admission key. The partial automation
    index bounds the fallback to descriptors, including other producers, so ordinary
    conversation history is never decoded just to discover a new completion.
    """
    from hermes_state_runtime import _row

    with db._read_ctx() as conn:
        direct = None
        if principal_id is not None:
            direct = _row(conn.execute(
                "SELECT * FROM session_admissions WHERE principal_id=? AND target_session_id=? AND request_id=?",
                (principal_id, session_id, identity)).fetchone())
            if direct is not None:
                descriptor = (direct['payload'].get('local_automation_v1')
                              or direct['payload'].get('native_text_v1', {}).get('automation', {}))
                if identity not in descriptor.get('identities', [descriptor.get('identity')]):
                    direct = None
        # A batched or differently keyed descriptor can precede the direct request.
        earlier = ' AND seq < :before' if direct is not None else ''
        row = conn.execute(
            f"SELECT * FROM session_admissions WHERE target_session_id=:session_id AND {_AUTOMATION_ROWS}"
            f"{earlier} AND {_IDENTITY_MATCH} ORDER BY seq LIMIT 1",
            {'session_id': session_id, 'identity': identity, 'before': direct['seq'] if direct else None},
        ).fetchone()
    return _row(row) if row is not None else direct
