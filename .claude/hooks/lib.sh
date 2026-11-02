#!/usr/bin/env bash
# Shared helper for this project's hooks.
#
# Claude Code hands every hook a JSON message on stdin. This file reads one
# field out of it without assuming any one tool is installed: it uses jq if
# present, then python3, and otherwise falls back to the raw JSON text.
# The fallback is rougher, but it never lets a check pass silently just
# because a tool is missing on another computer.

# Usage: field "$input" tool_input.command
field() {
  local input="$1" path="$2"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$input" | jq -r ".${path} // \"\"" 2>/dev/null && return
  fi
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$input" | python3 -c '
import json, sys
d = json.load(sys.stdin)
for k in sys.argv[1].split("."):
    d = d.get(k, "") if isinstance(d, dict) else ""
print(d if isinstance(d, str) else "")
' "$path" 2>/dev/null && return
  fi
  # Last resort: search the raw JSON. Matches still work on it, just less precisely.
  printf '%s' "$input"
}
