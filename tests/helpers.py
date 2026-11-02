"""Shared helpers: run the kit CLI and the applied project's check, through their public interfaces."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
KIT_PY = KIT_ROOT / "kit.py"


def run_kit(*args, cwd=None):
    return subprocess.run(
        [sys.executable, str(KIT_PY), *map(str, args)],
        capture_output=True, text=True, cwd=cwd,
    )


def new_project():
    """A fresh temp git repo. Returns (TemporaryDirectory, Path)."""
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return tmp, path


def run_check(project):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        ["bash", "scripts/check.sh"], capture_output=True, text=True, cwd=project, env=env,
    )


def git_config(project, key):
    out = subprocess.run(
        ["git", "config", "--get", key], capture_output=True, text=True, cwd=project,
    )
    return out.stdout.strip()


def make_layers(root, spec):
    """Write a fake layers dir. spec: {layer: {relpath: content}} (relpaths under the layer dir)."""
    for layer, files in spec.items():
        for rel, content in files.items():
            p = Path(root) / layer / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
