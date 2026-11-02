#!/usr/bin/env python3
# ruff: noqa: E501  (long comment and docstring lines are kept as written)
"""PreToolUse hook on Read, Grep and Bash: refuse anything that would print `.env` values into the chat.

Covers `.env` and `.env.<anything>` (`.env.local`, `.env.production`); `.env.example` (and `.sample`, `.template`) is
always allowed. Listing the names only stays allowed, because Claude can work with names:
    cut -d= -f1 .env
    awk -F= '{print $1, (length($2)>0?"filled":"EMPTY")}' .env
    grep -c KEY .env      (also -q, -l, -L)
Not covered: a program that loads `.env` into its own environment and prints it (`uv run --env-file .env env`),
and anything a program does with the values once running. The guard stops the common slips, not a determined bypass.

Blocks with exit 2 and a reason on stderr. A crash or unreadable input exits 0: a broken guard must not stop all tool use.
"""

from __future__ import annotations

import json
import os
import re
import sys
from fnmatch import fnmatchcase
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_words import all_commands, program  # noqa: E402

TEMPLATES = {"example", "sample", "template"}
# Programs that print what they read. Anything else that merely names .env (ls, test) is allowed.
PRINTERS = {
    "cat", "tac", "head", "tail", "less", "more", "view", "vi", "vim", "nano", "bat", "nl", "strings", "xxd", "od",
    "hexdump", "base64", "sort", "uniq", "diff", "paste", "column", "tr", "grep", "egrep", "fgrep", "rg", "sed",
    "awk", "gawk", "cut", "source", ".", "xargs", "jq", "python", "python3",
}  # fmt: skip
GREPS = {"grep", "egrep", "fgrep"}
# Programs that copy or pack the files they name: a copy of .env can then be printed under another name.
COPIERS = {"cp", "mv", "ln", "rsync", "scp", "install", "ditto"}
PACKERS = {"tar", "zip", "gzip", "bzip2", "xz", "zstd", "dd"}
# Interpreters whose quoted code (`python3 -c "open('.env')"`) or arguments can read the file.
INTERPRETER = re.compile(r"(python[0-9.]*|node|ruby|perl)")
# git subcommands that print a file's contents (`git show HEAD:.env`).
GIT_PRINTERS = {"show", "cat-file", "diff", "log", "blame", "grep"}
ENV_IN_CODE = re.compile(r"(?<![\w.])\.env(?:\.(\w+))?(?!\w)")
GLOB = re.compile(r"[*?\[]")

REASON = (
    "BLOCKED by env guard: this would print .env values (keys, passwords, personal details) into the chat. "
    "To see which settings exist: `cut -d= -f1 .env`. To see which are filled: "
    """`awk -F= '{print $1, (length($2)>0?"filled":"EMPTY")}' .env`. """
    "Code loads .env itself; never read it, source it, or copy it. To add a setting, add its name "
    "to .env.example and ask the owner to fill in .env by hand."
)


def is_env(word: str) -> bool:
    """The word names a `.env` file, or is a shell glob that matches one (`.env*`, `.[e]nv`)."""
    name = word.rstrip("/").rsplit("/", 1)[-1]
    if GLOB.search(name):
        # The shell's `*` and `?` never match a leading dot, so only a pattern starting with `.` reaches `.env`.
        return name.startswith(".") and any(
            fnmatchcase(env, name) for env in (".env", ".env.local")
        )
    match = re.fullmatch(r"\.env(?:\.(.+))?", name)
    return bool(match) and match.group(1) not in TEMPLATES


def mentions_env(text: str) -> bool:
    """`.env` appears inside a longer string, such as an interpreter's code: `open('.env')`, not `os.environ`."""
    return any(m.group(1) not in TEMPLATES for m in ENV_IN_CODE.finditer(text))


def delimiter_is_equals(args: list[str], short: str, long: str) -> bool:
    for i, arg in enumerate(args):
        if arg in (short, long) and i + 1 < len(args):
            return args[i + 1] == "="
        if arg.startswith(short) and len(arg) > 2:
            return arg[2:] == "="
        if arg.startswith(long + "="):
            return arg[len(long) + 1 :] == "="
    return False


def cut_names_only(args: list[str]) -> bool:
    fields = None
    for i, arg in enumerate(args):
        if arg in ("-f", "--fields") and i + 1 < len(args):
            fields = args[i + 1]
        elif arg.startswith("-f") and len(arg) > 2:
            fields = arg[2:]
        elif arg.startswith("--fields="):
            fields = arg[len("--fields=") :]
    return fields == "1" and delimiter_is_equals(args, "-d", "--delimiter")


def awk_names_only(args: list[str]) -> bool:
    if not delimiter_is_equals(args, "-F", "--field-separator"):
        return False
    programs = [a for a in args if "{" in a]
    if len(programs) != 1:
        return False  # no action block means awk prints whole lines
    text = re.sub(r"length\(\$\d+\)", "", programs[0])
    if re.search(r"\$(0|[2-9]|\d{2,}|NF|\(|[A-Za-z_])", text):
        return False
    return not re.search(r"\bprintf?\s*(;|}|$)", text)


def grep_quiet(args: list[str]) -> bool:
    for arg in args:
        if arg in (
            "--quiet",
            "--silent",
            "--count",
            "--files-with-matches",
            "--files-without-match",
        ):
            return True
        if re.fullmatch(r"-[A-Za-z]+", arg) and set(arg[1:]) & set("qclL"):
            return True
    return False


def names_only(name: str, args: list[str]) -> bool:
    if name == "cut":
        return cut_names_only(args)
    if name in ("awk", "gawk"):
        return awk_names_only(args)
    if name in ("grep", "egrep", "fgrep", "rg"):
        return grep_quiet(args)
    return False


def flag_values(args: list[str], *flags: str) -> list[str]:
    """Values of the given flags, in both `--flag=value` and `--flag value` forms."""
    values = []
    for i, arg in enumerate(args):
        flag, has_value, value = arg.partition("=")
        if flag in flags:
            if has_value:
                values.append(value)
            elif i + 1 < len(args):
                values.append(args[i + 1])
    return values


def reaches_folder_with_env(paths: list[str], cwd: str) -> bool:
    """A recursive search over these paths (none means `.`) would walk into a folder holding `.env`: the checkout
    folder or one above it, or any folder with a `.env` file in it."""
    here = Path(cwd).resolve()
    for path in paths or ["."]:
        folder = (here / os.path.expanduser(path)).resolve()
        if folder == here or folder in here.parents:
            return True
        if folder.is_dir() and any(
            is_env(child.name) for child in folder.glob(".env*")
        ):
            return True
    return False


def searches_env_recursively(name: str, args: list[str], cwd: str) -> bool:
    """`grep -r` or `rg --hidden` over a folder that holds `.env`, with no exclusion that skips it."""
    operands = [a for a in args if not a.startswith("-")]
    if name in GREPS:
        recursive = any(
            a in ("--recursive", "--dereference-recursive", "--directories=recurse")
            or re.fullmatch(r"-[A-Za-z0-9]*[rR][A-Za-z0-9]*", a)
            for a in args
        )
        excluded = any(
            fnmatchcase(".env", glob) for glob in flag_values(args, "--exclude")
        )
        return recursive and not excluded and reaches_folder_with_env(operands[1:], cwd)
    if name == "rg":
        # ripgrep skips hidden files such as .env unless told to search them.
        hidden = any(
            a == "--hidden"
            or (re.fullmatch(r"-[A-Za-z.]+", a) and ("." in a[1:] or a.count("u") >= 2))
            for a in args
        )
        globs = flag_values(args, "-g", "--glob", "--iglob")
        excluded = any(g.startswith("!") and fnmatchcase(".env", g[1:]) for g in globs)
        return hidden and not excluded and reaches_folder_with_env(operands[1:], cwd)
    return False


def git_prints_env(args: list[str]) -> bool:
    """`git show HEAD:.env`, `git cat-file -p :.env`, `git log -p -- .env`."""
    sub = next((a for a in args if not a.startswith("-")), "")
    return sub in GIT_PRINTERS and any(is_env(a.split(":", 1)[-1]) for a in args)


def copies_env(name: str, args: list[str]) -> bool:
    """`cp .env /tmp/x`, `mv .env x`, `tar czf x.tgz .env`, `dd if=.env`. Writing a template into .env
    (`cp .env.example .env`) is allowed: there .env is the destination."""
    if name in PACKERS:
        return any(is_env(arg.partition("=")[2] if "=" in arg else arg) for arg in args)
    if name not in COPIERS:
        return False
    operands = [a for a in args if not a.startswith("-")]
    targeted = any(
        a in ("-t", "--target-directory") or a.startswith("--target-directory=")
        for a in args
    )
    sources = operands if targeted else operands[:-1]
    return any(is_env(a) for a in sources)


def prints_env(command: str, cwd: str = ".") -> bool:
    try:
        commands = all_commands(command)
    except ValueError:
        return ".env" in command and bool(
            re.search(r"\b(cat|head|tail|less|grep|sed|awk|source)\b", command)
        )
    for words in commands:
        name, args = program(words)
        redirected = any(
            args[i] == "<" and is_env(args[i + 1]) for i in range(len(args) - 1)
        )
        # `if=.env` counts as naming it; a flag's value such as `--exclude=.env*` does not.
        named = any(
            is_env(arg) or (not arg.startswith("-") and is_env(arg.partition("=")[2]))
            for arg in args
        )
        if (redirected or (named and name in PRINTERS)) and not names_only(name, args):
            return True
        if INTERPRETER.fullmatch(name) and any(mentions_env(arg) for arg in args):
            return True
        if name == "git" and git_prints_env(args):
            return True
        if copies_env(name, args):
            return True
        if not names_only(name, args) and searches_env_recursively(name, args, cwd):
            return True
    return False


def glob_matches_env(glob: str) -> bool:
    name = glob.rstrip("/").rsplit("/", 1)[-1]
    return any(fnmatchcase(env, name) for env in (".env", ".env.local"))


def grep_tool_reads_env(tool_input: dict, cwd: str) -> bool:
    """The Grep tool names `.env` (path or glob), or prints matching lines (`output_mode: content`) from a search that
    walks a folder holding `.env` with no glob or type that leaves it out. Listing file names or counts is allowed,
    like `grep -l`."""
    path = tool_input.get("path") or ""
    glob = tool_input.get("glob") or ""
    if is_env(path) or is_env(glob):
        return True
    if tool_input.get("output_mode") != "content":
        return False
    could_include_env = not tool_input.get("type") and (
        not glob or glob_matches_env(glob)
    )
    return could_include_env and reaches_folder_with_env([path] if path else [], cwd)


def blocks(tool: str, tool_input: dict, cwd: str) -> bool:
    """The tool call would print `.env` values."""
    if tool == "Read":
        return is_env(tool_input.get("file_path") or "")
    if tool == "Grep":
        return grep_tool_reads_env(tool_input, cwd)
    return prints_env(tool_input.get("command") or "", cwd)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        refuse = blocks(
            payload.get("tool_name") or "",
            payload.get("tool_input") or {},
            payload.get("cwd") or ".",
        )
    except Exception:  # fail open: a crash in this guard must not stop all tool use
        return
    if refuse:
        print(REASON, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
