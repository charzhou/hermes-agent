"""Source-copy stamps must bind the resident manager before plugin builds."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.write_install_stamp import write_stamp
from tests.pm._fixtures import isolated_python as isolated_python


@pytest.mark.platforms("posix")
@pytest.mark.parametrize("ci_stamp", [True, False])
def test_plugin_build_resolves_resident_pm_before_application_exists(tmp_path, isolated_python, ci_stamp):
    from pm.environments import site_packages

    root = tmp_path / "image"
    runtime = root / "pm-runtime"
    runtime.mkdir(parents=True)
    (runtime / "pm-runtime.json").write_text(json.dumps({
        "python": sys._base_executable,
        "sitePackages": str(site_packages(isolated_python.parent.parent)),
    }))
    stamp = root / "install-stamp.json"
    original = write_stamp(stamp, source="ci", distribution="docker", base_version="0.19.0",
                           update_mechanism="external") if ci_stamp else {}
    assert "pmRuntime" not in original

    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    stage = dockerfile.read_text().split("FROM python_deps AS plugin_deps\n", 1)[1]
    stage = stage.split("FROM python_deps AS icons", 1)[0]
    # Execute the Dockerfile's own pre-install RUNs, so moving stamp binding
    # behind the first PM call regresses even if the final image binds it later.
    lines = iter(stage.splitlines())
    for line in lines:
        if not line.startswith("RUN "):
            continue
        command = line[4:]
        while command.endswith("\\"):
            command = command[:-1] + next(lines)
        if "bundle_memory_plugin" in command:
            break
        subprocess.run(["/bin/sh", "-ec", command.replace("/opt/hermes", str(root))],
                       cwd=root, check=True, capture_output=True, text=True, timeout=30)

    bound = json.loads(stamp.read_text())
    assert all(bound[key] == value for key, value in original.items())
    assert not (root / ".venv").exists()
    env = {**os.environ, "HERMES_INSTALL_ROOT": str(root)}
    # Use the real selector in a fresh process, not a patched PM worker fixture.
    selector = subprocess.run([sys.executable, "-c", """
import json,sys
from pathlib import Path
from pm.runtime import runtime_command
print(json.dumps(runtime_command(Path(sys.argv[1]))))
""", str(tmp_path / "probe.py")], env=env, capture_output=True, text=True, timeout=30)
    assert selector.returncode == 0, selector.stderr
    command = json.loads(selector.stdout)
    (tmp_path / "probe.py").write_text(
        "import importlib.util, packaging, tomli_w, truststore\n"
        "from ruamel.yaml import YAML\n"
        "assert importlib.util.find_spec('openai') is None\n"
        "print('resident manager ready')\n")
    probe = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "resident manager ready"
