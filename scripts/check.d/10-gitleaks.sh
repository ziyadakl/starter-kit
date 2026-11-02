#!/usr/bin/env bash
# Scans for keys when gitleaks is installed; otherwise skips with one line.
# Only files git would save are scanned: tracked ones plus new ones that are not git-ignored.
# A key in a git-ignored .env or .venv never leaves the machine, so it does not fail the push.
set -euo pipefail

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks not installed: skipping the key scan (CI still runs it)"
  exit 0
fi

scan_dir=$(mktemp -d)
trap 'rm -rf "$scan_dir"' EXIT

while IFS= read -r -d '' f; do
  { [ -f "$f" ] || [ -L "$f" ]; } || continue
  mkdir -p "$scan_dir/$(dirname "$f")"
  cp -pP "$f" "$scan_dir/$f"
done < <(git ls-files -z --cached --others --exclude-standard)

# Scan from inside the copy so paths stay relative, as the config's allowlist expects.
cd "$scan_dir"
# `gitleaks dir` exists from 8.19; older versions use `detect --no-git`.
if gitleaks dir --help >/dev/null 2>&1; then
  gitleaks dir --no-banner --redact .
else
  gitleaks detect --no-git --no-banner --redact --source .
fi
