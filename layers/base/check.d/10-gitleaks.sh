#!/usr/bin/env bash
# Scans the working tree for keys when gitleaks is installed; otherwise skips with one line.
set -euo pipefail

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "gitleaks not installed: skipping the key scan (CI still runs it)"
  exit 0
fi

# `gitleaks dir` exists from 8.19; older versions use `detect --no-git`.
if gitleaks dir --help >/dev/null 2>&1; then
  gitleaks dir --no-banner --redact .
else
  gitleaks detect --no-git --no-banner --redact --source .
fi
