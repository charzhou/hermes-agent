"""Sealed core/plugin builds validate the combined lock, not the core seed."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys

import pytest

from tests.pm._fixtures import _run, _wheel


@pytest.mark.parametrize("plugin_test_group", [False, True])
def test_plugin_build_checks_resolved_override_and_keeps_its_lock(tmp_path, monkeypatch, plugin_test_group):
    from pm.environment import managed_environment
    from pm.operations import build_environment
    from pm.package import InstallError
    from pm.pip_check import overridden_versions

    uv = shutil.which("uv")
    assert uv, "plugin construction requires real uv"
    monkeypatch.setattr("pm._uv._toolchain", lambda **kwargs: (Path(uv), Path(sys.executable)))
    source, plugin, wheels = (tmp_path / name for name in ("core", "plugin", "wheels"))
    source.mkdir()
    plugin.mkdir()
    wheels.mkdir()
    _wheel(wheels, "base_dep")
    _wheel(wheels, "shared_dep", "2.0")
    (source / "pyproject.toml").write_text(
        '[project]\nname="hermes-agent"\nversion="1"\nrequires-python=">=3.11"\n'
        'dependencies=["base-dep==1.0"]\n'
        '[tool.uv]\npackage=false\nno-index=true\noverride-dependencies=["shared-dep==2.0"]\n'
        f'find-links=[{json.dumps(wheels.as_posix())}]\n', encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("UV_", "PYTHON")) and key != "VIRTUAL_ENV"}
    env.update(HOME=str(home), USERPROFILE=str(home), HERMES_HOME=str(home / ".hermes"),
               UV_CACHE_DIR=str(tmp_path / "cache"), UV_PYTHON=sys.executable, UV_OFFLINE="1")
    _run([uv, "lock", "--python", sys.executable], cwd=source, env=env)
    seed = (source / "uv.lock").read_bytes()
    assert overridden_versions(source / "uv.lock") == {}

    # The plugin brings an overridden dependency absent from the core graph.
    _wheel(wheels, "plugin_dep", requirements=("shared-dep==1.0",))
    (plugin / "plugin.yaml").write_text(
        'name: plugin\npython_dependencies: ["plugin-dep==1.0"]\n', encoding="utf-8",
    )
    project = plugin / "pyproject.toml"
    if plugin_test_group:
        # Honcho's development suite references another Hermes checkout; runtime builds
        # must neither resolve that source nor install the plugin's test tools.
        project.write_text(
            '[project]\nname="fixture-plugin"\nversion="1"\n'
            'dependencies=["plugin-dep==1.0"]\n'
            '[dependency-groups]\ntest=["hermes-agent @ git+https://example.invalid/hermes", "test-only==1.0"]\n'
            '[tool.uv]\npackage=false\n', encoding="utf-8")
        original_project = project.read_bytes()
    out = tmp_path / "sealed environment"
    executable = build_environment(
        source=source, plugins=[plugin], out=out, python=Path(sys.executable),
        cache=tmp_path / "cache", env=env, offline=True, sealed=True,
        no_install_project=True, explicit=True,
    )
    assert _run([str(executable), "-I", "-c",
                 "import base_dep, plugin_dep, shared_dep; print(shared_dep.__version__)"],
                cwd=tmp_path, env=env) == "2.0"
    assert overridden_versions(out / "uv.lock") == {"shared-dep": {"2.0"}}
    assert (source / "uv.lock").read_bytes() == seed
    assert not list(out.rglob("__editable__*.pth"))
    if plugin_test_group:
        assert project.read_bytes() == original_project
        assert _run([str(executable), "-I", "-c",
                     "import importlib.util; print(importlib.util.find_spec('test_only'))"],
                    cwd=tmp_path, env=env) == "None"

    environment = managed_environment(out, python=Path(sys.executable), cache=tmp_path / "cache",
                                      env=env, offline=True, explicit=True)
    environment.check(out / "uv.lock")
    with pytest.raises(InstallError, match="dependency validation failed"):
        environment.check(source / "uv.lock")
