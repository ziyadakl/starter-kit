#!/usr/bin/env bash
# Keeps main read-only for Claude. Work happens on branches and reaches main
# through a pull request that a person merges.
#
# Called two ways from .claude/settings.json:
#   main-is-read-only.sh edit   before Claude edits or writes a file
#   main-is-read-only.sh bash   before Claude runs a shell command
#
# It refuses:
#   - editing any file while on main
#   - any commit, merge, rebase, reset, cherry-pick, revert or stash pop on main,
#     except bringing in GitHub's main with `git merge --ff-only origin/main`
#   - merging a pull request, from any branch
# Pushing to main is refused by block-risky-git.sh.
#
# Set STARTER_KIT_MAIN_WRITABLE=1 in the environment to switch this guard off.
# Exit 2 blocks the action and shows the reason to Claude.

# shellcheck source=/dev/null
source "$(dirname "$0")/lib.sh"
dir="${CLAUDE_PROJECT_DIR:-.}"
[ "${STARTER_KIT_MAIN_WRITABLE:-}" = "1" ] && exit 0

input="$(cat)"
branch="$(git -C "$dir" branch --show-current 2>/dev/null)"
block() { echo "BLOCKED: $1" >&2; exit 2; }
make_branch="Make a branch first with: git switch -c <short-name-for-the-work>. Then work, commit, push, and open a pull request."

case "$1" in
  edit)
    [ "$branch" = "main" ] && block "main is read-only. $make_branch"
    ;;
  bash)
    cmd="$(field "$input" tool_input.command)"
    [ -z "$cmd" ] && exit 0
    if printf '%s' "$cmd" | grep -qE 'gh[[:space:]]+pr[[:space:]]+merge|pulls/[0-9]+/merge'; then
      block "a person merges pull requests, not Claude. Leave the pull request open and say it waits for review."
    fi
    if [ "$branch" = "main" ]; then
      # Remove the one allowed update, then look for anything that changes main.
      rest="$(printf '%s' "$cmd" | sed -E 's/git[[:space:]]+merge[[:space:]]+--ff-only[[:space:]]+origin\/main//g')"
      if printf '%s' "$rest" | grep -qE 'git[[:space:]]+(commit|merge|rebase|reset|cherry-pick|revert|am|stash[[:space:]]+(pop|apply))([[:space:]"]|$)'; then
        block "main is read-only. To bring in GitHub's main, run: git merge --ff-only origin/main. $make_branch"
      fi
    fi
    ;;
esac
exit 0
