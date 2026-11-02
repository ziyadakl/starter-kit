#!/usr/bin/env bash
# SessionStart hook: on a fresh clone, point git at the tracked hooks so the pre-push check runs.
# Does nothing when core.hooksPath is already set (the project or the owner chose a path).
dir="${CLAUDE_PROJECT_DIR:-.}"
if [ -z "$(git -C "$dir" config --get core.hooksPath 2>/dev/null || true)" ] && [ -d "$dir/.githooks" ]; then
  git -C "$dir" config core.hooksPath .githooks 2>/dev/null || true
fi
exit 0
