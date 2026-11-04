#!/usr/bin/env bash
# Typecheck with TypeScript 7 (the native compiler), always. The compiler is, in this order:
#   1. the project's node_modules/.bin/tsc, when `tsc --version` says 7 or newer;
#   2. the project's node_modules/typescript7 (an alias for TypeScript 7 next to an older `typescript`), same test;
#   3. otherwise the version pinned in scripts/ts-common.sh, run through the package manager's one-off runner.
# A project's older tsc (TypeScript 5 or 6) is never used here; the first line printed names the compiler that ran.
#
# A tsconfig.json with "references" (the Vite layout: "files": [] and one tsconfig per part) is checked with
# `tsc -b --noEmit`, which builds every referenced project. Build mode may write .tsbuildinfo files where the
# referenced tsconfigs say (Vite puts them under node_modules/.tmp).
#
# Canary: tsc first runs on a planted type error and must report it, so a result that checked nothing cannot pass.
# shellcheck disable=SC2154  # pm, canary_dir and the version pins come from ts-common.sh
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/../ts-common.sh"

# Sets TSC_CMD to the project's own TypeScript 7+ compiler, or to nothing (the pinned one-off), and TSC_DESC to a line naming it.
resolve_tsc() {
  local ver="" note=""
  TSC_CMD=()
  if [ -x node_modules/.bin/tsc ]; then
    ver=$(node_modules/.bin/tsc --version 2>/dev/null | sed -nE 's/^Version ([0-9]+\.[0-9]+\.[0-9]+).*/\1/p')
    if [ "${ver%%.*}" -ge 7 ] 2>/dev/null; then
      TSC_CMD=(node_modules/.bin/tsc)
      TSC_DESC="tsc $ver (the project's own, node_modules/.bin/tsc)"
      return
    fi
    [ -n "$ver" ] && note="; the project's tsc $ver is older than 7 and is not used"
  fi
  if [ -x node_modules/typescript7/bin/tsc ]; then
    ver=$(node -p 'require("./node_modules/typescript7/package.json").version' 2>/dev/null)
    if [ "${ver%%.*}" -ge 7 ] 2>/dev/null; then
      TSC_CMD=(node_modules/typescript7/bin/tsc)
      TSC_DESC="tsc $ver (the project's own, node_modules/typescript7)"
      return
    fi
  fi
  TSC_DESC="tsc $TYPESCRIPT_VERSION (pinned by the kit, run one-off$note)"
}

tsc() {
  if [ "${#TSC_CMD[@]}" -gt 0 ]; then
    "${TSC_CMD[@]}" "$@"
  else
    ts_oneoff "$pm" tsc "typescript@$TYPESCRIPT_VERSION" -- "$@"
  fi
}

resolve_tsc
echo "-- typecheck: $TSC_DESC"

if [ ! -f tsconfig.json ]; then
  echo "no tsconfig.json at the repo root: add one so tsc has something to check" >&2
  exit 1
fi

# Canary: a file with one known type error must be reported as TS2322.
new_canary_dir
echo 'const x: number = "text";' >"$canary_dir/canary.ts"
out=$(tsc --noEmit --incremental false --pretty false -p "$canary_dir/tsconfig.json" 2>&1)
status=$?
if [ "$status" = 0 ] || ! printf '%s\n' "$out" | grep -q 'error TS2322'; then
  printf '%s\n' "$out"
  echo "CANARY FAILED: typecheck: tsc did not report the planted type error (TS2322), so a clean result from it means nothing" >&2
  exit 1
fi

# The project's tsconfig must load. tsc prints its own error for a broken one; show that, not a count of files.
listed=$(tsc --noEmit --listFilesOnly 2>&1)
status=$?
if [ "$status" != 0 ]; then
  printf '%s\n' "$listed" | grep -E 'error TS' || printf '%s\n' "$listed"
  echo "typecheck: tsc could not load tsconfig.json, see the error above" >&2
  exit 1
fi

# A tsconfig with references is checked in build mode.
if tsc --showConfig 2>&1 | grep -q '"references"'; then
  echo "-- tsconfig.json has references: building them with tsc -b --noEmit"
  tsc -b --noEmit --pretty false
  exit $?
fi

# Otherwise the tsconfig must reach at least one source file ("files": [] with nothing else checks nothing).
count=$(printf '%s\n' "$listed" | grep -Evc 'node_modules|^bundled:|/lib/lib\.|\.d\.[cm]?ts$|^$')
if [ "$count" = 0 ]; then
  echo "typecheck looked at zero source files: check the include/files/exclude entries of tsconfig.json" >&2
  exit 1
fi

tsc --noEmit --pretty false
