#!/usr/bin/env bash
# TypeScript checks for a single-package project: typecheck (tsc), lint (oxlint, type-aware), format (oxfmt), tests.
#
# Tools: the project's own copy in node_modules/.bin when it has one; otherwise the version pinned below, run through
# the package manager's one-off runner (npx -y, pnpm dlx, ...). The project's package.json needs no tool entries, and a
# kit update changes the versions in this one place. A project with eslint or prettier keeps them; this check does not run them.
#
# Every step runs even when an earlier one fails, so one round shows every problem. Two canaries guard against a
# check that passes because it checked nothing: the typecheck and the linter each run on a planted mistake and must report
# it, and each run must have looked at more than zero files.
set -uo pipefail

TYPESCRIPT_VERSION=7.0.2
OXLINT_VERSION=1.90.0
OXLINT_TSGOLINT_VERSION=7.0.2005
OXFMT_VERSION=0.75.0
FORMAT_GLOB='**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}'

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-pm.sh"

if [ ! -f package.json ]; then
  echo "no package.json at the repo root: the typescript layer checks single-package projects" >&2
  exit 1
fi
pm=$(ts_detect_pm) || exit 1
if [ ! -d node_modules ]; then
  ts_install_cmd "$pm"
  echo "dependencies are not installed: run '${TS_INSTALL_CMD[*]}' first" >&2
  exit 1
fi

# tool <binary> "<pinned packages>" args...: the project's installed binary, else the pinned one-off.
tool() {
  local bin=$1 pkgs=$2
  shift 2
  if [ -x "node_modules/.bin/$bin" ]; then
    "node_modules/.bin/$bin" "$@"
  else
    ts_oneoff "$pm" "$pkgs" "$bin" "$@"
  fi
}

# oxlint's type-aware rules need oxlint-tsgolint next to it; without that in the project, use the pinned pair.
lint_tool() {
  if [ -x node_modules/.bin/oxlint ] && [ -d node_modules/oxlint-tsgolint ]; then
    node_modules/.bin/oxlint "$@"
  else
    ts_oneoff "$pm" "oxlint@$OXLINT_VERSION oxlint-tsgolint@$OXLINT_TSGOLINT_VERSION" oxlint "$@"
  fi
}

canary_dir=""
cleanup() { [ -n "$canary_dir" ] && rm -rf "$canary_dir"; }
trap cleanup EXIT

new_canary_dir() {
  canary_dir=$(mktemp -d)
  cat >"$canary_dir/tsconfig.json" <<'JSON'
{
  "compilerOptions": {
    "strict": true,
    "noEmit": true,
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "types": []
  },
  "include": ["canary.ts"]
}
JSON
}

# files_checked <output>: the "on N files" count from oxlint's or oxfmt's summary line; empty when there is none.
files_checked() {
  printf '%s\n' "$1" | sed -nE 's/.* on ([0-9]+) files?( .*)?$/\1/p' | tail -n 1
}

typecheck() {
  if [ ! -f tsconfig.json ]; then
    echo "no tsconfig.json at the repo root: add one so tsc has something to check" >&2
    return 1
  fi
  local out status

  # Canary: a file with one known type error must be reported as TS2322.
  new_canary_dir
  echo 'const x: number = "text";' >"$canary_dir/canary.ts"
  out=$(tool tsc "typescript@$TYPESCRIPT_VERSION" --noEmit --incremental false --pretty false -p "$canary_dir/tsconfig.json" 2>&1)
  status=$?
  cleanup
  canary_dir=""
  if [ "$status" -eq 0 ] || ! printf '%s\n' "$out" | grep -q 'error TS2322'; then
    printf '%s\n' "$out"
    echo "CANARY FAILED: typecheck: tsc did not report the planted type error (TS2322), so a clean result from it means nothing" >&2
    return 1
  fi

  # The project's tsconfig must reach at least one source file ("files": [] with references checks nothing).
  local listed count
  listed=$(tool tsc "typescript@$TYPESCRIPT_VERSION" --noEmit --listFilesOnly 2>/dev/null)
  count=$(printf '%s\n' "$listed" | grep -Evc 'node_modules|^bundled:|/lib/lib\.|\.d\.[cm]?ts$|^$')
  if [ "$count" -eq 0 ]; then
    echo "typecheck looked at zero source files: check the include/files/exclude entries of tsconfig.json (a tsconfig that only lists references checks nothing)" >&2
    return 1
  fi

  tool tsc "typescript@$TYPESCRIPT_VERSION" --noEmit --pretty false
}

lint() {
  local -a cfg=()
  [ -f .oxlintrc.json ] && cfg=(-c .oxlintrc.json)
  local out status missing rule

  # Canary: a floating promise (type-aware) and a debugger statement must both be reported under the project's config.
  new_canary_dir
  printf 'async function f() {\n  return 1;\n}\nf();\ndebugger;\n' >"$canary_dir/canary.ts"
  out=$(lint_tool --type-aware -f default ${cfg[@]+"${cfg[@]}"} "$canary_dir" 2>&1)
  status=$?
  cleanup
  canary_dir=""
  missing=""
  for rule in no-debugger no-floating-promises; do
    printf '%s\n' "$out" | grep -q "$rule" || missing="$missing $rule"
  done
  if [ "$status" -eq 0 ] || [ -n "$missing" ]; then
    printf '%s\n' "$out"
    echo "CANARY FAILED: lint: oxlint did not report${missing:- the planted mistakes as errors}: its rules are off in the config, or type information is not reaching it" >&2
    return 1
  fi

  out=$(lint_tool --type-aware -f default ${cfg[@]+"${cfg[@]}"} . 2>&1)
  status=$?
  printf '%s\n' "$out"
  local count
  count=$(files_checked "$out")
  if [ "$count" = 0 ]; then
    echo "lint checked zero files: check ignorePatterns in .oxlintrc.json" >&2
    return 1
  fi
  if [ "$status" -eq 0 ] && [ -z "$count" ]; then
    echo "lint: could not read how many files oxlint checked from its output, so the result cannot be trusted" >&2
    return 1
  fi
  return "$status"
}

format() {
  local out status count
  out=$(tool oxfmt "oxfmt@$OXFMT_VERSION" --check "$FORMAT_GLOB" 2>&1)
  status=$?
  printf '%s\n' "$out"
  count=$(files_checked "$out")
  if [ "$count" = 0 ]; then
    echo "format checked zero files: check ignorePatterns in .oxfmtrc.json" >&2
    return 1
  fi
  return "$status"
}

# The project's own test script wins (it carries its flags); otherwise vitest when the project has it configured.
tests() {
  export CI=1
  local script
  script=$(node -e 'const s = (require("./package.json").scripts || {}).test; if (s) process.stdout.write(s)' 2>/dev/null)
  case "$script" in
    "" | *"no test specified"*) ;;
    *)
      "$pm" run test
      return $?
      ;;
  esac
  local configured=0 f
  for f in vitest.config.* vitest.workspace.*; do
    [ -e "$f" ] && configured=1
  done
  if node -e 'const p = require("./package.json"); process.exit({ ...p.dependencies, ...p.devDependencies }.vitest ? 0 : 1)' 2>/dev/null; then
    configured=1
  fi
  if [ "$configured" -eq 0 ]; then
    echo "no tests configured: skipping (no test script and no vitest)"
    return 0
  fi
  if [ ! -x node_modules/.bin/vitest ]; then
    echo "vitest is configured but not installed: run the dependency install" >&2
    return 1
  fi
  node_modules/.bin/vitest run
}

failures=()
run_step() {
  local name=$1 label=$2
  echo "-- $name ($label)"
  if ! "$name"; then
    failures+=("$name")
  fi
}

run_step typecheck "tsc"
run_step lint "oxlint"
run_step format "oxfmt"
run_step tests "$pm"

if [ "${#failures[@]}" -gt 0 ]; then
  summary=""
  for name in "${failures[@]}"; do
    summary="${summary:+$summary, }$name"
  done
  echo "-- failed: $summary" >&2
  exit 1
fi
