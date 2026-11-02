#!/usr/bin/env bash
# Runs shellcheck on every shell script in the repo, found by extension (.sh) or by a sh/bash/dash/ksh shebang.
# zsh scripts are left out: shellcheck cannot parse them. A missing shellcheck fails the check.
set -euo pipefail

if ! command -v shellcheck >/dev/null 2>&1; then
  echo "shellcheck is not installed: install shellcheck (brew install shellcheck, or your package manager; see https://github.com/koalaman/shellcheck#installing)" >&2
  exit 1
fi

scripts=()
# Tracked files plus new files not yet added (and not ignored), so the pre-push check sees fresh scripts.
while IFS= read -r f; do
  [ -f "$f" ] || continue
  case "$f" in
    *.sh) scripts+=("$f") ;;
    *) if head -n 1 "$f" 2>/dev/null | grep -Eaq '^#!.*\b(ba|da|k)?sh\b'; then scripts+=("$f"); fi ;;
  esac
done < <(git ls-files --cached --others --exclude-standard)

if [ "${#scripts[@]}" -eq 0 ]; then
  echo "no shell scripts found"
  exit 0
fi

shellcheck "${scripts[@]}"
