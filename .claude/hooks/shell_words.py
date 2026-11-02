# ruff: noqa: E501  (long comment and docstring lines are kept as written)
"""Split a shell command line into simple commands, for the guards in this folder.

Standard library only, and Python 3.9 compatible: hooks run on whatever `python3` the shell finds.
"""

from __future__ import annotations

import re
import shlex

SEPARATORS = set(";&|()\n")
# Programs whose arguments are themselves a command line, run here or on another machine.
WRAPPERS = {
    "ssh",
    "bash",
    "sh",
    "zsh",
    "eval",
    "env",
    "timeout",
    "nice",
    "watch",
    "xargs",
}
_HEREDOC = re.compile(r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def strip_heredocs(command: str) -> str:
    """Drop heredoc bodies, so text written into a file is not mistaken for a command."""
    out = []
    lines = command.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        for match in _HEREDOC.finditer(line):
            end = match.group(2)
            while i < len(lines) and lines[i].strip() != end:
                i += 1
            i += 1  # the closing delimiter
    return "\n".join(out)


def simple_commands(command: str) -> list[list[str]]:
    """Each simple command as its words, split at ; && || | & ( ) and newlines. Raises ValueError on bad quoting."""
    lexer = shlex.shlex(strip_heredocs(command), posix=True, punctuation_chars=True)
    lexer.whitespace = " \t\r"  # newline stays a token so it separates commands
    lexer.wordchars += "$`=~*?[]{}!%^,:@+"
    commands: list[list[str]] = []
    current: list[str] = []
    for token in lexer:
        if token and set(token) <= SEPARATORS:
            if current:
                commands.append(current)
            current = []
        else:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def program(words: list[str]) -> tuple[str, list[str]]:
    """The program name (no path, no backtick or `$(`) and its arguments, after any VAR=value prefixes."""
    i = 0
    while i < len(words) and _ASSIGNMENT.match(words[i]):
        i += 1
    while i < len(words) and words[i] in (
        "command",
        "exec",
        "builtin",
        "nohup",
        "sudo",
        "time",
    ):
        i += 1
    if i >= len(words):
        return "", []
    name = words[i].lstrip("`$").rsplit("/", 1)[-1]
    return name, words[i + 1 :]


def all_commands(command: str, depth: int = 0) -> list[list[str]]:
    """Every simple command, including those a wrapper runs: `bash -c "..."`, `eval ...`, `ssh host '...'`.

    Raises ValueError on bad quoting in the outer command line; a wrapped line that does not parse is skipped.
    """
    found: list[list[str]] = []
    for words in simple_commands(command):
        found.append(words)
        name, args = program(words)
        if name not in WRAPPERS or depth > 4:
            continue
        # The wrapped command starts at some argument (after the wrapper's own flags, host, or duration), or is one
        # quoted argument holding a whole command line. Try each.
        inner = [shlex.join(args[i:]) for i in range(len(args))] + [
            arg for arg in args if " " in arg
        ]
        for line in inner:
            try:
                found.extend(w for w in all_commands(line, depth + 1) if w not in found)
            except ValueError:
                pass
    return found
