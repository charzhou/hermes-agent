"""Feishu token decoding and persisted mention observation compatibility."""

import json

import pytest

from plugins.platforms.feishu import adapter_mentions


@pytest.mark.parametrize("encoded", ["A", "_w", "é"])
def test_malformed_native_token_is_ignored(encoded):
    assert adapter_mentions._decode_native_at_token(encoded) == ""


def test_unexpected_token_decoder_failure_propagates(monkeypatch):
    def broken_decoder(_encoded):
        raise RuntimeError("unexpected decoder failure")

    monkeypatch.setattr(adapter_mentions.base64, "urlsafe_b64decode", broken_decoder)
    with pytest.raises(RuntimeError, match="unexpected decoder failure"):
        adapter_mentions._decode_native_at_token("b3VfYWxleA")


@pytest.mark.parametrize("legacy_entry", [" ou_legacy ", {"open_id": " ou_legacy "},
                                         {"open_ids": [" ou_legacy ", ""]}])
def test_persisted_registry_preserves_migration_and_ambiguous_observations(
    tmp_path, monkeypatch, legacy_entry,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(adapter_mentions.time, "time", lambda: 1000.0)
    (tmp_path / "feishu_mention_targets.json").write_text(json.dumps({
        "version": 1,
        "chats": {
            "oc_chat": {
                "updated_at": "600",
                "targets": {
                    "Legacy": legacy_entry,
                    "Conflict": {"updated_at": "invalid", "open_ids": ["ou_a", "ou_b"]},
                    "Current": {"updated_at": "500", "observations": {
                        "ou_one": 0, "ou_two": "750", "ou_bad": "invalid", "": 800,
                    }},
                    "Empty": {},
                    " ": "ou_skip",
                },
            },
            "oc_without_timestamp": {"Fresh": "ou_fresh"},
            "oc_invalid": {"targets": []},
        },
    }), encoding="utf-8")

    mixin = adapter_mentions.FeishuMentionMixin()
    mixin._init_mention_registry()

    assert mixin._mention_registry == {
        "version": adapter_mentions._FEISHU_MENTION_REGISTRY_VERSION,
        "chats": {
            "oc_chat": {
                "updated_at": 600.0,
                "targets": {
                    "Legacy": {"observations": {"ou_legacy": 600.0}},
                    "Conflict": {"observations": {"ou_a": 600.0, "ou_b": 600.0}},
                    "Current": {"observations": {"ou_one": 500.0, "ou_two": 750.0}},
                },
            },
            "oc_without_timestamp": {
                "updated_at": 1000.0,
                "targets": {"Fresh": {"observations": {"ou_fresh": 1000.0}}},
            },
        },
    }
    assert mixin._mention_targets_for_chat("oc_chat") == {"Legacy": "ou_legacy"}
    assert mixin._mention_targets_for_chat("oc_without_timestamp") == {"Fresh": "ou_fresh"}
