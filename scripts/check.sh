#!/usr/bin/env bash
# The one check command: runs every scripts/check.d/*.sh in sorted order and stops at the first failure.
# The last line is always "CHECK PASSED" or "CHECK FAILED: <script>", and the exit code matches.
set -Eeuo pipefail

cd "$(git rev-parse --show-toplevel)"

# Self-install the tracked hooks in this checkout (and every worktree sharing its config).
if [ -z "$(git config --get core.hooksPath || true)" ]; then
  git config core.hooksPath .githooks
fi

for script in scripts/check.d/*.sh; do
  [ -e "$script" ] || continue
  echo "== $script"
  if ! bash "$script"; then
    echo "CHECK FAILED: $script" >&2
    exit 1
  fi
done

echo "CHECK PASSED"
