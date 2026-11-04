#!/usr/bin/env bash
# Lint with oxlint, type-aware (needs oxlint-tsgolint next to it), under the project's .oxlintrc.json.
#
# Canary: oxlint first runs on a planted floating promise (type-aware) and a debugger statement and must report both
# as errors under the project's config, so a rule switched off, a rule left at warning, or type information not
# reaching the linter fails here instead of passing silently. The real run must also have looked at more than zero files.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

oxlint_pkgs=("oxlint@$OXLINT_VERSION" "oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION")
config=()
[ -f .oxlintrc.json ] && config=(-c .oxlintrc.json)

echo "-- lint (oxlint $OXLINT_VERSION, type-aware)"

new_canary_dir
printf 'async function f() {\n  return 1;\n}\nf();\ndebugger;\n' >"$canary_dir/canary.ts"
# The unix format ends every finding with [Error/<rule>] or [Warning/<rule>].
out=$(tool oxlint "${oxlint_pkgs[@]}" -- --type-aware -f unix ${config[@]+"${config[@]}"} "$canary_dir" 2>&1)
status=$?
missing=""
for rule in no-debugger no-floating-promises; do
  printf '%s\n' "$out" | grep -Eq "\[Error/[a-z-]+\($rule\)\]" || missing="$missing $rule"
done
if [ "$status" = 0 ] || [ -n "$missing" ]; then
  printf '%s\n' "$out"
  echo "CANARY FAILED: lint: oxlint did not report${missing:- the planted mistakes} as errors: the rules are off or only warn in the config, or type information is not reaching it" >&2
  exit 1
fi

out=$(tool oxlint "${oxlint_pkgs[@]}" -- --type-aware -f default ${config[@]+"${config[@]}"} . 2>&1)
status=$?
printf '%s\n' "$out"
require_files lint "$out" "$status" || exit 1
exit "$status"
