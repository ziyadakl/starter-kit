#!/usr/bin/env python3
# ruff: noqa: E501  (long comment and docstring lines are kept as written)
"""PreToolUse hook on Bash: refuse a pull request merge unless the PR's head commit has passing checks.

Finds `gh pr merge ...` and `gh api repos/<owner>/<repo>/pulls/<N>/merge`, also inside `bash -c`, `eval` or
`ssh host '...'`, then asks GitHub (through gh) for the checks on the PR's head commit. Every check must have finished
and passed (success, neutral or skipped). No checks at all counts as not passing: they may not have started yet.
`--auto` gets the same test, so it cannot queue a merge past red or missing checks.

Optional review stamp: when `<git common dir>/review-stamps/` exists, the head commit must also have a file there
named after its full SHA. A repo that never makes that folder is never asked for a stamp. The folder is shared by every
worktree; whoever reviews the PR (a person, a script, a skill) creates it by writing the stamp:
    d="$(git rev-parse --git-common-dir)/review-stamps"; mkdir -p "$d"; touch "$d/<head sha>"
A commit pushed after the stamp changes the head SHA, so the stamp goes stale and the merge is refused again.

Blocks with exit 2 and a reason on stderr. A merge that gh cannot verify is blocked too (it cannot be shown to be
safe). Any other crash exits 0: a broken guard must not stop all tool use.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, NoReturn

sys.dont_write_bytecode = True  # no .pyc beside the hooks in a project
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_words import all_commands, program  # noqa: E402

# `gh pr merge` flags that take a value; every other flag is a switch.
VALUE_FLAGS = {
    "--subject",
    "-t",
    "--body",
    "-b",
    "--body-file",
    "-F",
    "--author-email",
    "-A",
    "--match-head-commit",
}
REPO_FLAGS = {"--repo", "-R"}
# `gh api` endpoint that merges a pull request: repos/<owner>/<repo>/pulls/<number>/merge
API_MERGE = re.compile(r"(?:^|/)repos/([^/]+)/([^/]+)/pulls/(\d+)/merge/?$")
PASSING_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
GH_TIMEOUT = 20  # seconds per gh or git call; KIT_GH_TIMEOUT overrides (for tests)


def gh_timeout() -> float:
    return float(os.environ.get("KIT_GH_TIMEOUT") or GH_TIMEOUT)


class MergeRequest(NamedTuple):
    selector: str | None  # PR number, URL or branch; None means the current branch's PR
    repo: str | None  # --repo value; None means the checkout's own repo


def block(reason: str) -> NoReturn:
    print(f"BLOCKED by merge guard: {reason}", file=sys.stderr)
    sys.exit(2)


def merges(command: str) -> list[MergeRequest]:
    """Every pull request merge in the command line: `gh pr merge ...` and `gh api .../pulls/<N>/merge`."""
    found = []
    for words in all_commands(command):
        name, args = program(words)
        if name != "gh":
            continue
        global_repo, args = strip_global_repo(args)
        if args[:2] == ["pr", "merge"]:
            merge = parse(args[2:])
            found.append(merge if merge.repo else merge._replace(repo=global_repo))
        elif args[:1] == ["api"]:
            for arg in args[1:]:
                match = API_MERGE.search(arg)
                if match:
                    owner, repo, number = match.groups()
                    placeholder = owner.startswith("{") or repo.startswith("{")
                    found.append(
                        MergeRequest(number, None if placeholder else f"{owner}/{repo}")
                    )
    return found


def strip_global_repo(args: list[str]) -> tuple[str | None, list[str]]:
    """`gh -R o/r pr merge 7` or `gh pr -R o/r merge 7`: the repo given before or between the two command words,
    and the arguments with it removed."""
    repo, kept, i = None, [], 0
    while i < len(args) and sum(not word.startswith("-") for word in kept) < 2:
        flag, has_value, value = args[i].partition("=")
        if flag in REPO_FLAGS:
            repo = value if has_value else (args[i + 1] if i + 1 < len(args) else None)
            i += 1 if has_value else 2
            continue
        kept.append(args[i])
        i += 1
    return repo, kept + args[i:]


def parse(args: list[str]) -> MergeRequest:
    """The arguments of `gh pr merge` as the PR they select."""
    selector, repo, i = None, None, 0
    while i < len(args):
        arg = args[i]
        flag, has_value, value = arg.partition("=")
        if flag in REPO_FLAGS:
            repo = value if has_value else (args[i + 1] if i + 1 < len(args) else None)
            i += 1 if has_value else 2
            continue
        if flag in VALUE_FLAGS:
            i += 1 if has_value else 2
            continue
        if not arg.startswith("-") and selector is None:
            selector = arg
        i += 1
    return MergeRequest(selector, repo)


def pull_request(merge: MergeRequest, cwd: str) -> dict:
    gh_args = ["pr", "view", *([merge.selector] if merge.selector else [])]
    gh_args += ["--repo", merge.repo] if merge.repo else []
    gh_args += ["--json", "number,headRefOid,statusCheckRollup"]
    try:
        result = subprocess.run(
            ["gh", *gh_args],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=gh_timeout(),
        )
    except subprocess.TimeoutExpired:
        block(
            f"`gh {' '.join(gh_args)}` timed out after {gh_timeout():g}s, so the checks cannot be read. "
            "Retry, or merge by hand."
        )
    except OSError as error:
        block(
            f"could not run gh ({error}), so the checks cannot be read. Install gh, or merge by hand."
        )
    if result.returncode != 0:
        block(
            f"could not run `gh {' '.join(gh_args)}`: {result.stderr.strip() or 'no output'}. Fix that, then retry."
        )
    return json.loads(result.stdout)


def check_passed(entry: dict) -> bool:
    if "state" in entry:  # StatusContext
        return entry["state"] == "SUCCESS"
    return (
        entry.get("status") == "COMPLETED"
        and entry.get("conclusion") in PASSING_CONCLUSIONS
    )  # CheckRun


def stamps_folder(cwd: str) -> Path | None:
    """The review-stamps folder inside the git common dir, or None when this repo does not use stamps."""
    result = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=gh_timeout(),
    )
    if result.returncode != 0:
        return None
    folder = (Path(cwd) / result.stdout.strip()).resolve() / "review-stamps"
    return folder if folder.is_dir() else None


def require_merge_ready(merge: MergeRequest, cwd: str) -> None:
    """Block unless the PR head's checks all passed, and, where the repo uses review stamps, the head is stamped."""
    pr = pull_request(merge, cwd)
    number, head = pr["number"], pr["headRefOid"]
    checks = pr.get("statusCheckRollup") or []
    unpassed = [
        str(c.get("name") or c.get("context")) for c in checks if not check_passed(c)
    ]
    if not checks:
        block(
            f"PR #{number} head {head[:12]} has no checks yet (they may not have started). Wait for them, "
            "then retry. A repo with no CI at all has to be merged by hand."
        )
    if unpassed:
        block(
            f"PR #{number} head {head[:12]} has checks that are not passing: {', '.join(unpassed)}. "
            "Fix the code until they pass (`gh pr checks` shows them); do not merge past red."
        )
    folder = stamps_folder(cwd)
    if folder is not None and not (folder / head).is_file():
        block(
            f"PR #{number} head {head[:12]} has no review stamp, and this repo uses them ({folder}). "
            "Review the PR's current head, then stamp it: write an empty file named after the full head SHA there."
        )


def check(command: str, cwd: str) -> None:
    """Block (exit 2) any pull request merge in the command line that the rules above refuse."""
    if "merge" not in command:
        return
    try:
        found = merges(command)
    except ValueError:
        if re.search(r"\bgh\b.*\b(merge|api)\b", command, re.DOTALL):
            block(
                "could not parse this command line (unbalanced quotes?). Run the merge as a plain command."
            )
        return
    for merge in found:
        require_merge_ready(merge, cwd)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        command = (payload.get("tool_input") or {}).get("command") or ""
        cwd = payload.get("cwd") or "."
    except Exception:  # fail open: unreadable input is not a merge
        return
    try:
        check(command, cwd)
    except Exception as error:
        # Fail closed only for a command that names a merge; every other command passes.
        if re.search(r"\bgh\b.*\bmerge\b", command, re.DOTALL):
            block(
                f"the merge guard crashed ({type(error).__name__}: {error}), so this merge is refused."
            )


if __name__ == "__main__":
    main()
