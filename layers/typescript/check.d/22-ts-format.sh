#!/usr/bin/env bash
# Format check with oxfmt on the JavaScript and TypeScript files (not JSON, Markdown or CSS), under .oxfmtrc.json.
# The run must have looked at more than zero files.
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

echo "-- format (oxfmt $OXFMT_VERSION)"

out=$(tool oxfmt "oxfmt@$OXFMT_VERSION" -- --check "$FORMAT_GLOB" 2>&1)
status=$?
printf '%s\n' "$out"
require_files format "$out" "$status" || exit 1
exit "$status"
