#!/usr/bin/env bash
# Runs before every shell command. Refuses commands that destroy work or
# skip review.
#
# 1. Deleting from the root folder, `git reset --hard`, and `DROP TABLE`
#    throw work away with no undo.
# 2. Commands that wipe unsaved work (changes not yet committed) with no
#    undo: `git clean -f`, `git checkout -- <file>` or `git checkout .`,
#    `git restore <file>` (only `git restore --staged`, which just unstages,
#    is allowed), `git stash drop` or `clear`, `git branch -D`, and
#    `rm -r` of `.git` or of the whole folder (`.`, `./`, `*`, `./*`).
#    The safe route is to commit the work on a branch and go back with
#    a revert commit, which only ever adds commits.
# 3. Force-pushing to main overwrites the shared copy on GitHub.
# 4. Committing or pushing straight to main skips the pull request, and
#    means nobody reviews the change before it lands.
#
# Exit 2 blocks the command and shows the reason to Claude.

# shellcheck source=/dev/null
source "$(dirname "$0")/lib.sh"
cmd="$(field "$(cat)" tool_input.command)"
[ -z "$cmd" ] && exit 0

block() { echo "BLOCKED: $1" >&2; exit 2; }

if printf '%s' "$cmd" | grep -qE 'rm[[:space:]]+-[a-zA-Z]*r[a-zA-Z]*f?[[:space:]]+/([[:space:]"]|$|\*)|rm[[:space:]]+-[a-zA-Z]*f[a-zA-Z]*r[[:space:]]+/([[:space:]"]|$|\*)'; then
  block "this deletes from the root of the computer."
fi
if printf '%s' "$cmd" | grep -qE 'git[[:space:]]+reset[[:space:]]+--hard'; then
  block "git reset --hard throws away uncommitted work with no undo. Ask the owner first; they can run it themselves."
fi
if printf '%s' "$cmd" | grep -qiE 'DROP[[:space:]]+TABLE'; then
  block "DROP TABLE deletes a database table and its data."
fi

# Commands that wipe unsaved work. Each check looks at one command at a time
# (the text up to the next ; & or |), so flags from a neighbouring command
# never count. When jq and python3 are both missing the hook sees raw JSON,
# where a quote arrives as \" — hence the optional \\ before quotes below.
keep="Keep the work instead: commit it on a branch. To reverse a change, commit it, then use git revert, which adds a commit that undoes it and keeps the old version recoverable."
git_='git([[:space:]]+-C[[:space:]]+[^[:space:];&|]+)*[[:space:]]+'   # git, or git -C <folder>
end='([[:space:]"'\''\\]|$)'                                             # end of one word
q='\\?["'\'']?'                                                          # optional quote
# segments <regex>: print each command in $cmd that starts with the regex.
segments() { printf '%s' "$cmd" | grep -oE "$1[^;&|]*"; }
has() { printf '%s' "$1" | grep -qE -- "$2"; }

while IFS= read -r s; do
  [ -n "$s" ] && has "$s" "[[:space:]](-[a-zA-Z]*f[a-zA-Z]*|--force)$end" &&
    block "git clean -f deletes files git has never saved, with no undo. $keep"
done <<< "$(segments "${git_}clean")"

while IFS= read -r s; do
  [ -n "$s" ] && has "$s" "[[:space:]]--$end|[[:space:]]$q\.(/[^[:space:]\"'\\\\]*)?$q$end" &&
    block "git checkout -- <file> and git checkout . throw away unsaved changes, with no undo. $keep"
done <<< "$(segments "${git_}checkout")"

while IFS= read -r s; do
  [ -z "$s" ] && continue
  if has "$s" "[[:space:]](--staged|-[a-zA-Z]*S[a-zA-Z]*)([[:space:]\"'\\\\=]|$)" &&
     ! has "$s" "[[:space:]](--worktree|-[a-zA-Z]*W[a-zA-Z]*)([[:space:]\"'\\\\=]|$)"; then
    continue  # --staged alone only unstages; the file's changes stay.
  fi
  block "git restore <file> throws away unsaved changes, with no undo. To unstage a file and keep its changes, use git restore --staged <file>. $keep"
done <<< "$(segments "${git_}restore")"

if printf '%s' "$cmd" | grep -qE "${git_}stash[[:space:]]+(drop|clear)$end"; then
  block "git stash drop and git stash clear delete set-aside work, with no undo. $keep"
fi

while IFS= read -r s; do
  [ -z "$s" ] && continue
  if has "$s" "[[:space:]]-[a-zA-Z]*D" ||
     { has "$s" "[[:space:]](-[a-zA-Z]*d[a-zA-Z]*|--delete)$end" && has "$s" "[[:space:]](-[a-zA-Z]*f[a-zA-Z]*|--force)$end"; }; then
    block "git branch -D deletes a branch even when its work is saved nowhere else. git branch -d deletes a branch once its work is merged. $keep"
  fi
done <<< "$(segments "${git_}branch")"

while IFS= read -r s; do
  [ -z "$s" ] && continue
  has "$s" "[[:space:]](-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)$end" || continue
  if has "$s" "[[:space:]]$q(\./)?\.git/?$q$end"; then
    block "this deletes .git, the project's whole saved history. $keep"
  fi
  if has "$s" "[[:space:]]$q(\./?|\*|\./\*)$q$end"; then
    block "this deletes every file in the folder. Delete the specific files or folders by name. $keep"
  fi
done <<< "$(segments '(^|[^A-Za-z0-9_.-])rm[[:space:]]')"

if printf '%s' "$cmd" | grep -qE 'git[[:space:]]+push[^;&|]*(-f([[:space:]"]|$)|--force)[^;&|]*[[:space:]:]main([[:space:]";&|]|$)|git[[:space:]]+push[^;&|]*[[:space:]:]main([[:space:]";&|]|$)[^;&|]*(-f([[:space:]"]|$)|--force)'; then
  block "force-pushing to main overwrites the shared copy on GitHub."
fi

# Straight to main: a push that names main, or a commit/push made while on main.
branch="$(git -C "${CLAUDE_PROJECT_DIR:-.}" branch --show-current 2>/dev/null)"
if printf '%s' "$cmd" | grep -qE 'git[[:space:]]+push[^;&|]*[[:space:]:]main([[:space:]";&|]|$)'; then
  block "pushing straight to main skips the pull request. Push a branch and open a pull request so someone reviews it first."
fi
if [ "$branch" = "main" ] && printf '%s' "$cmd" | grep -qE 'git[[:space:]]+(commit|push)([[:space:]"]|$)'; then
  block "you are on main. Make a branch first (git switch -c <name>), then commit, push, and open a pull request."
fi
exit 0
