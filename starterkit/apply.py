"""`kit.py apply`: copy layers into a project and write the .starter-kit.json record."""

import hashlib
import json
import subprocess
from pathlib import Path

MARKER = "# starter-kit: layer setup steps"
RECORD = ".starter-kit.json"


class KitError(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def resolve_layers(layers_dir: Path, names):
    ordered = ["base"] + [n for n in names if n != "base"]
    seen = []
    for n in ordered:
        if n not in seen:
            seen.append(n)
    for n in seen:
        if not (layers_dir / n).is_dir():
            raise KitError(f"unknown layer: {n}")
    return seen


def collect(layers_dir: Path, layers):
    """Return {relpath: bytes} for every file the layers write (later layers win)."""
    out = {}
    for name in layers:
        root = layers_dir / name
        files = root / "files"
        if files.is_dir():
            for f in sorted(files.rglob("*")):
                rel = f.relative_to(files)
                if "__pycache__" in rel.parts or f.suffix == ".pyc":
                    continue  # bytecode a hook run left in the layer
                if f.is_file():
                    out[rel.as_posix()] = (
                        f.read_bytes(),
                        f.stat().st_mode & 0o111,
                    )
        checks = root / "check.d"
        if checks.is_dir():
            for f in sorted(checks.iterdir()):
                if f.is_file():
                    out[f"scripts/check.d/{f.name}"] = (f.read_bytes(), 0o111)
    return out


def insert_ci_steps(layers_dir: Path, layers, out):
    steps = []
    for name in layers:
        f = layers_dir / name / "ci-steps.yml"
        if f.is_file():
            steps.append((name, f.read_text()))
    if not steps:
        return
    found = [
        rel
        for rel, (data, _) in out.items()
        if rel.startswith(".github/workflows/") and MARKER.encode() in data
    ]
    if not found:
        raise KitError(
            "a layer has ci-steps.yml but no workflow contains the line: " + MARKER
        )
    rel = found[0]
    data, mode = out[rel]
    lines = data.decode().splitlines(keepends=True)
    result = []
    for line in lines:
        if line.strip() == MARKER:
            indent = line[: len(line) - len(line.lstrip())]
            for _, text in steps:
                for s in text.splitlines():
                    result.append(indent + s + "\n" if s.strip() else "\n")
        result.append(line)
    out[rel] = ("".join(result).encode(), mode)


def write_file(dest: Path, data: bytes, exec_bits):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(0o755 if exec_bits else 0o644)


def write_record(target: Path, record):
    (target / RECORD).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


def install_hooks_path(target: Path, out=print):
    """Point core.hooksPath at .githooks unless the project already uses another path."""
    r = subprocess.run(
        ["git", "-C", str(target), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=False,
    )
    current = r.stdout.strip()
    if current and current != ".githooks":
        out(
            f"note: core.hooksPath is already set to {current!r}, so it was left alone; "
            "call .githooks/pre-push from your own hook path to run the check before a push"
        )
        return
    subprocess.run(
        ["git", "-C", str(target), "config", "core.hooksPath", ".githooks"], check=True
    )


def run(kit_root: Path, layers_dir: Path, names, target: Path, force=False, out=print):
    target = Path(target)
    if not (target / ".git").exists():
        raise KitError(f"{target} is not a git repository (run git init first)")
    layers = resolve_layers(layers_dir, names)
    out_files = collect(layers_dir, layers)
    insert_ci_steps(layers_dir, layers, out_files)

    if not force:
        differing = [
            rel
            for rel, (data, _) in sorted(out_files.items())
            if (target / rel).is_file() and (target / rel).read_bytes() != data
        ]
        if differing:
            raise KitError(
                "these files already exist with different content (nothing was written; "
                "use --force to overwrite them):\n  " + "\n  ".join(differing)
            )

    hashes = {}
    for rel in sorted(out_files):
        data, exec_bits = out_files[rel]
        write_file(target / rel, data, exec_bits)
        hashes[rel] = sha256(data)

    version = (kit_root / "VERSION").read_text().strip()
    write_record(target, {"kit_version": version, "layers": layers, "files": hashes})
    install_hooks_path(target, out)
