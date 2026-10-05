"""Sealed payloads report bundled plugin dependencies from their shipped venv."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _payload(tmp_path: Path, *, version: str) -> tuple[Path, Path]:
    root = tmp_path / "payload" / "hermes-agent"
    plugin = root / "plugins" / "memory" / "honcho"
    plugin.mkdir(parents=True)
    (plugin / "plugin.yaml").write_text("name: honcho\nversion: '1'\n")
    (plugin / "pyproject.toml").write_text(
        '[project]\nname="hermes-plugin-honcho"\nversion="1"\n'
        'dependencies=["honcho-ai>=2.2.0,<3"]\n', encoding="utf-8")
    environment = root.parent / ".venv"
    site = environment / "lib" / "python3.14" / "site-packages"
    site.mkdir(parents=True)
    dist = site / "honcho_ai-2.5.1.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: honcho-ai\nVersion: {version}\n", encoding="utf-8")
    (environment / "pyvenv.cfg").write_text("home = /usr/local\n", encoding="utf-8")
    (root.parent / "manifest.json").write_text(json.dumps({"repo": "hermes-agent", "venv": ".venv"}))
    return root, plugin


@pytest.mark.parametrize(("version", "expected"), [("2.5.1", True), ("1.0.0", False)])
def test_sealed_bundled_plugin_readiness_uses_payload_environment(tmp_path, monkeypatch, version, expected):
    root, plugin = _payload(tmp_path, version=version)
    import pm.install as install
    from pm.plugin_inputs import Candidates

    monkeypatch.setattr(install.paths, "repo_root", lambda: root)
    monkeypatch.setattr(install.paths, "store_root", lambda: root.parent / "tools")
    assert install.venv_is_current(plugins=Candidates([plugin]), project_root=root) is expected
