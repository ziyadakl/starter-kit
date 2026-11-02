"""`kit.py update`: bring a project up to the current kit without overwriting its edits."""

import json
import re
import subprocess
from pathlib import Path

from starterkit.apply import RECORD, KitError, collect, insert_ci_steps, sha256


def parse_version(text):
    try:
        return tuple(int(p) for p in text.strip().split("."))
    except ValueError:
        raise KitError(f"not a version: {text!r}")


def changelog_since(changelog: str, recorded: str):
    """Sections of CHANGELOG.md (## <version> headings) newer than `recorded`."""
    floor = parse_version(recorded)
    sections = re.split(r"(?m)^(?=## )", changelog)
    keep = []
    for s in sections:
        m = re.match(r"## +(\d+(?:\.\d+)*)", s)
        if m and parse_version(m.group(1)) > floor:
            keep.append(s.rstrip() + "\n")
    return keep


def run(kit_root: Path, layers_dir: Path, target: Path, out=print):
    target = Path(target)
    rec_path = target / RECORD
    if not rec_path.is_file():
        raise KitError(f"{target} has no {RECORD}; run `kit.py apply` first")
    record = json.loads(rec_path.read_text())
    layers = record["layers"]
    old = record["files"]

    new_files = collect(layers_dir, layers)
    insert_ci_steps(layers_dir, layers, new_files)
    version = (kit_root / "VERSION").read_text().strip()

    entries = changelog_since((kit_root / "CHANGELOG.md").read_text(), record["kit_version"])
    if entries:
        out(f"Changes since {record['kit_version']}:\n")
        out("\n".join(entries))

    updated, added, edited, deleted, conflicts, removed = [], [], [], [], [], []
    hashes = {}
    for rel in sorted(new_files):
        data, exec_bits = new_files[rel]
        new_hash = sha256(data)
        dest = target / rel
        if rel not in old:
            if dest.exists():
                if sha256(dest.read_bytes()) == new_hash:
                    hashes[rel] = new_hash
                else:
                    conflicts.append(rel)
                continue
            _write(dest, data, exec_bits)
            hashes[rel] = new_hash
            added.append(rel)
            continue
        if not dest.exists():
            deleted.append(rel)
            hashes[rel] = old[rel]
            continue
        current = sha256(dest.read_bytes())
        if current == new_hash:
            hashes[rel] = new_hash
        elif current == old[rel]:
            _write(dest, data, exec_bits)
            hashes[rel] = new_hash
            updated.append(rel)
        else:
            edited.append((rel, old[rel] != new_hash))
            hashes[rel] = old[rel]
    for rel in sorted(set(old) - set(new_files)):
        if (target / rel).exists():
            removed.append(rel)

    record["kit_version"] = version
    record["files"] = hashes
    rec_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    subprocess.run(["git", "-C", str(target), "config", "core.hooksPath", ".githooks"], check=True)

    _report(out, "Updated", updated)
    _report(out, "Added (new in the kit)", added)
    if edited:
        out("Left alone, the project edited these (kit changed: yes/no):")
        for rel, changed in edited:
            out(f"  {rel} ({'yes' if changed else 'no'})")
    _report(out, "Left alone, the project already has a different file at the path of a new kit file", conflicts)
    _report(out, "Left alone, the project deleted these and they stay deleted", deleted)
    _report(out, "Kept, the kit no longer ships these (now yours; delete if unwanted)", removed)
    if not (updated or added or edited or conflicts or deleted or removed or entries):
        out(f"Already up to date at {version}.")


def _write(dest: Path, data: bytes, exec_bits):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    dest.chmod(0o755 if exec_bits else 0o644)


def _report(out, title, items):
    if items:
        out(f"{title}:")
        for rel in items:
            out(f"  {rel}")
