#!/usr/bin/env bash
# Shared by the typescript layer's scripts/check.d/*-ts-*.sh steps: source it, do not run it.
# It holds the pinned tool versions (the one place to bump them; Renovate reads the comments above each pin), the checks
# every step starts with (a package.json, a lockfile, installed dependencies), and the helpers the steps share.
#
# Tools: the project's own copy in node_modules/.bin when every pinned package of that tool is installed there;
# otherwise the version pinned below, run through the package manager's one-off runner (npx -y, pnpm dlx, ...). The
# project's package.json needs no tool entries. A project with eslint or prettier keeps them; the check does not run them.
# The typecheck is the exception: it always runs TypeScript 7 (see scripts/check.d/20-ts-typecheck.sh).
set -uo pipefail

# renovate: datasource=npm depName=typescript
TYPESCRIPT_VERSION=7.0.2
# renovate: datasource=npm depName=oxlint
OXLINT_VERSION=1.90.0
# renovate: datasource=npm depName=oxlint-tsgolint
OXLINT_TSGOLINT_VERSION=7.0.2005
# renovate: datasource=npm depName=oxfmt
OXFMT_VERSION=0.75.0
FORMAT_GLOB='**/*.{js,jsx,mjs,cjs,ts,tsx,mts,cts}'

# The lint files and rule groups (see .oxlint-kit.json). The lower-cap command and the CI cap check read these too.
# Locked rules are always errors. Lowerable rules (a trailing * is a prefix) may be set to warn or off in the project
# file; no-var may also be turned off there, in an override for untyped browser files. Every other rule stays an error.
LINT_KIT_FILE=.oxlint-kit.json
LINT_PROJECT_FILE=.oxlint-project.json
LINT_CAP_KEY=maxWarnings
LINT_CAP_COMMAND="bash scripts/lint-cap.sh"
LINT_LOCKED_RULES="typescript/no-floating-promises typescript/no-misused-promises typescript/await-thenable no-debugger"
LINT_LOWERABLE_RULES="typescript/no-unsafe-* typescript/no-non-null-assertion typescript/prefer-nullish-coalescing typescript/prefer-optional-chain typescript/require-await"
export LINT_KIT_FILE LINT_PROJECT_FILE LINT_CAP_KEY LINT_CAP_COMMAND LINT_LOCKED_RULES LINT_LOWERABLE_RULES
export TYPESCRIPT_VERSION OXLINT_VERSION OXLINT_TSGOLINT_VERSION OXFMT_VERSION FORMAT_GLOB

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ts-pm.sh"

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

# tool <binary> <pkg@version>... -- [args...]
# Runs the project's installed binary when every listed package is installed under node_modules; otherwise the
# pinned packages through the one-off runner. A project that has oxlint but not oxlint-tsgolint gets the pinned pair.
tool() {
  local bin=$1 p
  shift
  local -a pkgs=()
  while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do
    pkgs+=("$1")
    shift
  done
  [ "${1:-}" = "--" ] && shift
  local all_installed=1
  for p in "${pkgs[@]}"; do
    [ -d "node_modules/${p%@*}" ] || all_installed=0
  done
  if [ "$all_installed" = 1 ] && [ -x "node_modules/.bin/$bin" ]; then
    "node_modules/.bin/$bin" "$@"
  else
    ts_oneoff "$pm" "$bin" "${pkgs[@]}" -- "$@"
  fi
}

# A scratch folder for a canary: a file with a known mistake and a tsconfig that reaches it. Removed on exit.
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

# require_files <step> "<tool output>" <tool exit status>: fails when the tool looked at zero files, or when the
# count cannot be read from its output (a run that passes without saying what it checked cannot be trusted).
# When the tool itself failed, an unreadable count adds nothing: the step already fails on the tool's status.
require_files() {
  local step=$1 out=$2 status=$3 count config
  count=$(files_checked "$out")
  case "$step" in
    lint) config=.oxlintrc.json ;;
    format) config=.oxfmtrc.json ;;
    *) config="the tool's config" ;;
  esac
  if [ "$count" = 0 ]; then
    echo "$step checked zero files: check ignorePatterns in $config" >&2
    return 1
  fi
  if [ -z "$count" ] && [ "$status" = 0 ]; then
    echo "$step: could not read how many files the tool checked from its output, so the result cannot be trusted" >&2
    return 1
  fi
  return 0
}
