"""Catalog-pinned image plugins resolve through the real memory loader offline."""
from __future__ import annotations

import importlib.util
import os
import json
from pathlib import Path
import subprocess
import sys

import pytest

from hermes_cli.plugin_catalog import PluginCatalogEntry
from hermes_cli.plugins_cmd import _resolve_git_executable
from scripts.build import bundle_memory_plugin as builder
from tests.pm._fixtures import (
    _wheel,
    build_worker as build_worker,
    client as client,
    isolated_python as isolated_python,
)
import pm


def _catalog_fixture(tmp_path, monkeypatch, requirement: str) -> PluginCatalogEntry:
    git = _resolve_git_executable()
    assert git, "catalog bundle tests require Git"
    repo = tmp_path / "upstream"
    source = repo / "provider"
    source.mkdir(parents=True)
    (source / "plugin.yaml").write_text("name: bundle_fixture\nversion: '1'\n")
    (source / "pyproject.toml").write_text(
        '[project]\nname="bundle-fixture"\nversion="1"\ndependencies=['
        + json.dumps(requirement) + ']\n', encoding="utf-8")
    (source / "values.py").write_text("VALUE = 'catalog memory provider'\n")
    (source / "__init__.py").write_text(
        "from agent.memory_provider import MemoryProvider\n"
        "from .values import VALUE\n"
        "import bundle_dependency\n"
        "def register(ctx):\n"
        "    class Provider(MemoryProvider):\n"
        "        name = 'bundle_fixture'\n"
        "        def is_available(self): return True\n"
        "        def initialize(self, session_id, **kwargs): return None\n"
        "        def get_tool_schemas(self): return []\n"
        "    ctx.register_memory_provider(Provider())\n", encoding="utf-8")
    for args in (("init",), ("add", "."),
                 ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                  "commit", "-m", "Pinned provider")):
        subprocess.run([git, *args], cwd=repo, check=True, capture_output=True, timeout=30)
    pin = subprocess.check_output([git, "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    # A newer HEAD must never change the payload selected by the catalog.
    (source / "values.py").write_text("VALUE = 'unreviewed latest revision'\n")
    subprocess.run([git, "add", "."], cwd=repo, check=True, capture_output=True, timeout=30)
    subprocess.run([git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "-m", "Later revision"], cwd=repo,
                   check=True, capture_output=True, timeout=30)
    entry = PluginCatalogEntry(
        name="bundle_fixture", repo=str(repo), sha=pin, subdir="provider",
        description="Local memory plugin", maintainer="fixture", category="memory")
    monkeypatch.setattr(builder, "get_catalog_entry", lambda name: entry)
    return entry


def _core_fixture(image, tmp_path):
    image.mkdir()
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel(wheels, "bundle_core")
    _wheel(wheels, "bundle_leaf")
    _wheel(wheels, "bundle_dependency", requirements=["bundle-leaf==1.0"])
    (image / "pyproject.toml").write_text(
        '[project]\nname="bundle-core-app"\nversion="1"\n'
        'requires-python=">=3.11"\ndependencies=["bundle-core==1.0"]\n'
        '[tool.uv]\npackage=false\nno-index=true\n'
        f'find-links=[{json.dumps(wheels.as_posix())}]\n', encoding="utf-8")
    pm.lock_project(image, python=Path(sys.executable), offline=True, explicit=True)
    return (image / "uv.lock").read_bytes()


def test_pinned_bundle_installs_declared_dependency_and_loads_offline(tmp_path, monkeypatch, build_worker):
    assert importlib.util.find_spec("bundle_dependency") is None
    entry = _catalog_fixture(tmp_path, monkeypatch, "bundle-dependency==1.0")
    image = tmp_path / "image"
    locked = _core_fixture(image, tmp_path)
    target = builder.bundle_memory_plugin(image, entry.name)
    executable = pm.build_environment(
        source=image, out=tmp_path / "environment", plugins=[target],
        python=Path(sys.executable), offline=True, sealed=True,
        no_install_project=True, explicit=True)
    assert (image / "uv.lock").read_bytes() == locked
    assert (target / "values.py").read_text() == "VALUE = 'catalog memory provider'\n"
    assert json.loads((target / "catalog-pin.json").read_text())["sha"] == entry.sha
    assert not (target / ".git").exists()
    installed = subprocess.run([str(executable), "-I", "-c",
        "import bundle_dependency, bundle_leaf, bundle_core; print(bundle_dependency.__version__)"],
        text=True, capture_output=True, timeout=30)
    assert installed.returncode == 0, installed.stderr
    assert installed.stdout.strip() == "1.0"
    assert (executable.parent.parent / "uv.lock").is_file()

    # Load from a fresh profile after PM's build workspace has been removed.
    # The small output contains the newly installed SDK fixture; the host supplies
    # Hermes's real loader and its core dependencies without reinstalling them.
    from pm.environments import site_packages
    home = tmp_path / "fresh-profile"
    home.mkdir()
    environment = {**os.environ, "HERMES_HOME": str(home)}
    probe = subprocess.run([sys.executable, "-c", """
import sys, socket
from pathlib import Path
sys.path.insert(0, sys.argv[3])
def refuse_network(*args, **kwargs): raise AssertionError('unexpected runtime network')
socket.socket.connect = refuse_network
import plugins.memory as memory
from hermes_cli.memory_provider_migration import provider_present
memory._MEMORY_PLUGINS_DIR = Path(sys.argv[1])
assert provider_present('bundle_fixture', Path(sys.argv[2]))
provider = memory.load_memory_provider('bundle_fixture', register_skills=False)
assert provider is not None and provider.is_available()
assert not (Path(sys.argv[2]) / 'plugins').exists()
print('offline provider loaded')
""", str(image / "plugins/memory"), str(home), str(site_packages(executable.parent.parent))],
        env=environment, text=True, capture_output=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "offline provider loaded"


def test_plugin_dependency_conflict_preserves_core_lock_and_cleans_output(tmp_path, monkeypatch, build_worker):
    entry = _catalog_fixture(tmp_path, monkeypatch, "bundle-core==2.0")
    image = tmp_path / "image"
    locked = _core_fixture(image, tmp_path)
    _wheel(tmp_path / "wheels", "bundle_core", "2.0")
    target = builder.bundle_memory_plugin(image, entry.name)
    output = tmp_path / "environment"
    with pytest.raises(pm.InstallError):
        pm.build_environment(
            source=image, out=output, plugins=[target], python=Path(sys.executable),
            offline=True, sealed=True, no_install_project=True, explicit=True)
    assert (image / "uv.lock").read_bytes() == locked
    assert not output.exists()
