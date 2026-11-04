#!/usr/bin/env bash
# Lint with oxlint, type-aware (needs oxlint-tsgolint next to it), under .oxlintrc.json: the kit's rules
# (.oxlint-kit.json) then the project's optional .oxlint-project.json.
#
# Policy (scripts/lint-policy.mjs, data in ts-common.sh): the merged config must keep the locked rules as errors and may lower
# only the allowed rules, to warn; a project file that lowers anything must set options.maxWarnings, which caps the warnings:
# over the cap fails, under it prints the lower-cap command.
#
# Canary: oxlint first runs on a planted floating promise (type-aware) and a debugger statement and must report both
# as errors under the merged config, so a rule switched off, a rule left at warning, or type information not
# reaching the linter fails here instead of passing silently. The real run must also have looked at more than zero files.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

oxlint_pkgs=("oxlint@$OXLINT_VERSION" "oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION")
config=()
cap=""

echo "-- lint (oxlint $OXLINT_VERSION, type-aware)"

new_canary_dir
if [ -f .oxlintrc.json ]; then
  config_file=.oxlintrc.json
  if [ ! -f "$LINT_PROJECT_FILE" ]; then
    # oxlint refuses an extends entry that points at a missing file: lint under a copy of the root config without it.
    # The copy sits in the repo root and is named by a relative path, because oxlint matches ignorePatterns (and override
    # globs) against the folder of the config it was given: a copy elsewhere would lint dist/ and the config files.
    config_file=".oxlintrc.check-$$.json"
    track_scratch "$config_file"
    lint_policy root-config "$config_file" || exit 1
  fi
  config=(-c "$config_file")

  if ! { tool oxlint "${oxlint_pkgs[@]}" -- -c "$LINT_KIT_FILE" --print-config >"$canary_dir/kit.json" 2>&1 &&
    tool oxlint "${oxlint_pkgs[@]}" -- -c "$config_file" --print-config >"$canary_dir/merged.json" 2>&1; }; then
    cat "$canary_dir/kit.json" "$canary_dir/merged.json" 2>/dev/null
    echo "lint: could not read the merged oxlint config" >&2
    exit 1
  fi
  # An override that turns rules off is only allowed for files outside the tsconfig, so list the tsconfig's files then.
  policy_args=()
  if [ -f "$LINT_PROJECT_FILE" ] && grep -q '"overrides"' "$LINT_PROJECT_FILE"; then
    if ! ts_files >"$canary_dir/tsfiles.txt" 2>"$canary_dir/ts.err"; then
      cat "$canary_dir/ts.err"
      echo "lint: tsc could not list the files the tsconfig reaches" >&2
      exit 1
    fi
    policy_args=(--ts-files "$canary_dir/tsfiles.txt")
  fi
  # Prints the cap (nothing when there is none); violations go to stderr, one line each, with exit status 1.
  cap=$(lint_policy check "$canary_dir/kit.json" "$canary_dir/merged.json" ${policy_args[@]+"${policy_args[@]}"}) || exit 1
fi

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

warnings=$(printf '%s\n' "$out" | sed -nE 's/^Found ([0-9]+) warnings? and [0-9]+ errors?\..*/\1/p' | tail -n 1)
if [ -n "$cap" ] && [ -n "$warnings" ]; then
  if printf '%s\n' "$out" | grep -q 'Exceeded maximum number of warnings'; then
    echo "lint: $warnings warnings, over the cap of $cap ($LINT_CAP_KEY in $LINT_PROJECT_FILE): fix the new warnings. Only the owner raises the cap" >&2
  elif [ "$status" = 0 ] && [ "$warnings" -lt "$cap" ]; then
    echo "lint: $warnings warnings, below the cap of $cap: lower the cap with: $LINT_CAP_COMMAND"
  fi
fi
exit "$status"
