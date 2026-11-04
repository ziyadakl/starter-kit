#!/usr/bin/env bash
# On a pull request, fail when the project lint file's warning cap is higher than on the base branch, so a cap raised
# through the shell (which the config guard cannot see) still turns CI red and leaves the merge to the owner.
#
# A project file that is new in the pull request (first adoption) passes: the base has no cap to compare with.
# A project file or cap removed in the pull request fails: no cap is a cap raised to infinity.
# Outside a pull request (GITHUB_BASE_REF is empty: a local run, a push to main) the step skips.
# The base branch is fetched here, so the default shallow checkout of check.yml is enough.
# shellcheck disable=SC2154  # LINT_* come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

echo "-- cap raise (the warning cap against the base branch)"

base=${GITHUB_BASE_REF:-}
if [ -z "$base" ]; then
  echo "cap raise: skipped, not a pull request (GITHUB_BASE_REF is empty)"
  exit 0
fi

if ! git fetch --quiet --depth=1 origin "$base" 2>&1; then
  echo "cap raise: could not fetch the base branch '$base' from origin, so the cap cannot be compared" >&2
  exit 1
fi

base_file=$(mktemp)
trap 'rm -f "$base_file"' EXIT
if ! git show "FETCH_HEAD:$LINT_PROJECT_FILE" >"$base_file" 2>/dev/null; then
  echo "cap raise: $LINT_PROJECT_FILE is new in this pull request (no cap on '$base' to compare with): ok"
  exit 0
fi

# Prints the cap of a project file: a number, or "none".
read_cap() {
  node -e '
    let o = {};
    try { o = JSON.parse(require("fs").readFileSync(process.argv[1], "utf8")); } catch (e) { process.exit(2); }
    const c = o.options && o.options[process.env.LINT_CAP_KEY];
    console.log(Number.isInteger(c) ? c : "none");
  ' "$1"
}

base_cap=$(read_cap "$base_file") || { echo "cap raise: $LINT_PROJECT_FILE on '$base' is not readable JSON" >&2; exit 1; }
if [ "$base_cap" = none ]; then
  echo "cap raise: '$base' sets no $LINT_CAP_KEY: nothing to compare with, ok"
  exit 0
fi

head_cap=none
if [ -f "$LINT_PROJECT_FILE" ]; then
  head_cap=$(read_cap "$LINT_PROJECT_FILE") || { echo "cap raise: $LINT_PROJECT_FILE is not readable JSON" >&2; exit 1; }
fi
if [ "$head_cap" = none ] || [ "$head_cap" -gt "$base_cap" ]; then
  echo "cap raise: $LINT_CAP_KEY in $LINT_PROJECT_FILE is ${head_cap/none/missing} here but $base_cap on '$base': the cap only goes down. Only the owner raises it (by merging this pull request by hand)" >&2
  exit 1
fi
echo "cap raise: $LINT_CAP_KEY is $head_cap here, $base_cap on '$base': ok"
