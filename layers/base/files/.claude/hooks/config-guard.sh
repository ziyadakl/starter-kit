#!/usr/bin/env bash
# Stops Claude from making a failing check pass by loosening the check: edits to lint, format and scan
# configuration are refused. The right fix for a failing check is in the code.
#
# Called from .claude/settings.json before Claude edits or writes a file (Edit, Write, MultiEdit, NotebookEdit).
# Which files are protected comes from the lists in .claude/protect.d/*.txt: the base layer ships base.txt, and every
# other layer ships its own (python.txt, shell.txt, ...), so a project is protected for exactly the layers it applied.
# A list holds one pattern per line; blank lines and lines starting with # are ignored.
#   pattern without a slash   matches a file of that name in any folder       (ruff.toml)
#   pattern with a slash      matches the path from the project root, * glob  (.claude/protect.d/*)
# Add your own lines in a new file, e.g. .claude/protect.d/local.txt.
#
# The owner can still change a protected file on purpose: edit it by hand outside Claude, or start Claude with
# ALLOW_CONFIG_EDIT=1 in its environment (the hook sees it; Claude's own shell commands cannot set it).
#
# Exit 2 blocks and shows the reason to Claude. Any error in this script lets the edit through.

# shellcheck source=/dev/null
source "$(dirname "$0")/lib.sh" 2>/dev/null || exit 0
[ "${ALLOW_CONFIG_EDIT:-}" = 1 ] && exit 0
dir="${CLAUDE_PROJECT_DIR:-.}"
input="$(cat)"

file="$(field "$input" tool_input.file_path)"
[ -z "$file" ] && file="$(field "$input" tool_input.notebook_path)"
# With neither jq nor python3, field returns the raw JSON; pull the path out of it.
if [[ $file == \{* ]]; then
  file=""
  [[ $input =~ \"(file|notebook)_path\"[[:space:]]*:[[:space:]]*\"([^\"]*)\" ]] && file="${BASH_REMATCH[2]}"
fi
[ -z "$file" ] && exit 0

rel="${file#"${dir%/}"/}"
name="${file##*/}"

for list in "$dir"/.claude/protect.d/*.txt; do
  [ -f "$list" ] || continue
  while IFS= read -r pattern || [ -n "$pattern" ]; do
    pattern="${pattern%%#*}"
    pattern="${pattern//[[:space:]]/}"
    [ -z "$pattern" ] && continue
    case "$pattern" in
      */*) target="$rel" ;;
      *) target="$name" ;;
    esac
    # The pattern is a glob on purpose.
    # shellcheck disable=SC2254
    case "$target" in
      $pattern)
        echo "BLOCKED: $rel is check configuration (listed in ${list#"${dir%/}"/}). Fix the code, don't weaken the config. If the owner really wants this change, they can edit the file by hand outside Claude, or restart Claude with ALLOW_CONFIG_EDIT=1." >&2
        exit 2
        ;;
    esac
  done < "$list"
done
exit 0
