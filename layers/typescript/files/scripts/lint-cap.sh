#!/usr/bin/env bash
# The lower-cap command: measures the project's lint warnings and writes (or lowers) the project lint file, .oxlint-project.json.
#   bash scripts/lint-cap.sh
# What it does, in order:
#   1. Files outside the tsconfig that have findings (untyped browser scripts, say) get an override that turns off the
#      typescript/no-unsafe-* family and no-var there. The promise rules stay on.
#   2. Each allowed rule (LINT_LOWERABLE_RULES in ts-common.sh) that still fires is set to "warn". Locked rules and every
#      other rule stay errors.
#   3. options.maxWarnings is set to the warning count measured now.
# On a re-run (the project file already has a cap) it only lowers the cap: it adds no rule at warn and no override, and
# refuses to raise the cap: if the count is above it, nothing is written. Only the owner raises the cap.
# The policy and the file edits are scripts/lint-policy.mjs.
# A project with no warnings and nothing to override gets no project file. Findings that stay errors are listed by rule:
# fix those, they are what the check still fails on.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ts-common.sh"

oxlint_pkgs=("oxlint@$OXLINT_VERSION" "oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION")

if [ ! -f .oxlintrc.json ] || [ ! -f "$LINT_KIT_FILE" ]; then
  echo "lint-cap: .oxlintrc.json and $LINT_KIT_FILE are not both here: apply the typescript layer first (kit.py apply --layer typescript)" >&2
  exit 1
fi
if [ ! -f tsconfig.json ]; then
  echo "lint-cap: no tsconfig.json at the repo root: the type-aware rules need it" >&2
  exit 1
fi

new_canary_dir
work=$canary_dir

# lint_json <project file to lint under> <out.json>: the type-aware findings as JSON, under the kit config plus that project file.
lint_json() {
  lint_policy root-config "$lint_root" "$1" || exit 1
  tool oxlint "${oxlint_pkgs[@]}" -- --type-aware -f json -c "$lint_root" . >"$2" 2>"$work/lint.err"
  if ! node -e 'JSON.parse(require("fs").readFileSync(process.argv[1], "utf8"))' "$2" 2>/dev/null; then
    cat "$2" "$work/lint.err" 2>/dev/null
    echo "lint-cap: oxlint did not produce a report" >&2
    exit 1
  fi
}

echo "-- lint-cap (oxlint $OXLINT_VERSION, type-aware)"
existing=-
[ -f "$LINT_PROJECT_FILE" ] && existing=$LINT_PROJECT_FILE
# The candidate project file and the root config copy sit in the repo root: oxlint reads an override's globs relative to
# the root config it was given, so a copy elsewhere would match the override's files against the wrong folder.
# Both are named by a relative path: oxlint matches the globs against the real path of the folder, which an absolute path
# through a symlink (/tmp on macOS) would miss.
cand=".oxlint-project.lint-cap-$$.json"
lint_root=".oxlintrc.lint-cap-$$.json"
track_scratch "$cand" "$lint_root"

tool oxlint "${oxlint_pkgs[@]}" -- -c "$LINT_KIT_FILE" --print-config >"$work/kit.json" 2>"$work/kit.err" || {
  cat "$work/kit.err"
  echo "lint-cap: could not read $LINT_KIT_FILE" >&2
  exit 1
}

lint_policy candidate "$existing" "$cand" || exit 1
lint_json "$cand" "$work/pass1.json"
ts_files >"$work/tsfiles.txt" 2>"$work/ts.err" || {
  cat "$work/ts.err"
  echo "lint-cap: tsc could not list the files the tsconfig reaches" >&2
  exit 1
}
lint_policy outside "$work/pass1.json" "$work/tsfiles.txt" "$work/kit.json" "$cand" "$existing" || exit 1
lint_json "$cand" "$work/pass2.json"
lint_policy final "$work/pass2.json" "$cand" "$existing" "$LINT_PROJECT_FILE"
