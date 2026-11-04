#!/usr/bin/env bash
# The one check command: runs every scripts/check.d/*.sh in sorted order, keeps going after a failure, and lists every failure at the end.
# Sorted order is cost order: name a script 10-* when it takes seconds, 20-* for the language tools, 30-* and up for slow suites.
# The last line is always "CHECK PASSED" or "CHECK FAILED: <script>" (one such line per failed script), and the exit code matches.
set -Eeuo pipefail

cd "$(git rev-parse --show-toplevel)"

# Self-install the tracked hooks in this checkout (and every worktree sharing its config).
if [ -z "$(git config --get core.hooksPath || true)" ]; then
  git config core.hooksPath .githooks
fi

failed=()
for script in scripts/check.d/*.sh; do
  [ -e "$script" ] || continue
  echo "== $script"
  if ! bash "$script"; then
    echo "-- failed: $script" >&2
    failed+=("$script")
  fi
done

if [ "${#failed[@]}" -gt 0 ]; then
  echo >&2
  for script in "${failed[@]}"; do
    echo "CHECK FAILED: $script" >&2
  done
  exit 1
fi

echo "CHECK PASSED"
