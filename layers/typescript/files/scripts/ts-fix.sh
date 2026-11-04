#!/usr/bin/env bash
# The adoption fixer: the linter's fix mode, then the formatter in write mode, on the kit's pinned tools.
#   bash scripts/ts-fix.sh
# Run it once when a project adopts the typescript layer (README, "Adopting the kit in an existing project", step 2).
# It works before .oxlint-project.json exists: oxlint refuses .oxlintrc.json's extends entry for a missing file, so it
# then fixes under a copy of the root config without that entry, in the repo root (oxlint matches ignorePatterns against
# the folder of the config it was given). The fixer leaves code unformatted, so the formatter runs after it, until its
# check passes (oxfmt can need a second pass), at most three times.
# Findings the fixer cannot fix are left for the person adopting the kit; this command exits 0 after a clean run.
# Then run the typecheck and the tests: a fix can remove a type assertion the code needed.
# shellcheck disable=SC2154  # pm and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ts-common.sh"

oxlint_pkgs=("oxlint@$OXLINT_VERSION" "oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION")

if [ ! -f .oxlintrc.json ] || [ ! -f "$LINT_KIT_FILE" ]; then
  echo "ts-fix: .oxlintrc.json and $LINT_KIT_FILE are not both here: apply the typescript layer first (kit.py apply --layer typescript)" >&2
  exit 1
fi

config_file=.oxlintrc.json
if [ ! -f "$LINT_PROJECT_FILE" ]; then
  config_file=".oxlintrc.fix-$$.json"
  track_scratch "$config_file"
  lint_policy root-config "$config_file" || exit 1
fi

echo "-- fix (oxlint $OXLINT_VERSION, type-aware)"
# oxlint exits non-zero while findings remain; only a run that produced no report is a failure here.
out=$(tool oxlint "${oxlint_pkgs[@]}" -- --type-aware --fix -c "$config_file" . 2>&1)
status=$?
printf '%s\n' "$out" | tail -3
if [ "$status" -ne 0 ] && ! printf '%s\n' "$out" | grep -q 'Found [0-9]* warning'; then
  echo "ts-fix: oxlint did not run" >&2
  exit 1
fi

echo "-- format (oxfmt $OXFMT_VERSION)"
for pass in 1 2 3; do
  tool oxfmt "oxfmt@$OXFMT_VERSION" -- --write "$FORMAT_GLOB" >/dev/null 2>&1
  if tool oxfmt "oxfmt@$OXFMT_VERSION" -- --check "$FORMAT_GLOB" >/dev/null 2>&1; then
    echo "formatted (pass $pass)"
    exit 0
  fi
done
tool oxfmt "oxfmt@$OXFMT_VERSION" -- --check "$FORMAT_GLOB" 2>&1 | tail -5
echo "ts-fix: the formatter still reports differences after three passes" >&2
exit 1
