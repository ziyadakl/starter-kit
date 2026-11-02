"""The kit is public: no personal paths, host names, emails or literal private names in any tracked file."""

import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Patterns are built from pieces so this file does not match itself.
PATTERNS = {
    "mac home path": re.compile(r"/" + "Users" + r"/(?!<)[A-Za-z0-9_.-]+"),
    "linux home path": re.compile(r"/" + "home" + r"/(?!runner\b|<)[A-Za-z0-9_.-]+"),
    "tailnet name": re.compile(r"[A-Za-z0-9-]+\.ts" + r"\.net\b"),
}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org"}


def files():
    out = (
        subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        .stdout.decode()
        .split("\0")
    )
    for rel in filter(None, out):
        if rel.startswith(".git/") or not (ROOT / rel).is_file():
            continue
        try:
            yield rel, (ROOT / rel).read_text()
        except UnicodeDecodeError:
            continue


def scan(text, literals=()):
    """Return a list of problem descriptions found in text."""
    found = []
    for label, pat in PATTERNS.items():
        found += [f"{label}: {m.group(0)}" for m in pat.finditer(text)]
    for m in EMAIL.finditer(text):
        local = m.group(0).split("@")[0]
        if local == "noreply" or m.group(1) in ALLOWED_EMAIL_DOMAINS:
            continue
        found.append(f"email: {m.group(0)}")
    low = text.lower()
    found += [f"private name: {n}" for n in literals if n and n.lower() in low]
    return found


def literals_from_env():
    return [
        n.strip() for n in os.environ.get("PRIVATE_NAMES", "").split(",") if n.strip()
    ]


class TestScanner(unittest.TestCase):
    def test_flags_personal_paths_emails_tailnet_and_literals(self):
        text = (
            "/"
            + "Users/someone/x /"
            + "home/someone/y a@b"
            + ".io host.tail1234.ts"
            + ".net secretword"
        )
        found = scan(text, ["SecretWord"])
        self.assertEqual(len(found), 5, found)

    def test_allows_generic_examples(self):
        text = (
            "/"
            + "Users/<name>/x /"
            + "home/runner/work a@example.com b@example.org noreply@anthropic.com"
        )
        self.assertEqual(scan(text), [])


class TestNoPrivateNames(unittest.TestCase):
    def test_tracked_files_are_clean(self):
        literals = literals_from_env()
        problems = []
        for rel, text in files():
            problems += [f"{rel}: {p}" for p in scan(text, literals)]
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
