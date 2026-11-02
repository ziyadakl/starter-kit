#!/usr/bin/env bash
# Stops an API key, token or password from landing in a file git saves.
# Once a secret is in git history it is on GitHub for good, and the only fix
# is to cancel the key and get a new one.
#
# Called two ways from .claude/settings.json:
#   block-secrets.sh edit   before Claude edits or writes a file: checks the
#                           new text
#   block-secrets.sh bash   before Claude runs a shell command:
#                           - on `git commit`, checks every change git would
#                             see (staged, unstaged, and new files git does
#                             not ignore)
#                           - refuses `git add -f` of a .env file
#
# Secrets belong in .env or .env.<something>, which git ignores, so those
# files are never checked. .env.example is the exception: git saves it, so it
# must hold only placeholders like your-key-here.
# Files in .claude/hooks/ are skipped: this script holds the patterns and the
# tests hold samples.
#
# Exit 2 blocks the action and shows the reason to Claude. The reason names
# the file and the kind of secret, never the secret itself.

# shellcheck source=/dev/null
source "$(dirname "$0")/lib.sh"
dir="${CLAUDE_PROJECT_DIR:-.}"
input="$(cat)"

# Each entry is "<kind of secret>|<pattern>".
kinds=(
  'an AWS access key|(^|[^0-9A-Za-z])(AKIA|ASIA)[0-9A-Z]{16}([^0-9A-Za-z]|$)'
  'a GitHub token|(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})'
  'an Anthropic API key|sk-ant-[A-Za-z0-9_-]{20,}'
  'an OpenAI-style API key|(^|[^A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}'
  'a Slack token|xox[baprs]-[A-Za-z0-9-]{10,}'
  'a Stripe live key|(sk|rk)_live_[A-Za-z0-9]{16,}'
  'a Google API key|AIza[0-9A-Za-z_-]{35}'
  'a private key|-----BEGIN ([A-Z]+ )*PRIVATE KEY-----'
  # name = "value" or name: 'value' with a quoted value, or name=value unquoted.
  'a password or key|(^|[^A-Za-z0-9])([A-Za-z0-9_]*[_-])?(api[_-]?key|secret([_-]?key)?|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token|refresh[_-]?token|token)\\?["'\'']?[[:space:]]*([:=][[:space:]]*\\?["'\''][^"'\''[:space:]\\]{16,}\\?["'\'']|=[[:space:]]*[A-Za-z0-9_+/=-]{16,}([[:space:];,)"\\]|$))'
)

# real <kind> <match>: true unless the match is an obvious placeholder or code.
placeholder='your|example|placeholder|change_?me|dummy|sample|fake|redacted|replace|insert|here|xxxx|[*]{3}|[.]{3}|[<>]'
real() {
  local kind="$1" m="$2" v quoted='[:=][[:space:]]*\\?["'\'']' value='[:=][[:space:]]*\\?["'\'']?([^"'\''[:space:]\\]+)'
  shopt -s nocasematch
  [ "$kind" = 'a private key' ] && return 0
  v="$m"
  if [ "$kind" = 'a password or key' ]; then
    [[ $m =~ $value ]] || return 1
    v="${BASH_REMATCH[1]}"
    [[ $v =~ ^[\$\{%] ]] && return 1                     # ${VAR}, {{ x }}, %s
    [[ $m =~ $quoted ]] || [[ $v =~ [0-9] ]] || return 1  # token = someVariableName
  fi
  [ "$kind" = 'an OpenAI-style API key' ] && ! [[ $v =~ [0-9] ]] && return 1
  [[ $v =~ $placeholder ]] && return 1
  return 0
}

# found <text>: print the kind of the first secret in the text, or nothing.
found() {
  local entry kind re flag m
  for entry in "${kinds[@]}"; do
    kind="${entry%%|*}"; re="${entry#*|}"; flag=-oE
    [ "$kind" = 'a password or key' ] && flag=-oiE
    while IFS= read -r m; do
      [ -n "$m" ] && real "$kind" "$m" && { printf '%s' "$kind"; return; }
    done <<< "$(printf '%s\n' "$1" | grep $flag -- "$re")"
  done
}

block() {
  echo "BLOCKED: $1 holds what looks like $2. Put the real value in .env (git never saves it) and have the code read it at run time, for example process.env.NAME_OF_KEY. Where an example is needed, write a placeholder such as your-key-here. If this secret was ever shared, pasted or pushed anywhere, get a fresh one from the service and cancel the old one." >&2
  exit 2
}

# is_env <path>: true for .env and .env.<something>, except .env.example.
is_env() {
  local name="${1##*/}"
  [ "$name" != ".env.example" ] && [[ $name =~ ^\.env(\..+)?$ ]]
}

case "$1" in
  edit)
    file="$(field "$input" tool_input.file_path)"
    # With neither jq nor python3, field returns the raw JSON; pull the path out of it.
    if [[ $file == \{* ]]; then
      [[ $input =~ \"file_path\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]] && file="${BASH_REMATCH[1]}" || file=""
    fi
    is_env "$file" && exit 0
    [[ $file == */.claude/hooks/* || $file == .claude/hooks/* ]] && exit 0
    text="$(field "$input" tool_input.content)
$(field "$input" tool_input.new_string)
$(field "$input" tool_input.new_source)"
    kind="$(found "$text")"
    [ -n "$kind" ] && block "${file:-this file}" "$kind"
    ;;
  bash)
    cmd="$(field "$input" tool_input.command)"
    [ -z "$cmd" ] && exit 0

    # git add -f of a .env file: .gitignore keeps these out on purpose.
    while IFS= read -r s; do
      [ -z "$s" ] && continue
      printf '%s' "$s" | grep -qE -- '[[:space:]](-[a-zA-Z]*f[a-zA-Z]*|--force)([[:space:]"\\]|$)' || continue
      if printf '%s' "$s" | grep -oE -- '(^|[[:space:]/"])\.env(\.[A-Za-z0-9_-]+)?([[:space:]"\\]|$)' | grep -vqE '\.env\.example'; then
        echo "BLOCKED: git add -f would save a .env file into git, and .env holds the real passwords and keys. Leave .env out of git; put placeholders in .env.example for others to copy." >&2
        exit 2
      fi
    done <<< "$(printf '%s' "$cmd" | grep -oE 'git([[:space:]]+-C[[:space:]]+[^[:space:];&|]+)*[[:space:]]+add[^;&|]*')"

    printf '%s' "$cmd" | grep -qE 'git([[:space:]]+-C[[:space:]]+[^[:space:];&|]+)*[[:space:]]+commit([[:space:]"\\]|$)' || exit 0
    root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)" || exit 0
    g() { git -C "$root" -c core.quotePath=false "$@"; }
    skip=(-- . ':(exclude).claude/hooks')
    base=HEAD
    g rev-parse --verify -q HEAD >/dev/null || base="$(g hash-object -t tree /dev/null)"  # first commit: compare to nothing

    # Changed files: only the lines being added are checked.
    while IFS= read -r f; do
      [ -z "$f" ] && continue
      kind="$(found "$(g diff "$base" -- "$f" | grep -E '^\+' | grep -vE '^\+\+\+ ')")"
      [ -n "$kind" ] && block "$f" "$kind"
    done <<< "$(g diff "$base" --name-only --diff-filter=d "${skip[@]}")"

    # New files git does not ignore. Binary files (images, fonts) are skipped.
    while IFS= read -r f; do
      [ -z "$f" ] || [ ! -f "$root/$f" ] && continue
      grep -Iq '' "$root/$f" 2>/dev/null || continue
      kind="$(found "$(cat "$root/$f")")"
      [ -n "$kind" ] && block "$f" "$kind"
    done <<< "$(g ls-files --others --exclude-standard "${skip[@]}")"
    ;;
esac
exit 0
