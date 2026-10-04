"""Bake a catalog-pinned memory plugin into a fork image without runtime installation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from hermes_cli.plugin_catalog import get_catalog_entry
from hermes_cli.plugins_cmd import _resolve_subdir_within
from hermes_cli.plugins_cmd_git import _clone_plugin_repo
from pm.plugin_declarations import read_python_declaration


def bundle_memory_plugin(root: Path, name: str) -> Path:
    entry = get_catalog_entry(name)
    if entry is None or entry.category != "memory":
        raise ValueError(f"No memory plugin in the shipped catalog: {name}")
    target = root / "plugins" / "memory" / name
    if target.exists():
        raise FileExistsError(target)
    with tempfile.TemporaryDirectory(prefix=f"bundle-{name}-") as temporary:
        clone = Path(temporary) / "repo"
        revision = _clone_plugin_repo(clone, entry.repo, entry.sha, entry.subdir or None)
        if revision != entry.sha:
            raise ValueError(f"Catalog revision mismatch for {name}: {revision}")
        source = _resolve_subdir_within(clone, entry.subdir) if entry.subdir else clone
        declaration = read_python_declaration(source)
        if declaration.manifest.get("name") != name or not (source / "__init__.py").is_file():
            raise ValueError(f"Catalog source is not a directory memory plugin: {name}")
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(
            ".git", ".venv", "venv", "__pycache__", "*.pyc", "*.egg-info", "node_modules"))
    (target / "catalog-pin.json").write_text(json.dumps({
        "name": name, "repo": entry.repo, "sha": revision, "subdir": entry.subdir,
    }, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Bundled memory plugin {name} @ {revision}", flush=True)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--source", type=Path, default=Path("/opt/hermes"))
    args = parser.parse_args()
    bundle_memory_plugin(args.source, args.name)


if __name__ == "__main__":
    main()
