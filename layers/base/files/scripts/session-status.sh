#!/usr/bin/env bash
# Live state for the start of a session: branch, uncommitted files, open pull requests, open issues, free disk.
# Run by the SessionStart hook in .claude/settings.json, so every session opens knowing where things stand;
# run it by hand any time: bash scripts/session-status.sh
# Fast and never fails: GitHub calls give up after 3 seconds, a missing or signed-out gh just leaves those lines
# out, and the exit code is always 0.

main() {
  local top tmp
  top=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "Session status: not in a git checkout"; return; }
  cd "$top" || return

  echo "Branch: $(git branch --show-current 2>/dev/null || echo '?') at $(git log -1 --format='%h %s' 2>/dev/null || echo 'no commits yet')"
  local changed
  changed=$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  [ "${changed:-0}" != 0 ] && echo "Uncommitted: $changed file(s)"

  # Both GitHub calls run at once in the background; whatever has not answered in 3 seconds is skipped.
  if command -v gh >/dev/null 2>&1 && tmp=$(mktemp -d 2>/dev/null); then
    GH_PROMPT_DISABLED=1 gh pr list --state open --limit 30 \
      --json number,title,headRefName,baseRefName \
      --jq '.[] | "  #\(.number) \(.headRefName) -> \(.baseRefName): \(.title)"' >"$tmp/prs" 2>/dev/null &
    local p1=$!
    GH_PROMPT_DISABLED=1 gh issue list --state open --limit 1000 --json number --jq length >"$tmp/issues" 2>/dev/null &
    local p2=$! waited=0
    while [ "$waited" -lt 30 ] && { kill -0 "$p1" 2>/dev/null || kill -0 "$p2" 2>/dev/null; }; do
      sleep 0.1
      waited=$((waited + 1))
    done
    kill "$p1" "$p2" 2>/dev/null
    [ -s "$tmp/prs" ] && { echo "Open PRs:"; cat "$tmp/prs"; }
    [ -s "$tmp/issues" ] && echo "Open issues: $(cat "$tmp/issues")"
    rm -rf "$tmp"
  fi

  # Free space on the disk holding this checkout, in GB; warn below 10.
  local free_kb free_gb
  free_kb=$(df -Pk . 2>/dev/null | awk 'NR==2 {print $4}')
  if [ -n "$free_kb" ]; then
    free_gb=$((free_kb / 1024 / 1024))
    echo "Free disk: ${free_gb} GB"
    [ "$free_gb" -lt 10 ] && echo "WARNING: under 10 GB free."
  fi
}

main 2>/dev/null
exit 0
